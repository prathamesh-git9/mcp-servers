# outcome-ledger

A SQLite-backed idempotency ledger that never pretends a post-crash external outcome is
known.

## Run

```bash
uv sync --all-packages
uv run outcome-ledger
```

## Exact stdio client config

```json
{
  "mcpServers": {
    "outcome-ledger": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/outcome-ledger",
        "--with",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/common",
        "outcome-ledger"
      ]
    }
  }
}
```

Tools: `begin`, `record`, `status`. Resources: `ledger://semantics`,
`ledger://durability`. Prompt: `perform-idempotently`.

The database uses WAL, `synchronous=FULL`, and immediate transactions. A new process
promotes leftover `pending` rows to `outcome_unknown`; clients must reconcile instead of
blindly retrying. Set `OUTCOME_LEDGER_DB` to override the platform data path.

