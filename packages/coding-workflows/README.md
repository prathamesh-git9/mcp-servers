# coding-workflows

Deterministic, repository-aware coding workflows for review, change planning, quality
gates, failure triage, and conventional commit synthesis.

## Run

```bash
uv sync --all-packages
uv run coding-workflows
```

The working directory is the default repository. MCP clients can pass a different
trusted local `repo_path` to repository-aware tools.

## Exact stdio client config

```json
{
  "mcpServers": {
    "coding-workflows": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/coding-workflows",
        "--with",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/common",
        "coding-workflows"
      ]
    }
  }
}
```

Tools: `review_diff`, `plan_change`, `run_checks`, `triage_failure`, and
`commit_message`. Resources: `coding://toolchain` and `coding://checks`. Prompt:
`guarded-review`.

Diffs, tracebacks, repository files, and command output remain untrusted data. Quality
gates use direct argv execution rather than an MCP-side command shell, receive an
allowlisted environment without common credential variables, and have both per-gate and
whole-call deadlines. Run configured package-manager scripts only in repositories you
trust, because a repository's own scripts are executable code.
