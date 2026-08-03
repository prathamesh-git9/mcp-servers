"""Unified-diff parsing, deterministic review rules, and commit synthesis."""

import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from mcp_server_common import Failure, FailureCode, ServiceError
from mcp_server_common.redaction import redact

from coding_workflows.models import (
    CommitProposal,
    DiffFileSummary,
    DiffFinding,
    DiffReview,
    FindingCategory,
    Severity,
)

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[\"'][^\"']{6,}"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})\b"
)


@dataclass(slots=True)
class ChangedLine:
    file: str
    line: int | None
    side: str
    content: str


@dataclass(slots=True)
class ParsedFile:
    path: str
    old_path: str | None = None
    new_path: str | None = None
    additions: int = 0
    deletions: int = 0
    old_is_null: bool = False
    new_is_null: bool = False
    renamed: bool = False

    def summary(self) -> DiffFileSummary:
        if self.renamed:
            change_type = "renamed"
        elif self.old_is_null:
            change_type = "added"
        elif self.new_is_null:
            change_type = "deleted"
        else:
            change_type = "modified"
        return DiffFileSummary(
            file=self.path,
            change_type=change_type,
            additions=self.additions,
            deletions=self.deletions,
        )


@dataclass(slots=True)
class ParsedDiff:
    files: list[ParsedFile] = field(default_factory=list)
    lines: list[ChangedLine] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReviewRule:
    rule_id: str
    pattern: re.Pattern[str]
    severity: Severity
    category: FindingCategory
    rationale: str
    sides: frozenset[str] = frozenset({"new"})


_RULES = (
    ReviewRule(
        "secret-in-diff",
        _SECRET,
        Severity.CRITICAL,
        FindingCategory.SECURITY,
        "A credential-shaped value is added to source control; revoke it and use "
        "a secret store.",
    ),
    ReviewRule(
        "shell-execution",
        re.compile(r"\b(?:os\.system|eval|exec)\s*\(|shell\s*=\s*True"),
        Severity.HIGH,
        FindingCategory.SECURITY,
        "Dynamic shell or code execution can turn repository-controlled text into "
        "commands.",
    ),
    ReviewRule(
        "tls-verification-disabled",
        re.compile(r"\bverify\s*=\s*False\b|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0"),
        Severity.HIGH,
        FindingCategory.SECURITY,
        "Disabling certificate verification permits machine-in-the-middle responses.",
    ),
    ReviewRule(
        "world-writable-permission",
        re.compile(r"\b(?:0o777|chmod\s+777)\b"),
        Severity.HIGH,
        FindingCategory.SECURITY,
        "World-writable permissions broaden the modification boundary unnecessarily.",
    ),
    ReviewRule(
        "bare-except",
        re.compile(r"^\s*except\s*:"),
        Severity.HIGH,
        FindingCategory.RELIABILITY,
        "A bare exception handler also catches cancellation and process-control "
        "exceptions.",
    ),
    ReviewRule(
        "broad-except",
        re.compile(r"^\s*except\s+Exception(?:\s+as\s+\w+)?\s*:"),
        Severity.MEDIUM,
        FindingCategory.RELIABILITY,
        "A broad handler can hide unrelated defects unless it converts them at a "
        "boundary.",
    ),
    ReviewRule(
        "network-without-timeout",
        re.compile(
            r"\b(?:requests|httpx)\.(?:get|post|put|patch|delete|request)\s*\((?![^\n]*timeout\s*=)"
        ),
        Severity.MEDIUM,
        FindingCategory.RELIABILITY,
        "A network call without an explicit timeout can block the workflow indefinitely.",
    ),
    ReviewRule(
        "disabled-test",
        re.compile(
            r"(?:pytest\.mark\.skip|pytest\.skip\(|describe\.skip|it\.skip|\bxit\(|@Disabled|#\[ignore\])"
        ),
        Severity.HIGH,
        FindingCategory.TESTING,
        "The change disables test execution and can turn a regression into a false "
        "green build.",
    ),
    ReviewRule(
        "debug-output",
        re.compile(r"^\s*(?:print|console\.log)\s*\("),
        Severity.LOW,
        FindingCategory.MAINTAINABILITY,
        "Unstructured debug output can leak data or make automation logs noisy.",
    ),
    ReviewRule(
        "unfinished-marker",
        re.compile(r"\b(?:TODO|FIXME|HACK)\b"),
        Severity.INFO,
        FindingCategory.MAINTAINABILITY,
        "The added marker records unfinished work that should be resolved or tracked "
        "explicitly.",
    ),
    ReviewRule(
        "removed-assertion",
        re.compile(r"\bassert\b|\bexpect\s*\("),
        Severity.MEDIUM,
        FindingCategory.TESTING,
        "A removed assertion weakens regression coverage and should be justified by "
        "replacement coverage.",
        frozenset({"old"}),
    ),
)


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    parsed = ParsedDiff()
    current: ParsedFile | None = None
    old_line: int | None = None
    new_line: int | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            parts = _split_diff_header(raw_line)
            old_path = _clean_path(parts[0]) if parts else None
            new_path = _clean_path(parts[1]) if len(parts) > 1 else old_path
            current = ParsedFile(
                path=new_path or old_path or "unknown",
                old_path=old_path,
                new_path=new_path,
            )
            parsed.files.append(current)
            old_line = new_line = None
            continue
        if raw_line.startswith("rename from ") and current:
            current.old_path = raw_line.removeprefix("rename from ").strip()
            current.renamed = True
            continue
        if raw_line.startswith("rename to ") and current:
            current.new_path = raw_line.removeprefix("rename to ").strip()
            current.path = current.new_path
            current.renamed = True
            continue
        if raw_line.startswith("--- "):
            if current is None:
                current = ParsedFile(path="unknown")
                parsed.files.append(current)
            value = raw_line[4:].strip().split("\t", 1)[0]
            current.old_is_null = value == "/dev/null"
            if not current.old_is_null:
                current.old_path = _clean_path(value)
            continue
        if raw_line.startswith("+++ "):
            if current is None:
                current = ParsedFile(path="unknown")
                parsed.files.append(current)
            value = raw_line[4:].strip().split("\t", 1)[0]
            current.new_is_null = value == "/dev/null"
            if not current.new_is_null:
                current.new_path = _clean_path(value)
                current.path = current.new_path or current.path
            elif current.old_path:
                current.path = current.old_path
            continue
        match = _HUNK.match(raw_line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(3))
            continue
        if current is None or old_line is None or new_line is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current.additions += 1
            parsed.lines.append(
                ChangedLine(current.path, max(new_line, 1), "new", raw_line[1:])
            )
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current.deletions += 1
            parsed.lines.append(
                ChangedLine(current.path, max(old_line, 1), "old", raw_line[1:])
            )
            old_line += 1
        elif raw_line.startswith(" "):
            old_line += 1
            new_line += 1

    if not parsed.files or not parsed.lines:
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message=(
                    "Input does not contain a parseable unified diff with changed lines."
                ),
            )
        )
    return parsed


def review_diff(diff_text: str, *, max_findings: int = 50) -> DiffReview:
    parsed = parse_unified_diff(diff_text)
    findings: list[DiffFinding] = []
    seen: set[tuple[str, int | None, str]] = set()
    truncated = False

    for changed in parsed.lines:
        for rule in _RULES:
            if changed.side not in rule.sides or not rule.pattern.search(changed.content):
                continue
            key = (changed.file, changed.line, rule.rule_id)
            if key in seen:
                continue
            seen.add(key)
            if len(findings) >= max_findings:
                truncated = True
                continue
            evidence = (
                "[REDACTED credential-shaped line]"
                if rule.rule_id == "secret-in-diff"
                else redact(changed.content.strip())[:180] or None
            )
            findings.append(
                DiffFinding(
                    file=changed.file,
                    line=changed.line,
                    side=changed.side,
                    severity=rule.severity,
                    category=rule.category,
                    rationale=rule.rationale,
                    evidence=evidence,
                    rule_id=rule.rule_id,
                )
            )

    findings.sort(key=_finding_sort_key)
    counts = {severity: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity] += 1
    return DiffReview(
        files=[item.summary() for item in parsed.files],
        findings=findings,
        finding_counts=counts,
        truncated=truncated,
        reviewed_added_lines=sum(item.additions for item in parsed.files),
        reviewed_removed_lines=sum(item.deletions for item in parsed.files),
    )


def propose_commit_message(diff_text: str) -> CommitProposal:
    parsed = parse_unified_diff(diff_text)
    summaries = [item.summary() for item in parsed.files]
    paths = [item.file for item in summaries]
    commit_type = _commit_type(paths, parsed.lines)
    scope = _commit_scope(paths)
    subject = _commit_subject(commit_type, scope, summaries)
    header = f"{commit_type}{f'({scope})' if scope else ''}: {subject}"[:72].rstrip()
    additions = sum(item.additions for item in summaries)
    deletions = sum(item.deletions for item in summaries)
    shown_paths = ", ".join(paths[:3])
    if len(paths) > 3:
        shown_paths += f", and {len(paths) - 3} more"
    body = (
        f"Update {len(paths)} staged "
        f"file{'s' if len(paths) != 1 else ''}: {shown_paths}.\n\n"
        f"The diff adds {additions} line{'s' if additions != 1 else ''} and removes "
        f"{deletions} line{'s' if deletions != 1 else ''}; verification should cover "
        "the affected behavior and its repository checks."
    )
    breaking = any(
        "BREAKING CHANGE" in line.content or "breaking:" in line.content.casefold()
        for line in parsed.lines
    )
    if breaking:
        body += (
            "\n\nBREAKING CHANGE: the staged diff explicitly marks an incompatible "
            "change."
        )
    return CommitProposal(
        type=commit_type,
        scope=scope,
        subject=subject,
        header=header,
        body=body,
        breaking_change=breaking,
        full_message=f"{header}\n\n{body}",
    )


def _split_diff_header(line: str) -> list[str]:
    try:
        values = shlex.split(line.removeprefix("diff --git "))
    except ValueError:
        values = line.removeprefix("diff --git ").split()
    return values[:2]


def _clean_path(path: str) -> str:
    value = path.strip().strip('"').replace("\\", "/")
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _finding_sort_key(finding: DiffFinding) -> tuple[int, str, int]:
    severity_order = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
        Severity.INFO: 4,
    }
    return severity_order[finding.severity], finding.file, finding.line or 0


def _commit_type(paths: list[str], lines: list[ChangedLine]) -> str:
    lowered = [path.casefold() for path in paths]
    if all(path.startswith(("docs/", "readme", "license")) for path in lowered):
        return "docs"
    if all(_is_test_path(path) for path in lowered):
        return "test"
    if all(path.startswith(".github/") for path in lowered):
        return "ci"
    if all(
        path.endswith(("lock", "lock.json", "pyproject.toml", "package.json"))
        for path in lowered
    ):
        return "build"
    added_text = " ".join(line.content.casefold() for line in lines if line.side == "new")
    if re.search(r"\b(?:fix|bug|regression|incorrect|crash)\b", added_text):
        return "fix"
    if re.search(r"\b(?:refactor|rename|extract)\b", added_text):
        return "refactor"
    if re.search(r"\b(?:performance|optimi[sz]e|faster)\b", added_text):
        return "perf"
    return "feat"


def _commit_scope(paths: list[str]) -> str | None:
    package_scopes = {
        parts[1]
        for path in paths
        if len(parts := PurePosixPath(path).parts) >= 2 and parts[0] == "packages"
    }
    if len(package_scopes) == 1:
        return next(iter(package_scopes))
    top_levels = {PurePosixPath(path).parts[0] for path in paths if path}
    if len(top_levels) == 1:
        candidate = next(iter(top_levels)).removeprefix(".")
        return re.sub(r"[^a-z0-9-]+", "-", candidate.casefold()).strip("-") or None
    return None


def _commit_subject(
    commit_type: str, scope: str | None, summaries: list[DiffFileSummary]
) -> str:
    if commit_type == "docs":
        return "update project documentation"
    if commit_type == "test":
        return "expand regression coverage"
    if commit_type == "ci":
        return "update automation workflow"
    if commit_type == "build":
        return "update project dependencies"
    action = "add" if any(item.change_type == "added" for item in summaries) else "update"
    target = (scope or PurePosixPath(summaries[0].file).stem).replace("-", " ")
    return f"{action} {target} workflow"


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    return (
        normalized.startswith(("tests/", "test/"))
        or name.startswith("test_")
        or (".test." in name or ".spec." in name)
    )
