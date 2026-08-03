# grounded-cv

Structured profile resources, hybrid BM25 + 384-D dense retrieval fused with RRF, and
per-claim exact-span verification.

## Run

```bash
uv sync --all-packages
uv run grounded-cv
```

## Exact stdio client config

```json
{
  "mcpServers": {
    "grounded-cv": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/grounded-cv",
        "--with",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/common",
        "grounded-cv"
      ]
    }
  }
}
```

Tools: `lookup`, `verify_claim`. Resources: `cv://profile`, `cv://corpus`,
`cv://section/{section}`. Prompt: `answer-from-cv`.

Atomic chunks never cross section boundaries. BM25 and deterministic dense feature-hash
vectors are independently ranked and fused with `RRF(k=60)`. Run `uv run
grounded-cv-eval`; the committed 10-query set reports recall@5 1.0000, MRR 1.0000, and
nDCG@5 0.9920.

