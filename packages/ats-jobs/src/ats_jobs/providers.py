"""Pure parsers and public endpoint builders for six ATS providers."""

from abc import ABC, abstractmethod
from html import unescape
from html.parser import HTMLParser
from typing import Any

from mcp_server_common import Failure, FailureCode, ServiceError

from ats_jobs.models import BoardRef, JobRole, Provider


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean:
            self.parts.append(clean)


def plain_text(value: object) -> str | None:
    if value is None:
        return None
    parser = _TextExtractor()
    parser.feed(unescape(str(value)))
    text = " ".join(parser.parts).strip()
    return text or None


class ProviderAdapter(ABC):
    provider: Provider

    @abstractmethod
    def board(self, token: str, original_url: str) -> BoardRef: ...

    @abstractmethod
    def parse(self, board: BoardRef, payload: object) -> list[JobRole]: ...

    def list_params(self) -> dict[str, str | int]:
        return {}

    def detail_url(self, board: BoardRef, role_id: str) -> str | None:
        return None

    def parse_detail(self, board: BoardRef, payload: object) -> JobRole:
        roles = self.parse(board, [payload])
        if not roles:
            raise _parse_error(self.provider)
        return roles[0]


class GreenhouseAdapter(ProviderAdapter):
    provider = Provider.GREENHOUSE

    def board(self, token: str, original_url: str) -> BoardRef:
        return BoardRef(
            provider=self.provider,
            token=token,
            board_url=f"https://job-boards.greenhouse.io/{token}",
            api_url=f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        )

    def list_params(self) -> dict[str, str | int]:
        return {"content": "true"}

    def detail_url(self, board: BoardRef, role_id: str) -> str:
        return f"{board.api_url}/{role_id}"

    def parse(self, board: BoardRef, payload: object) -> list[JobRole]:
        jobs = _dict(payload).get("jobs", [])
        return [self._role(board, item) for item in _list(jobs)]

    def parse_detail(self, board: BoardRef, payload: object) -> JobRole:
        return self._role(board, _dict(payload))

    def _role(self, board: BoardRef, item: dict) -> JobRole:
        location = item.get("location", {})
        departments = item.get("departments", [])
        department = departments[0].get("name") if departments else None
        return JobRole(
            id=str(item.get("id", "")),
            provider=self.provider,
            company=board.token,
            title=str(item.get("title", "")),
            location=str(location.get("name", "")) or None,
            department=str(department) if department else None,
            description=plain_text(item.get("content")),
            url=str(item.get("absolute_url", board.board_url)),
            posted_at=str(item.get("updated_at")) if item.get("updated_at") else None,
            source_url=board.api_url,
        )


class LeverAdapter(ProviderAdapter):
    provider = Provider.LEVER

    def board(self, token: str, original_url: str) -> BoardRef:
        eu = "eu.lever.co" in original_url.casefold()
        api_host = "api.eu.lever.co" if eu else "api.lever.co"
        jobs_host = "jobs.eu.lever.co" if eu else "jobs.lever.co"
        return BoardRef(
            provider=self.provider,
            token=token,
            board_url=f"https://{jobs_host}/{token}",
            api_url=f"https://{api_host}/v0/postings/{token}",
        )

    def list_params(self) -> dict[str, str | int]:
        return {"mode": "json"}

    def detail_url(self, board: BoardRef, role_id: str) -> str:
        return f"{board.api_url}/{role_id}?mode=json"

    def parse(self, board: BoardRef, payload: object) -> list[JobRole]:
        return [self._role(board, item) for item in _list(payload)]

    def parse_detail(self, board: BoardRef, payload: object) -> JobRole:
        return self._role(board, _dict(payload))

    def _role(self, board: BoardRef, item: dict) -> JobRole:
        categories = item.get("categories", {})
        return JobRole(
            id=str(item.get("id", "")),
            provider=self.provider,
            company=board.token,
            title=str(item.get("text", "")),
            location=str(categories.get("location", "")) or None,
            department=str(categories.get("department", "")) or None,
            employment_type=str(categories.get("commitment", "")) or None,
            workplace_type=str(item.get("workplaceType", "")) or None,
            description=plain_text(
                item.get("descriptionPlain") or item.get("description")
            ),
            url=str(item.get("hostedUrl", board.board_url)),
            apply_url=str(item.get("applyUrl")) if item.get("applyUrl") else None,
            source_url=board.api_url,
        )


class AshbyAdapter(ProviderAdapter):
    provider = Provider.ASHBY

    def board(self, token: str, original_url: str) -> BoardRef:
        return BoardRef(
            provider=self.provider,
            token=token,
            board_url=f"https://jobs.ashbyhq.com/{token}",
            api_url=f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        )

    def list_params(self) -> dict[str, str | int]:
        return {"includeCompensation": "true"}

    def parse(self, board: BoardRef, payload: object) -> list[JobRole]:
        jobs = _dict(payload).get("jobs", [])
        return [self._role(board, item) for item in _list(jobs)]

    def _role(self, board: BoardRef, item: dict) -> JobRole:
        return JobRole(
            id=str(item.get("id") or item.get("jobUrl", "").rstrip("/").split("/")[-1]),
            provider=self.provider,
            company=board.token,
            title=str(item.get("title", "")),
            location=str(item.get("location", "")) or None,
            department=str(item.get("department") or item.get("team") or "") or None,
            employment_type=str(item.get("employmentType", "")) or None,
            workplace_type="remote" if item.get("isRemote") else None,
            description=plain_text(
                item.get("descriptionHtml") or item.get("description")
            ),
            url=str(item.get("jobUrl", board.board_url)),
            apply_url=str(item.get("applyUrl")) if item.get("applyUrl") else None,
            posted_at=str(item.get("publishedAt")) if item.get("publishedAt") else None,
            source_url=board.api_url,
        )


class WorkableAdapter(ProviderAdapter):
    provider = Provider.WORKABLE

    def board(self, token: str, original_url: str) -> BoardRef:
        return BoardRef(
            provider=self.provider,
            token=token,
            board_url=f"https://apply.workable.com/{token}/",
            api_url=f"https://www.workable.com/api/accounts/{token}",
        )

    def list_params(self) -> dict[str, str | int]:
        return {"details": "true"}

    def parse(self, board: BoardRef, payload: object) -> list[JobRole]:
        data = _dict(payload)
        jobs = data.get("jobs") or data.get("results") or []
        return [self._role(board, item) for item in _list(jobs)]

    def _role(self, board: BoardRef, item: dict) -> JobRole:
        location = item.get("location", {})
        location_text = (
            location.get("location_str") if isinstance(location, dict) else location
        )
        shortcode = str(item.get("shortcode") or item.get("id", ""))
        return JobRole(
            id=shortcode,
            provider=self.provider,
            company=board.token,
            title=str(item.get("title", "")),
            location=str(location_text) if location_text else None,
            department=str(item.get("department", "")) or None,
            workplace_type=(
                str(location.get("workplace_type", "")) or None
                if isinstance(location, dict)
                else None
            ),
            description=plain_text(
                item.get("description") or item.get("description_html")
            ),
            url=str(item.get("shortlink") or item.get("url") or board.board_url),
            apply_url=(
                str(item.get("application_url")) if item.get("application_url") else None
            ),
            posted_at=str(item.get("created_at")) if item.get("created_at") else None,
            source_url=board.api_url,
        )


class SmartRecruitersAdapter(ProviderAdapter):
    provider = Provider.SMARTRECRUITERS

    def board(self, token: str, original_url: str) -> BoardRef:
        return BoardRef(
            provider=self.provider,
            token=token,
            board_url=f"https://careers.smartrecruiters.com/{token}",
            api_url=f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
        )

    def list_params(self) -> dict[str, str | int]:
        return {"limit": 100, "destination": "PUBLIC"}

    def detail_url(self, board: BoardRef, role_id: str) -> str:
        return f"{board.api_url}/{role_id}"

    def parse(self, board: BoardRef, payload: object) -> list[JobRole]:
        jobs = _dict(payload).get("content", [])
        return [self._role(board, item) for item in _list(jobs)]

    def parse_detail(self, board: BoardRef, payload: object) -> JobRole:
        return self._role(board, _dict(payload))

    def _role(self, board: BoardRef, item: dict) -> JobRole:
        location = item.get("location", {})
        location_parts = [
            str(location.get(key, ""))
            for key in ("city", "region", "country")
            if location.get(key)
        ]
        company = item.get("company", {})
        department = item.get("department", {})
        employment = item.get("typeOfEmployment", {})
        sections = item.get("jobAd", {}).get("sections", {})
        description = " ".join(
            str(section.get("text", ""))
            for section in sections.values()
            if isinstance(section, dict)
        )
        posting_id = str(item.get("id") or item.get("uuid", ""))
        return JobRole(
            id=posting_id,
            provider=self.provider,
            company=str(company.get("name") or company.get("identifier") or board.token),
            title=str(item.get("name", "")),
            location=", ".join(location_parts) or None,
            department=str(department.get("label", "")) or None,
            employment_type=str(employment.get("label", "")) or None,
            workplace_type=("remote" if location.get("remote") else None),
            description=plain_text(description),
            url=str(
                item.get("applyUrl")
                or f"https://jobs.smartrecruiters.com/{board.token}/{posting_id}"
            ),
            apply_url=str(item.get("applyUrl")) if item.get("applyUrl") else None,
            posted_at=str(item.get("releasedDate")) if item.get("releasedDate") else None,
            source_url=board.api_url,
        )


class RecruiteeAdapter(ProviderAdapter):
    provider = Provider.RECRUITEE

    def board(self, token: str, original_url: str) -> BoardRef:
        return BoardRef(
            provider=self.provider,
            token=token,
            board_url=f"https://{token}.recruitee.com/",
            api_url=f"https://{token}.recruitee.com/api/offers",
        )

    def parse(self, board: BoardRef, payload: object) -> list[JobRole]:
        offers = _dict(payload).get("offers", [])
        return [self._role(board, item) for item in _list(offers)]

    def _role(self, board: BoardRef, item: dict) -> JobRole:
        department = item.get("department", {})
        location = item.get("location", "")
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""
        return JobRole(
            id=str(item.get("id", "")),
            provider=self.provider,
            company=board.token,
            title=str(item.get("title", "")),
            location=str(location) or None,
            department=(
                str(department.get("name", "")) or None
                if isinstance(department, dict)
                else str(department) or None
            ),
            employment_type=str(item.get("employment_type", "")) or None,
            workplace_type=str(item.get("remote", "")) or None,
            description=plain_text(item.get("description") or item.get("requirements")),
            url=str(item.get("careers_url") or item.get("url") or board.board_url),
            posted_at=str(item.get("published_at")) if item.get("published_at") else None,
            source_url=board.api_url,
        )


ADAPTERS: dict[Provider, ProviderAdapter] = {
    adapter.provider: adapter
    for adapter in (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        WorkableAdapter(),
        SmartRecruitersAdapter(),
        RecruiteeAdapter(),
    )
}


def _dict(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _parse_error(None)
    return payload


def _list(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise _parse_error(None)
    return payload


def _parse_error(provider: Provider | None) -> ServiceError:
    label = provider.value if provider else "ATS"
    return ServiceError(
        Failure(
            code=FailureCode.PARSE_ERROR,
            message=f"{label} returned an unexpected public response shape.",
        )
    )
