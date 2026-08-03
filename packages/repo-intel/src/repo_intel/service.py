"""Read-only GitHub REST client."""

import base64
import os
import re

from mcp_server_common import Failure, FailureCode, ServiceError
from mcp_server_common.cache import TTLCache
from mcp_server_common.http import HttpGateway

from repo_intel.models import (
    ActivityItem,
    CodeMatch,
    RepositoryDetail,
    RepositorySummary,
    WorkflowSnapshot,
)

_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_API = "https://api.github.com"


class RepoIntelService:
    def __init__(
        self,
        gateway: HttpGateway | None = None,
        *,
        token: str | None = None,
    ) -> None:
        self.gateway = gateway or HttpGateway(timeout_seconds=8)
        self._token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self._cache: TTLCache[object] = TTLCache(ttl_seconds=180)

    async def list_repositories(
        self, owner: str, *, limit: int = 20
    ) -> list[RepositorySummary]:
        self._validate_slug(owner, "owner")
        key = f"repos:{owner.casefold()}:{limit}"
        cached = await self._cache.get(key)
        if isinstance(cached, list):
            return cached
        _, payload = await self.gateway.get_json(
            f"{_API}/users/{owner}/repos",
            headers=self._headers(),
            params={"per_page": limit, "sort": "updated", "type": "owner"},
        )
        repositories = [self._summary(item) for item in _expect_list(payload)[:limit]]
        await self._cache.put(key, repositories)
        return repositories

    async def repository_detail(self, owner: str, repository: str) -> RepositoryDetail:
        self._validate_repo(owner, repository)
        base = f"{_API}/repos/{owner}/{repository}"
        _, repo_data = await self.gateway.get_json(base, headers=self._headers())
        _, languages_data = await self.gateway.get_json(
            f"{base}/languages", headers=self._headers()
        )
        readme_excerpt = await self._readme_excerpt(base)
        latest_ci = await self._latest_ci(base)
        return RepositoryDetail(
            repository=self._summary(_expect_dict(repo_data)),
            languages={
                str(key): int(value)
                for key, value in _expect_dict(languages_data).items()
                if isinstance(value, int)
            },
            readme_excerpt=readme_excerpt,
            latest_ci=latest_ci,
        )

    async def search_repository(
        self, owner: str, repository: str, query: str, *, limit: int = 10
    ) -> list[CodeMatch]:
        self._validate_repo(owner, repository)
        if not query.strip():
            raise _invalid("query must not be empty")
        _, payload = await self.gateway.get_json(
            f"{_API}/search/code",
            headers={
                **self._headers(),
                "Accept": "application/vnd.github.text-match+json",
            },
            params={"q": f"{query} repo:{owner}/{repository}", "per_page": limit},
        )
        items = _expect_dict(payload).get("items", [])
        return [
            CodeMatch(
                path=str(item.get("path", "")),
                repository=str(item.get("repository", {}).get("full_name", "")),
                url=str(item.get("html_url", "")),
                score=max(float(item.get("score", 0)), 0),
                text_matches=[
                    str(match.get("fragment", ""))[:500]
                    for match in item.get("text_matches", [])
                    if isinstance(match, dict)
                ],
            )
            for item in _expect_list(items)[:limit]
        ]

    async def latest_activity(
        self, owner: str, repository: str, *, limit: int = 10
    ) -> list[ActivityItem]:
        self._validate_repo(owner, repository)
        base = f"{_API}/repos/{owner}/{repository}"
        _, commits = await self.gateway.get_json(
            f"{base}/commits",
            headers=self._headers(),
            params={"per_page": min(limit, 20)},
        )
        _, issues = await self.gateway.get_json(
            f"{base}/issues",
            headers=self._headers(),
            params={"per_page": min(limit, 20), "state": "all", "sort": "updated"},
        )
        activity: list[ActivityItem] = []
        for item in _expect_list(commits):
            commit = item.get("commit", {}) if isinstance(item, dict) else {}
            author = commit.get("author", {}) if isinstance(commit, dict) else {}
            activity.append(
                ActivityItem(
                    kind="commit",
                    title=str(commit.get("message", "")).splitlines()[0][:300],
                    author=str(author.get("name", "")) or None,
                    created_at=str(author.get("date", "")) or None,
                    url=str(item.get("html_url", "")),
                )
            )
        for item in _expect_list(issues):
            activity.append(
                ActivityItem(
                    kind="pull_request" if "pull_request" in item else "issue",
                    title=str(item.get("title", ""))[:300],
                    state=str(item.get("state", "")) or None,
                    author=str(item.get("user", {}).get("login", "")) or None,
                    created_at=str(item.get("created_at", "")) or None,
                    url=str(item.get("html_url", "")),
                )
            )
        activity.sort(key=lambda item: item.created_at or "", reverse=True)
        return activity[:limit]

    async def _readme_excerpt(self, base: str) -> str | None:
        try:
            _, data = await self.gateway.get_json(
                f"{base}/readme", headers=self._headers()
            )
        except ServiceError as exc:
            if exc.failure.code == FailureCode.NOT_FOUND:
                return None
            raise
        raw = str(_expect_dict(data).get("content", "")).replace("\n", "")
        try:
            return base64.b64decode(raw, validate=True).decode("utf-8", errors="replace")[
                :4000
            ]
        except ValueError:
            return None

    async def _latest_ci(self, base: str) -> WorkflowSnapshot | None:
        try:
            _, data = await self.gateway.get_json(
                f"{base}/actions/runs",
                headers=self._headers(),
                params={"per_page": 1},
            )
        except ServiceError as exc:
            if exc.failure.code in {FailureCode.NOT_FOUND, FailureCode.UPSTREAM_ERROR}:
                return None
            raise
        runs = _expect_dict(data).get("workflow_runs", [])
        if not isinstance(runs, list) or not runs:
            return None
        run = _expect_dict(runs[0])
        return WorkflowSnapshot(
            name=str(run.get("name", "workflow")),
            status=str(run.get("status", "unknown")),
            conclusion=str(run.get("conclusion")) if run.get("conclusion") else None,
            branch=str(run.get("head_branch")) if run.get("head_branch") else None,
            url=str(run.get("html_url", "")),
            created_at=str(run.get("created_at")) if run.get("created_at") else None,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _summary(item: dict) -> RepositorySummary:
        owner = item.get("owner", {}) if isinstance(item.get("owner"), dict) else {}
        return RepositorySummary(
            owner=str(owner.get("login", "")),
            name=str(item.get("name", "")),
            description=str(item["description"]) if item.get("description") else None,
            url=str(item.get("html_url", "")),
            default_branch=str(item.get("default_branch", "main")),
            stars=max(int(item.get("stargazers_count", 0)), 0),
            forks=max(int(item.get("forks_count", 0)), 0),
            open_issues=max(int(item.get("open_issues_count", 0)), 0),
            language=str(item["language"]) if item.get("language") else None,
            topics=[str(topic) for topic in item.get("topics", [])],
            archived=bool(item.get("archived", False)),
            updated_at=str(item["updated_at"]) if item.get("updated_at") else None,
        )

    @staticmethod
    def _validate_slug(value: str, label: str) -> None:
        if not _SLUG.fullmatch(value):
            raise _invalid(f"{label} contains unsupported characters")

    def _validate_repo(self, owner: str, repository: str) -> None:
        self._validate_slug(owner, "owner")
        self._validate_slug(repository, "repository")


def _expect_dict(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise _parse_error()
    return payload


def _expect_list(payload: object) -> list[dict]:
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise _parse_error()
    return payload


def _parse_error() -> ServiceError:
    return ServiceError(
        Failure(
            code=FailureCode.PARSE_ERROR,
            message="GitHub returned an unexpected public response shape.",
        )
    )


def _invalid(message: str) -> ServiceError:
    return ServiceError(
        Failure(code=FailureCode.INVALID_INPUT, message=message, retryable=False)
    )
