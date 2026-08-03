"""MCP stdio transport for grounded CV retrieval."""

from typing import Annotated

from mcp.server import MCPServer
from mcp_server_common import SourceRef, run_bounded
from pydantic import Field

from grounded_cv.models import LookupResult, VerifyClaimResult
from grounded_cv.service import GroundedCVService, profile_json

CALL_TIMEOUT_SECONDS = 3.0


def create_server(service: GroundedCVService | None = None) -> MCPServer:
    cv = service or GroundedCVService()
    server = MCPServer(
        "grounded-cv",
        description="Structured CV retrieval and evidence-bound claim verification.",
        version="0.1.0",
    )

    @server.tool(structured_output=True)
    async def lookup(
        topic: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> LookupResult:
        """Retrieve CV evidence with BM25+dense reciprocal-rank fusion."""

        outcome = await run_bounded(
            lambda: _async_lookup(cv, topic, limit), timeout_seconds=CALL_TIMEOUT_SECONDS
        )
        if outcome.failure:
            return LookupResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
                query=topic,
            )
        hits = outcome.value or []
        return LookupResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            query=topic,
            hits=hits,
            sources=[
                SourceRef(url=hit.citation.source_uri, label=hit.chunk.title)
                for hit in hits
            ],
        )

    @server.tool(structured_output=True)
    async def verify_claim(
        text: Annotated[str, Field(min_length=1, max_length=2_000)],
    ) -> VerifyClaimResult:
        """Verify claims and attach exact spans to each supported one."""

        outcome = await run_bounded(
            lambda: _async_verify(cv, text), timeout_seconds=CALL_TIMEOUT_SECONDS
        )
        if outcome.failure:
            return VerifyClaimResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
                text=text,
            )
        assessments = outcome.value or []
        sources = [
            SourceRef(url=citation.source_uri, label=citation.chunk_id)
            for assessment in assessments
            for citation in assessment.citations
        ]
        return VerifyClaimResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            text=text,
            assessments=assessments,
            sources=sources,
        )

    @server.resource(
        "cv://profile",
        name="structured-profile",
        description="The complete versioned profile and atomic evidence chunks.",
        mime_type="application/json",
    )
    def profile_resource() -> str:
        return profile_json(cv.profile)

    @server.resource(
        "cv://corpus",
        name="retrieval-corpus",
        description="The independently citable chunks indexed by both retrievers.",
        mime_type="application/json",
    )
    def corpus_resource() -> str:
        return profile_json(
            cv.profile.model_copy(
                update={"retrieval_policy": cv.profile.retrieval_policy}
            )
        )

    @server.resource(
        "cv://section/{section}",
        name="profile-section",
        description="Atomic evidence chunks from one profile section.",
        mime_type="application/json",
    )
    def section_resource(section: str) -> str:
        return (
            "[" + ",".join(chunk.model_dump_json() for chunk in cv.section(section)) + "]"
        )

    @server.prompt(
        name="answer-from-cv",
        description="Prepare an evidence-only answer with exact CV citations.",
    )
    def answer_from_cv(question: str) -> str:
        return (
            "Answer using only grounded-cv lookup results. Cite each factual claim with "
            "its chunk_id and exact_quote. Say unsupported when evidence is absent. "
            f"Question: {question}"
        )

    return server


async def _async_lookup(cv: GroundedCVService, topic: str, limit: int):
    return cv.lookup(topic, limit=limit)


async def _async_verify(cv: GroundedCVService, text: str):
    return cv.verify_claims(text)


server = create_server()


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
