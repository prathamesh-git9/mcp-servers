# ats-jobs

One typed, read-only role schema across public Greenhouse, Lever, Ashby, Workable,
SmartRecruiters, and Recruitee job boards.

## Run

```bash
uv sync --all-packages
uv run ats-jobs
```

## Exact stdio client config

```json
{
  "mcpServers": {
    "ats-jobs": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/ats-jobs",
        "--with",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/common",
        "ats-jobs"
      ]
    }
  }
}
```

Tools: `detect_board`, `list_roles`, `get_role`. Resources: `ats://providers`,
`ats://policy`. Prompt: `find-open-roles`.

No API key or application endpoint is used. Provider detection and response parsing are
covered by six committed offline fixtures.

