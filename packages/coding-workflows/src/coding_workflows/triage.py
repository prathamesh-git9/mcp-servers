"""Map tracebacks and test failures back to likely repository files."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from coding_workflows.models import FailureTriage, FileCandidate
from coding_workflows.toolchain import repository_files, resolve_repository

_PYTHON_FRAME = re.compile(r"File [\"']([^\"']+)[\"'], line (\d+)")
_GENERIC_FRAME = re.compile(
    r"((?:[A-Za-z]:)?(?:[^\s:()]+[\\/])+[^\s:()]+\.(?:py|js|jsx|ts|tsx|rs|go|java|kt|cs|rb)):(\d+)(?::\d+)?"
)
_PYTEST_NODE = re.compile(r"((?:[^\s:]+[\\/])*test[^\s:]*\.py)(?:::[^\s]+|:(\d+))?")
_SYMBOL_PATTERNS = (
    re.compile(r"NameError: name [\"']([^\"']+)[\"']"),
    re.compile(r"AttributeError: .* has no attribute [\"']([^\"']+)[\"']"),
    re.compile(r"ModuleNotFoundError: No module named [\"']([^\"']+)[\"']"),
    re.compile(r"ImportError: cannot import name [\"']([^\"']+)[\"']"),
    re.compile(r"FAILED\s+[^\s:]+::([A-Za-z_][A-Za-z0-9_]*)"),
)
_TEXT_SUFFIXES = {
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".py",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
}
_MAX_SEARCHED_SOURCE_BYTES = 20 * 1024 * 1024


@dataclass(slots=True)
class _Candidate:
    score: float = 0
    line: int | None = None
    reasons: list[str] = field(default_factory=list)
    symbols: set[str] = field(default_factory=set)


def triage_failure(
    failure_text: str,
    repo_path: str,
    *,
    max_candidates: int = 10,
) -> FailureTriage:
    repository = resolve_repository(repo_path)
    files = repository_files(repository)
    relative = {path.relative_to(repository).as_posix(): path for path in files}
    by_name: dict[str, list[str]] = {}
    for path in relative:
        by_name.setdefault(Path(path).name.casefold(), []).append(path)
    candidates: dict[str, _Candidate] = {}

    frames = [*_PYTHON_FRAME.findall(failure_text), *_GENERIC_FRAME.findall(failure_text)]
    for raw_path, raw_line in frames:
        matched = _match_path(raw_path, relative, by_name)
        for path in matched:
            _add_candidate(
                candidates,
                path,
                0.98 if _normalize(raw_path).endswith(path.casefold()) else 0.9,
                "The failure output contains an explicit frame for this file.",
                int(raw_line) if raw_line else None,
            )
            for counterpart in _source_counterparts(path, relative):
                _add_candidate(
                    candidates,
                    counterpart,
                    0.78,
                    "Its name corresponds to the failing test module.",
                    None,
                )

    for raw_path, raw_line in _PYTEST_NODE.findall(failure_text):
        for path in _match_path(raw_path, relative, by_name):
            _add_candidate(
                candidates,
                path,
                0.9,
                "Pytest identifies this test module in the failing node id.",
                int(raw_line) if raw_line else None,
            )
            for counterpart in _source_counterparts(path, relative):
                _add_candidate(
                    candidates,
                    counterpart,
                    0.82,
                    "Its source-module name matches the failing test module.",
                    None,
                )

    symbols = _extract_symbols(failure_text)
    scanned_bytes = 0
    for path, absolute in relative.items():
        unmatched_symbols: list[str] = []
        for symbol in symbols:
            normalized_symbol = symbol.casefold().replace(".", "/")
            stem = Path(path).stem.casefold().removeprefix("test_")
            if stem and stem in normalized_symbol:
                _add_candidate(
                    candidates,
                    path,
                    0.66,
                    "The file name matches a module or symbol named in the failure.",
                    None,
                    symbol,
                )
            else:
                unmatched_symbols.append(symbol)
        if not unmatched_symbols or absolute.suffix.casefold() not in _TEXT_SUFFIXES:
            continue
        try:
            size = absolute.stat().st_size
        except OSError:
            continue
        if size > 256_000 or scanned_bytes + size > _MAX_SEARCHED_SOURCE_BYTES:
            continue
        scanned_bytes += size
        try:
            content = absolute.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for symbol in unmatched_symbols:
            if re.search(rf"\b{re.escape(symbol.split('.')[-1])}\b", content):
                _add_candidate(
                    candidates,
                    path,
                    0.52,
                    "The file defines or references a symbol named in the failure.",
                    None,
                    symbol,
                )

    _add_configuration_candidates(failure_text, relative, candidates)
    ranked = sorted(
        candidates.items(),
        key=lambda item: (-item[1].score, _test_penalty(item[0]), item[0]),
    )[:max_candidates]
    return FailureTriage(
        repository=str(repository),
        failure_kind=_failure_kind(failure_text),
        extracted_symbols=symbols,
        candidates=[
            FileCandidate(
                file=path,
                line=data.line,
                score=round(min(data.score, 1), 3),
                reasons=data.reasons,
                matched_symbols=sorted(data.symbols),
            )
            for path, data in ranked
        ],
    )


def _match_path(
    raw_path: str,
    relative: dict[str, Path],
    by_name: dict[str, list[str]],
) -> list[str]:
    normalized = _normalize(raw_path)
    exact = [path for path in relative if normalized.endswith(path.casefold())]
    if exact:
        return sorted(exact, key=len, reverse=True)[:1]
    return by_name.get(Path(normalized).name.casefold(), [])[:3]


def _source_counterparts(test_path: str, relative: dict[str, Path]) -> list[str]:
    name = Path(test_path).stem.casefold()
    if name.startswith("test_"):
        wanted = name.removeprefix("test_")
    elif name.endswith("_test"):
        wanted = name.removesuffix("_test")
    else:
        return []
    return [
        path
        for path in relative
        if Path(path).stem.casefold() == wanted and path != test_path
    ][:4]


def _extract_symbols(failure_text: str) -> list[str]:
    symbols = {
        match.group(1)
        for pattern in _SYMBOL_PATTERNS
        for match in pattern.finditer(failure_text)
        if match.group(1)
    }
    return sorted(symbols)[:20]


def _add_candidate(
    candidates: dict[str, _Candidate],
    path: str,
    score: float,
    reason: str,
    line: int | None,
    symbol: str | None = None,
) -> None:
    candidate = candidates.setdefault(path, _Candidate())
    candidate.score = max(candidate.score, score)
    candidate.line = candidate.line or line
    if reason not in candidate.reasons:
        candidate.reasons.append(reason)
    if symbol:
        candidate.symbols.add(symbol)


def _add_configuration_candidates(
    failure_text: str,
    relative: dict[str, Path],
    candidates: dict[str, _Candidate],
) -> None:
    lowered = failure_text.casefold()
    config_names: list[str] = []
    if any(word in lowered for word in ("ruff", "pytest", "mypy", "dependency")):
        config_names.extend(("pyproject.toml", "tox.ini"))
    if any(word in lowered for word in ("eslint", "jest", "npm", "typescript")):
        config_names.extend(("package.json", "tsconfig.json"))
    if any(word in lowered for word in ("workflow", "github actions", "yaml")):
        config_names.extend(
            path for path in relative if path.startswith(".github/workflows/")
        )
    for name in config_names:
        for path in relative:
            if path == name or Path(path).name == name:
                _add_candidate(
                    candidates,
                    path,
                    0.48,
                    "The failure names tooling configured by this file.",
                    None,
                )


def _failure_kind(failure_text: str) -> str:
    lowered = failure_text.casefold()
    if "traceback (most recent call last)" in lowered:
        return "python_traceback"
    if "failed" in lowered and "::" in failure_text:
        return "test_failure"
    if "error[" in lowered and "-->" in failure_text:
        return "compiler_error"
    if "lint" in lowered or "ruff" in lowered or "eslint" in lowered:
        return "quality_gate_failure"
    return "unclassified_failure"


def _normalize(path: str) -> str:
    return path.strip().strip("\"'").replace("\\", "/").casefold()


def _test_penalty(path: str) -> int:
    normalized = path.casefold()
    return 1 if "test" in Path(normalized).name or normalized.startswith("tests/") else 0
