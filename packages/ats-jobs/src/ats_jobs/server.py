"""MCP stdio server for public ATS role discovery."""

import json
from typing import Annotated

from mcp.server import MCPServer
from mcp_server_common import SourceRef, run_bounded
from pydantic import Field

from ats_jobs.models import DetectBoardResult, RoleDetailResult, RoleListResult
from ats_jobs.service import AtsJobsService

CALL_TIMEOUT_SECONDS = 20.0


def create_server(service: AtsJobsService | None = None) -> MCPServer:
    ats = service or AtsJobsService()
    server = MCPServer(
        "ats-jobs",
        description="Public job discovery across six common ATS providers.",
        version="0.1.0",
    )

    @server.tool(structured_output=True)
    async def detect_board(
        url: Annotated[str, Field(min_length=1, max_length=2048)],
    ) -> DetectBoardResult:
        """Detect the ATS provider and normalized public endpoint from a board URL."""

        outcome = await run_bounded(
            lambda: _async_detect(ats, url), timeout_seconds=CALL_TIMEOUT_SECONDS
        )
        if outcome.failure:
            return DetectBoardResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
            )
        board = outcome.value
        return DetectBoardResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            board=board,
            sources=[]
            if board is None
            else [SourceRef(url=board.board_url, label=board.provider.value)],
        )

    @server.tool(structured_output=True)
    async def list_roles(
        board_url: Annotated[str, Field(min_length=1, max_length=2048)],
        query: Annotated[str | None, Field(max_length=300)] = None,
        location: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> RoleListResult:
        """List and optionally filter all currently public roles from one ATS board."""

        outcome = await run_bounded(
            lambda: ats.list_roles(
                board_url, query=query, location=location, limit=limit
            ),
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        )
        if outcome.failure:
            return RoleListResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
                content_is_untrusted=True,
            )
        board, roles = outcome.value or (None, [])
        return RoleListResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            board=board,
            roles=roles,
            sources=[SourceRef(url=role.url, label=role.title) for role in roles],
            content_is_untrusted=True,
        )

    @server.tool(structured_output=True)
    async def get_role(
        board_url: Annotated[str, Field(min_length=1, max_length=2048)],
        role_id: Annotated[str, Field(min_length=1, max_length=120)],
    ) -> RoleDetailResult:
        """Retrieve the complete public description for one role."""

        outcome = await run_bounded(
            lambda: ats.get_role(board_url, role_id),
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        )
        if outcome.failure:
            return RoleDetailResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
                content_is_untrusted=True,
            )
        board, role = outcome.value or (None, None)
        return RoleDetailResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            board=board,
            role=role,
            sources=[] if role is None else [SourceRef(url=role.url, label=role.title)],
            content_is_untrusted=True,
        )

    @server.resource(
        "ats://providers",
        name="supported-ats-providers",
        description="Supported public ATS board patterns and keyless endpoints.",
        mime_type="application/json",
    )
    def providers_resource() -> str:
        return json.dumps(
            {
                "providers": [
                    "greenhouse",
                    "lever",
                    "ashby",
                    "workable",
                    "smartrecruiters",
                    "recruitee",
                ],
                "authentication": "none for public board endpoints",
            }
        )

    @server.resource(
        "ats://policy",
        name="ats-jobs-policy",
        description="Read-only and trust policy for role data.",
        mime_type="application/json",
    )
    def policy_resource() -> str:
        return json.dumps(
            {
                "read_only": True,
                "applications": "never submitted",
                "content_is_untrusted": True,
                "timeout_seconds": CALL_TIMEOUT_SECONDS,
            }
        )

    @server.prompt(
        name="find-open-roles",
        description="Compare public roles with a supplied candidate profile.",
    )
    def find_open_roles(company_url: str, candidate_profile: str) -> str:
        return (
            f"Find current roles at {company_url} using ats-jobs. Compare only explicit "
            f"requirements to this profile: {candidate_profile}. Cite every role URL and "
            "label inferred fit separately from published facts."
        )

    return server


async def _async_detect(ats: AtsJobsService, url: str):
    return ats.detect_board(url)


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()
