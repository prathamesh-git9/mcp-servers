"""Committed offline retrieval evaluation harness."""

import argparse
import json
import math
from importlib.resources import files
from pathlib import Path

from grounded_cv.models import EvalMetrics
from grounded_cv.service import GroundedCVService


def evaluate(query_path: Path | None = None, *, k: int = 5) -> EvalMetrics:
    path = query_path or Path(
        str(files("grounded_cv").joinpath("data/eval_queries.json"))
    )
    queries = json.loads(path.read_text(encoding="utf-8"))
    service = GroundedCVService()
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    for item in queries:
        relevant = set(item["relevant"])
        ranked = [hit.chunk.id for hit in service.lookup(item["query"], limit=k)]
        found = relevant.intersection(ranked)
        recalls.append(len(found) / len(relevant))
        first_rank = next(
            (
                index
                for index, chunk_id in enumerate(ranked, start=1)
                if chunk_id in relevant
            ),
            None,
        )
        reciprocal_ranks.append(0 if first_rank is None else 1 / first_rank)
        dcg = sum(
            1 / math.log2(index + 1)
            for index, chunk_id in enumerate(ranked, start=1)
            if chunk_id in relevant
        )
        ideal_count = min(len(relevant), k)
        ideal = sum(1 / math.log2(index + 1) for index in range(1, ideal_count + 1))
        ndcgs.append(dcg / ideal)
    count = len(queries)
    return EvalMetrics(
        query_count=count,
        k=k,
        recall_at_k=round(sum(recalls) / count, 4),
        mrr=round(sum(reciprocal_ranks) / count, 4),
        ndcg_at_k=round(sum(ndcgs) / count, 4),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    print(evaluate(args.queries, k=args.k).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
