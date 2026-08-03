"""ATS board detection, public retrieval, filtering, and role detail."""

import re
from urllib.parse import urlsplit

from mcp_server_common import Failure, FailureCode, ServiceError
from mcp_server_common.http import HttpGateway

from ats_jobs.models import BoardRef, JobRole, Provider
from ats_jobs.providers import ADAPTERS

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class AtsJobsService:
    def __init__(self, gateway: HttpGateway | None = None) -> None:
        self.gateway = gateway or HttpGateway(timeout_seconds=10, max_bytes=4_000_000)

    def detect_board(self, url: str) -> BoardRef:
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        segments = [segment for segment in parts.path.split("/") if segment]
        provider: Provider | None = None
        token: str | None = None

        if (
            host
            in {
                "jobs.greenhouse.io",
                "boards.greenhouse.io",
                "job-boards.greenhouse.io",
            }
            and segments
        ):
            provider, token = Provider.GREENHOUSE, segments[0]
        elif host == "boards-api.greenhouse.io" and "boards" in segments:
            provider, token = Provider.GREENHOUSE, _after(segments, "boards")
        elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and segments:
            provider, token = Provider.LEVER, segments[0]
        elif host in {"api.lever.co", "api.eu.lever.co"} and "postings" in segments:
            provider, token = Provider.LEVER, _after(segments, "postings")
        elif host == "jobs.ashbyhq.com" and segments:
            provider, token = Provider.ASHBY, segments[0]
        elif host == "api.ashbyhq.com" and "job-board" in segments:
            provider, token = Provider.ASHBY, _after(segments, "job-board")
        elif host == "apply.workable.com" and segments:
            provider, token = Provider.WORKABLE, segments[0]
        elif host in {"www.workable.com", "workable.com"} and "accounts" in segments:
            provider, token = Provider.WORKABLE, _after(segments, "accounts")
        elif host == "careers.smartrecruiters.com" and segments:
            provider, token = Provider.SMARTRECRUITERS, segments[0]
        elif host == "api.smartrecruiters.com" and "companies" in segments:
            provider, token = Provider.SMARTRECRUITERS, _after(segments, "companies")
        elif host.endswith(".recruitee.com"):
            provider, token = Provider.RECRUITEE, host.removesuffix(".recruitee.com")

        if provider is None or token is None or not _TOKEN.fullmatch(token):
            raise ServiceError(
                Failure(
                    code=FailureCode.INVALID_INPUT,
                    message="The URL is not a recognized public ATS board.",
                )
            )
        return ADAPTERS[provider].board(token, url)

    async def list_roles(
        self,
        board_url: str,
        *,
        query: str | None = None,
        location: str | None = None,
        limit: int = 50,
    ) -> tuple[BoardRef, list[JobRole]]:
        board = self.detect_board(board_url)
        adapter = ADAPTERS[board.provider]
        _, payload = await self.gateway.get_json(
            board.api_url, params=adapter.list_params()
        )
        roles = adapter.parse(board, payload)
        if query:
            needle = query.casefold()
            roles = [role for role in roles if needle in _role_search_text(role)]
        if location:
            needle = location.casefold()
            roles = [role for role in roles if needle in (role.location or "").casefold()]
        return board, roles[:limit]

    async def get_role(self, board_url: str, role_id: str) -> tuple[BoardRef, JobRole]:
        if not _TOKEN.fullmatch(role_id):
            raise ServiceError(
                Failure(
                    code=FailureCode.INVALID_INPUT,
                    message="role_id contains unsupported characters.",
                )
            )
        board = self.detect_board(board_url)
        adapter = ADAPTERS[board.provider]
        detail_url = adapter.detail_url(board, role_id)
        if detail_url:
            _, payload = await self.gateway.get_json(detail_url)
            return board, adapter.parse_detail(board, payload)
        _, roles = await self.list_roles(board_url, limit=100)
        role = next((item for item in roles if item.id == role_id), None)
        if role is None:
            raise ServiceError(
                Failure(
                    code=FailureCode.NOT_FOUND,
                    message="The public role was not found on this board.",
                    source_url=board.api_url,
                )
            )
        return board, role


def _after(segments: list[str], marker: str) -> str | None:
    index = segments.index(marker) + 1
    return segments[index] if index < len(segments) else None


def _role_search_text(role: JobRole) -> str:
    return " ".join(
        (role.title, role.department or "", role.description or "")
    ).casefold()
