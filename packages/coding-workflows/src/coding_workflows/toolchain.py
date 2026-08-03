"""Repository-local toolchain and quality-gate detection."""

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from mcp_server_common import Failure, FailureCode, ServiceError

from coding_workflows.models import CheckCommand, GateCategory, ToolchainReport

_IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
_LANGUAGE_MARKERS = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".rb": "Ruby",
}


def resolve_repository(repo_path: str | Path) -> Path:
    try:
        repository = Path(repo_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message="The repository path does not exist or cannot be resolved.",
            )
        ) from exc
    if not repository.is_dir():
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message="The repository path must identify a directory.",
            )
        )
    return repository


def detect_toolchain(repo_path: str | Path) -> ToolchainReport:
    repository = resolve_repository(repo_path)
    names = {item.name for item in repository.iterdir()}
    config_files = sorted(
        name
        for name in names
        if name
        in {
            ".pre-commit-config.yaml",
            ".ruff.toml",
            ".eslintrc.json",
            ".eslintrc.yml",
            "Cargo.toml",
            "Makefile",
            "biome.json",
            "eslint.config.js",
            "eslint.config.mjs",
            "go.mod",
            "package.json",
            "pyproject.toml",
            "pytest.ini",
            "ruff.toml",
            "setup.cfg",
            "tsconfig.json",
            "tox.ini",
            "vitest.config.js",
            "vitest.config.ts",
        }
        or name.endswith((".csproj", ".sln"))
    )
    languages = _detect_languages(repository)
    package_managers = _detect_package_managers(names)
    checks: list[CheckCommand] = []
    frameworks: set[str] = set()

    pyproject = _read_toml(repository / "pyproject.toml")
    if pyproject or "Python" in languages:
        python_checks, python_frameworks = _python_checks(repository, pyproject, names)
        checks.extend(python_checks)
        frameworks.update(python_frameworks)

    package_json = _read_json(repository / "package.json")
    if package_json:
        node_checks, node_frameworks = _node_checks(package_json, names)
        checks.extend(node_checks)
        frameworks.update(node_frameworks)

    if "Cargo.toml" in names:
        checks.extend(
            (
                CheckCommand(
                    name="rust-format",
                    category=GateCategory.FORMAT,
                    command=["cargo", "fmt", "--check"],
                    source="Cargo.toml",
                ),
                CheckCommand(
                    name="rust-lint",
                    category=GateCategory.LINT,
                    command=["cargo", "clippy", "--all-targets", "--", "-D", "warnings"],
                    source="Cargo.toml",
                ),
                CheckCommand(
                    name="rust-test",
                    category=GateCategory.TEST,
                    command=["cargo", "test"],
                    source="Cargo.toml",
                ),
            )
        )
    if "go.mod" in names:
        go_sources = [
            path.relative_to(repository).as_posix()
            for path in repository_files(repository, max_files=2_000)
            if path.suffix.casefold() == ".go"
        ][:200]
        if go_sources:
            checks.append(
                CheckCommand(
                    name="go-format",
                    category=GateCategory.FORMAT,
                    command=["gofmt", "-l", *go_sources],
                    source="go.mod",
                    pass_condition="exit_zero_and_empty_output",
                )
            )
        checks.extend(
            (
                CheckCommand(
                    name="go-lint",
                    category=GateCategory.LINT,
                    command=["go", "vet", "./..."],
                    source="go.mod",
                ),
                CheckCommand(
                    name="go-test",
                    category=GateCategory.TEST,
                    command=["go", "test", "./..."],
                    source="go.mod",
                ),
            )
        )

    return ToolchainReport(
        repository=str(repository),
        languages=sorted(languages),
        package_managers=package_managers,
        frameworks=sorted(frameworks),
        config_files=config_files,
        checks=_deduplicate_checks(checks),
    )


def repository_files(repository: Path, *, max_files: int = 5_000) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(repository):
        directories[:] = [
            name for name in directories if name not in _IGNORED_DIRECTORIES
        ]
        for name in names:
            candidate = Path(current, name)
            if candidate.is_symlink():
                continue
            files.append(candidate)
            if len(files) >= max_files:
                return files
    return files


def _detect_languages(repository: Path) -> set[str]:
    languages: set[str] = set()
    for path in repository_files(repository, max_files=2_000):
        language = _LANGUAGE_MARKERS.get(path.suffix.casefold())
        if language:
            languages.add(language)
    return languages


def _detect_package_managers(names: set[str]) -> list[str]:
    detected: list[str] = []
    for marker, manager in (
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("pdm.lock", "pdm"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("Cargo.lock", "cargo"),
        ("go.mod", "go"),
    ):
        if marker in names:
            detected.append(manager)
    node_managers = {"npm", "pnpm", "yarn"}
    if "package.json" in names and node_managers.isdisjoint(detected):
        detected.append("npm")
    return detected


def _python_checks(
    repository: Path, pyproject: dict[str, Any], names: set[str]
) -> tuple[list[CheckCommand], set[str]]:
    tool = pyproject.get("tool", {}) if isinstance(pyproject.get("tool"), dict) else {}
    dependency_text = json.dumps(pyproject.get("project", {})).casefold()
    prefix = _python_prefix(names)
    quality_source = next(
        (
            name
            for name in ("pyproject.toml", ".ruff.toml", "ruff.toml", "setup.cfg")
            if name in names
        ),
        "Python source files",
    )
    checks: list[CheckCommand] = []
    frameworks = {
        name
        for name in ("django", "fastapi", "flask", "pydantic", "pytest")
        if name in dependency_text
    }
    has_ruff = (
        "ruff" in tool
        or "ruff" in dependency_text
        or bool({".ruff.toml", "ruff.toml"}.intersection(names))
    )
    if has_ruff:
        checks.extend(
            (
                CheckCommand(
                    name="python-format",
                    category=GateCategory.FORMAT,
                    command=[*prefix, "ruff", "format", "--check", "."],
                    source=quality_source,
                ),
                CheckCommand(
                    name="python-lint",
                    category=GateCategory.LINT,
                    command=[*prefix, "ruff", "check", "."],
                    source=quality_source,
                ),
            )
        )
    elif "black" in tool or "black" in dependency_text:
        checks.append(
            CheckCommand(
                name="python-format",
                category=GateCategory.FORMAT,
                command=[*prefix, "black", "--check", "."],
                source=quality_source,
            )
        )
    if "mypy" in tool or "mypy" in dependency_text:
        checks.append(
            CheckCommand(
                name="python-types",
                category=GateCategory.LINT,
                command=[*prefix, "mypy", "."],
                source=quality_source,
            )
        )
    has_tests = (
        (repository / "tests").is_dir() or "pytest" in tool or "pytest" in dependency_text
    )
    if has_tests:
        source = "pyproject.toml" if "pyproject.toml" in names else "tests/"
        checks.append(
            CheckCommand(
                name="python-test",
                category=GateCategory.TEST,
                command=[*prefix, "pytest"],
                source=source,
            )
        )
    return checks, frameworks


def _python_prefix(names: set[str]) -> list[str]:
    if "uv.lock" in names:
        return ["uv", "run"]
    if "poetry.lock" in names:
        return ["poetry", "run"]
    if "pdm.lock" in names:
        return ["pdm", "run"]
    return []


def _node_checks(
    package_json: dict[str, Any], names: set[str]
) -> tuple[list[CheckCommand], set[str]]:
    scripts = package_json.get("scripts", {})
    dependencies = {
        **_string_dict(package_json.get("dependencies")),
        **_string_dict(package_json.get("devDependencies")),
    }
    manager = (
        "pnpm" if "pnpm-lock.yaml" in names else "yarn" if "yarn.lock" in names else "npm"
    )
    checks: list[CheckCommand] = []
    if isinstance(scripts, dict):
        for script, category in (
            ("format:check", GateCategory.FORMAT),
            ("lint", GateCategory.LINT),
            ("test", GateCategory.TEST),
        ):
            if script not in scripts:
                continue
            command = [manager, "run", script]
            checks.append(
                CheckCommand(
                    name=f"node-{category.value}",
                    category=category,
                    command=command,
                    source=f"package.json#scripts.{script}",
                )
            )
    frameworks = {
        framework
        for framework in ("express", "jest", "next", "react", "typescript", "vitest")
        if framework in dependencies
    }
    return checks, frameworks


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _deduplicate_checks(checks: list[CheckCommand]) -> list[CheckCommand]:
    unique: list[CheckCommand] = []
    seen: set[tuple[str, ...]] = set()
    for check in checks:
        key = tuple(check.command)
        if key not in seen:
            seen.add(key)
            unique.append(check)
    return unique
