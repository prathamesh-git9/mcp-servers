"""Deterministic dependency-aware change planning."""

import re
from pathlib import Path

from coding_workflows.models import ChangePlan, PlanStep
from coding_workflows.toolchain import detect_toolchain, resolve_repository

_PATH_HINT = re.compile(
    r"`([^`]+\.(?:py|js|jsx|ts|tsx|rs|go|java|kt|json|toml|ya?ml|md))`"
)
_CONCERNS = {
    "api-contract": {"api", "endpoint", "schema", "mcp", "tool", "resource"},
    "data-migration": {"database", "migration", "schema", "sqlite", "sql", "model"},
    "security": {
        "auth",
        "permission",
        "secret",
        "secure",
        "security",
        "token",
        "validation",
    },
    "user-interface": {"frontend", "component", "page", "css", "ui", "ux"},
    "delivery": {"ci", "deploy", "docker", "release", "workflow"},
    "documentation": {"docs", "documentation", "readme", "manifest"},
}


def plan_change(
    request: str,
    *,
    repo_path: str | None = None,
    max_steps: int = 7,
) -> ChangePlan:
    normalized = set(re.findall(r"[a-z0-9_-]+", request.casefold()))
    concerns = [
        concern
        for concern, keywords in _CONCERNS.items()
        if normalized.intersection(keywords)
    ]
    repository: Path | None = None
    likely_files = _PATH_HINT.findall(request)
    config_hints: list[str] = []
    if repo_path:
        repository = resolve_repository(repo_path)
        toolchain = detect_toolchain(repository)
        config_hints = toolchain.config_files[:4]

    is_fix = bool(
        normalized.intersection({"bug", "crash", "error", "fail", "fix", "regression"})
    )
    steps: list[PlanStep] = []

    steps.append(
        _step(
            1,
            "Map the affected behavior",
            "Trace the current implementation, callers, tests, and repository "
            "conventions before editing.",
            [],
            likely_files or config_hints,
            [
                "Affected entry points and downstream consumers are listed.",
                "Existing tests and quality commands relevant to the change are "
                "identified.",
            ],
        )
    )
    steps.append(
        _step(
            2,
            "Reproduce and bound the failure" if is_fix else "Define the change contract",
            (
                "Create the smallest deterministic reproduction and state the "
                "invariant that is broken."
                if is_fix
                else "Translate the request into inputs, outputs, failure behavior, "
                "and compatibility constraints."
            ),
            ["step-1"],
            likely_files,
            [
                (
                    "A test or fixture fails for the current behavior and names the "
                    "expected result."
                    if is_fix
                    else "Observable behavior and non-goals are written before "
                    "implementation."
                ),
                "Validation, timeout, and error-boundary expectations are explicit.",
            ],
        )
    )

    dependency = "step-2"
    if concerns and max_steps >= 7:
        concern = concerns[0]
        steps.append(
            _step(
                3,
                f"Resolve {concern} constraints",
                _concern_description(concern),
                [dependency],
                likely_files,
                _concern_acceptance(concern),
            )
        )
        dependency = "step-3"

    implementation_order = len(steps) + 1
    steps.append(
        _step(
            implementation_order,
            "Implement the smallest complete slice",
            "Change the production path while preserving existing boundaries and "
            "typed failure behavior.",
            [dependency],
            likely_files,
            [
                "The requested behavior is reachable through the intended public "
                "entry point.",
                "Invalid inputs and dependency failures return controlled outcomes.",
                "No unrelated files or public contracts change accidentally.",
            ],
        )
    )
    test_order = len(steps) + 1
    steps.append(
        _step(
            test_order,
            "Add focused regression coverage",
            "Cover the success path, boundary conditions, and one representative "
            "failure path offline.",
            [f"step-{implementation_order}"],
            _test_hints(repository, likely_files),
            [
                "New tests fail without the implementation and pass with it.",
                "Tests require no network, credentials, clock luck, or mutable "
                "external state.",
                "Structured output schemas are asserted where the public contract "
                "changes.",
            ],
        )
    )

    if len(steps) < max_steps - 1:
        docs_order = len(steps) + 1
        steps.append(
            _step(
                docs_order,
                "Update integration evidence",
                "Update public documentation, examples, manifests, or migration "
                "notes affected by the change.",
                [f"step-{implementation_order}"],
                [*config_hints, "README.md", "docs/"],
                [
                    "Copy-paste examples match the implemented interface.",
                    "Machine-readable catalogs stay aligned with the live protocol "
                    "surface.",
                ],
            )
        )
        verification_dependencies = [f"step-{test_order}", f"step-{docs_order}"]
    else:
        verification_dependencies = [f"step-{test_order}"]

    verify_order = len(steps) + 1
    steps.append(
        _step(
            verify_order,
            "Run release gates and inspect the final diff",
            "Execute detected format, lint, and test gates, then review the staged "
            "patch for scope and secrets.",
            verification_dependencies,
            config_hints,
            [
                "Every configured gate reports passed with its exit code captured.",
                "The final diff contains only requested changes and no "
                "credential-shaped values.",
                "The acceptance criteria from earlier steps are demonstrably satisfied.",
            ],
        )
    )

    return ChangePlan(
        request=request,
        repository=str(repository) if repository else None,
        detected_concerns=concerns,
        steps=steps[:max_steps],
    )


def _step(
    order: int,
    title: str,
    description: str,
    dependencies: list[str],
    likely_files: list[str],
    acceptance: list[str],
) -> PlanStep:
    return PlanStep(
        id=f"step-{order}",
        order=order,
        title=title,
        description=description,
        depends_on=dependencies,
        likely_files=list(dict.fromkeys(item for item in likely_files if item))[:8],
        acceptance_criteria=acceptance,
    )


def _concern_description(concern: str) -> str:
    descriptions = {
        "api-contract": (
            "Resolve versioning, schema, validation, and consumer compatibility "
            "before coding."
        ),
        "data-migration": (
            "Define forward migration, rollback, idempotency, and mixed-version behavior."
        ),
        "security": (
            "Model trust boundaries, authorization, secret handling, and abuse cases."
        ),
        "user-interface": (
            "Map loading, empty, error, accessibility, and responsive states."
        ),
        "delivery": "Define environment, rollout, observability, and rollback behavior.",
        "documentation": (
            "Identify human and machine-readable contracts that must remain synchronized."
        ),
    }
    return descriptions[concern]


def _concern_acceptance(concern: str) -> list[str]:
    criteria = {
        "api-contract": [
            "Input and output schemas are backward-compatible or explicitly versioned.",
            "Every failure mode has a stable machine-readable representation.",
        ],
        "data-migration": [
            "Migration is idempotent and has a tested recovery or rollback path.",
            "Old and new process versions cannot silently corrupt shared state.",
        ],
        "security": [
            "Untrusted data cannot become code, credentials, or authority.",
            "Logs and structured results contain no secrets.",
        ],
        "user-interface": [
            "Loading, empty, error, keyboard, and narrow-screen states are specified.",
            "The interface exposes errors without leaking internal details.",
        ],
        "delivery": [
            "Rollout and rollback commands are deterministic and documented.",
            "Health and failure signals are observable before full rollout.",
        ],
        "documentation": [
            "Human examples and machine-readable manifests describe the same interface.",
            "A fresh reader can reproduce the workflow from documented commands.",
        ],
    }
    return criteria[concern]


def _test_hints(repository: Path | None, likely_files: list[str]) -> list[str]:
    hints = [item for item in likely_files if "test" in item.casefold()]
    if repository and (repository / "tests").is_dir():
        hints.append("tests/")
    return list(dict.fromkeys(hints))[:8]
