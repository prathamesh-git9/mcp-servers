import pytest
from grounded_cv.eval import evaluate
from grounded_cv.server import create_server
from grounded_cv.service import GroundedCVService
from mcp import Client


def test_hybrid_lookup_and_claim_verification() -> None:
    service = GroundedCVService()

    hits = service.lookup("Java Spring REST backend", limit=3)
    supported = service.verify_claims("Prathamesh is based in Dublin, Ireland.")
    unsupported = service.verify_claims("Prathamesh worked at NASA for 12 years.")

    assert hits[0].chunk.id == "skills.java-spring"
    assert hits[0].bm25_rank is not None
    assert hits[0].dense_rank is not None
    assert supported[0].verdict == "supported"
    assert supported[0].citations[0].exact_quote
    assert unsupported[0].verdict == "unsupported"
    assert unsupported[0].citations == []


def test_committed_eval_has_strong_retrieval_metrics() -> None:
    metrics = evaluate(k=5)

    assert metrics.query_count == 10
    assert metrics.recall_at_k >= 0.9
    assert metrics.mrr >= 0.8
    assert metrics.ndcg_at_k >= 0.8


@pytest.mark.asyncio
async def test_grounded_cv_protocol_surfaces_and_schema_validation() -> None:
    async with Client(create_server()) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        result = await client.call_tool("lookup", {"topic": "Python", "limit": 3})
        invalid = await client.call_tool("lookup", {"topic": "Python", "limit": 99})

    assert {tool.name for tool in tools.tools} == {"lookup", "verify_claim"}
    assert {str(resource.uri) for resource in resources.resources} >= {
        "cv://profile",
        "cv://corpus",
    }
    assert {prompt.name for prompt in prompts.prompts} == {"answer-from-cv"}
    assert result.is_error is False
    assert result.structured_content["retrieval"] == "bm25+dense+rrf"
    assert invalid.is_error is True
