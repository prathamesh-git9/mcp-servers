"""Pydantic v2 schemas for the grounded CV server."""

from typing import Literal

from mcp_server_common.models import ToolResult
from pydantic import BaseModel, ConfigDict, Field


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    headline: str
    location: str
    links: list[str]


class EvidenceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    section: str
    title: str
    text: str
    source_uri: str


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    person: Person
    retrieval_policy: dict[str, str]
    chunks: list[EvidenceChunk]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_uri: str
    exact_quote: str


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk: EvidenceChunk
    fused_score: float = Field(ge=0)
    bm25_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    citation: Citation


class LookupResult(ToolResult):
    query: str
    retrieval: Literal["bm25+dense+rrf"] = "bm25+dense+rrf"
    hits: list[RetrievalHit] = Field(default_factory=list)


class ClaimAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    verdict: Literal["supported", "unsupported"]
    confidence: float = Field(ge=0, le=1)
    explanation: str
    citations: list[Citation] = Field(default_factory=list)


class VerifyClaimResult(ToolResult):
    text: str
    assessments: list[ClaimAssessment] = Field(default_factory=list)


class EvalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_count: int = Field(ge=1)
    k: int = Field(ge=1)
    recall_at_k: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)
