# web-research

Public search and main-content extraction with fail-closed robots enforcement, explicit
host policy, SSRF protection, bounded redirects/responses, and per-host pacing.

## Run

```bash
uv sync --all-packages
uv run web-research
```

## Exact stdio client config

```json
{
  "mcpServers": {
    "web-research": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/web-research",
        "--with",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/common",
        "web-research"
      ]
    }
  }
}
```

Tools: `search_web`, `fetch_page`, `research_web`. Resources: `research://policy`,
`research://trust`. Prompt: `source-backed-research`.

The server performs no authenticated scraping. Fetched text stays inert and every result
includes its public source URL.

