"""Grounded lookup and conservative claim verification."""

import json
import re
from importlib.resources import files

from grounded_cv.models import (
    Citation,
    ClaimAssessment,
    EvidenceChunk,
    Profile,
    RetrievalHit,
)
from grounded_cv.retrieval import HybridRetriever, tokenize

_CLAIM_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*;\s*")


class GroundedCVService:
    def __init__(self, profile: Profile | None = None) -> None:
        self.profile = profile or load_profile()
        self.retriever = HybridRetriever(self.profile.chunks)

    def lookup(self, topic: str, *, limit: int = 5) -> list[RetrievalHit]:
        ranked = self.retriever.search(topic, limit=limit)
        return [
            RetrievalHit(
                chunk=item.chunk,
                fused_score=round(item.score, 8),
                bm25_rank=item.bm25_rank,
                dense_rank=item.dense_rank,
                citation=_citation(item.chunk),
            )
            for item in ranked
        ]

    def verify_claims(self, text: str) -> list[ClaimAssessment]:
        claims = [claim.strip() for claim in _CLAIM_SPLIT.split(text) if claim.strip()]
        return [self._verify_one(claim) for claim in claims]

    def section(self, section: str) -> list[EvidenceChunk]:
        return [chunk for chunk in self.profile.chunks if chunk.section == section]

    def _verify_one(self, claim: str) -> ClaimAssessment:
        hits = self.lookup(claim, limit=3)
        claim_tokens = set(tokenize(claim))
        evidence_tokens = set(token for hit in hits for token in tokenize(hit.chunk.text))
        informative = {
            token
            for token in claim_tokens
            if len(token) > 2 and token not in {"and", "are", "for", "has", "the", "with"}
        }
        coverage = len(informative & evidence_tokens) / max(len(informative), 1)
        claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", claim))
        evidence_numbers = set(
            re.findall(r"\b\d+(?:\.\d+)?\b", " ".join(hit.chunk.text for hit in hits))
        )
        supported = coverage >= 0.62 and claim_numbers <= evidence_numbers
        citations = [hit.citation for hit in hits[:2]] if supported else []
        return ClaimAssessment(
            claim=claim,
            verdict="supported" if supported else "unsupported",
            confidence=round(coverage if supported else 1 - min(coverage, 0.99), 3),
            explanation=(
                "The claim is covered by exact committed profile spans."
                if supported
                else (
                    "The committed profile does not contain enough evidence for this "
                    "claim."
                )
            ),
            citations=citations,
        )


def load_profile() -> Profile:
    profile_path = files("grounded_cv").joinpath("data/profile.json")
    return Profile.model_validate_json(profile_path.read_text(encoding="utf-8"))


def profile_json(profile: Profile) -> str:
    return json.dumps(profile.model_dump(mode="json"), indent=2, sort_keys=True)


def _citation(chunk: EvidenceChunk) -> Citation:
    return Citation(
        chunk_id=chunk.id,
        source_uri=chunk.source_uri,
        exact_quote=chunk.text,
    )
