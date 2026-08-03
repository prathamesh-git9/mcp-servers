# repo-intel

Read-only GitHub repository intelligence for repositories, topics, languages, README
content, code search, CI, commits, issues, and pull requests.

## Run

```bash
uv sync --all-packages
uv run repo-intel
```

## Exact stdio client config

```json
{
  "mcpServers": {
    "repo-intel": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/repo-intel",
        "--with",
        "git+https://github.com/prathamesh-git9/mcp-servers.git#subdirectory=packages/common",
        "repo-intel"
      ]
    }
  }
}
```

Tools: `list_repositories`, `repository_detail`, `search_repository`,
`latest_activity`. Resources: `github://capabilities`, `github://safety`. Prompt:
`investigate-repository`.

`GITHUB_TOKEN` is optional for higher public API limits. The server only sends GET
requests, never logs or returns that token, and marks all repository content untrusted.

