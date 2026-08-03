"""Pydantic v2 schemas for web research."""

from mcp_server_common.models import ToolResult
from pydantic import BaseModel, ConfigDict, Field


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    snippet: str = ""


class SearchResult(ToolResult):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)


class ExtractedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    text: str
    word_count: int = Field(ge=0)
    robots_url: str


class FetchResult(ToolResult):
    page: ExtractedPage | None = None


class ResearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_hit: SearchHit
    page: ExtractedPage | None = None
    fetch_failure: str | None = None


class ResearchResult(ToolResult):
    query: str
    items: list[ResearchItem] = Field(default_factory=list)
