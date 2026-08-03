# MCP API contract

All six processes speak JSON-RPC over MCP stdio using the official Python SDK 2.0.
Every tool returns a Pydantic v2 structured object. Tool failures are values, not raised
transport exceptions.

## Shared result envelope

Every tool output contains:

| Field | Type | Meaning |
|---|---|---|
| `status` | `"ok" \| "error"` | Stable operation status. |
| `failure` | object or null | Typed failure code, safe message, retryability, and optional public source. |
| `duration_ms` | integer | Wall-clock duration observed at the MCP boundary. |
| `sources` | array | Public URL/URI and label pairs supporting the output. |
| `content_is_untrusted` | boolean | Whether returned upstream text must remain inert. |

Failure codes are `invalid_input`, `timeout`, `blocked`, `not_found`, `rate_limited`,
`upstream_error`, `parse_error`, `configuration_error`, `conflict`, and
`internal_error`.

## grounded-cv

Tools:

- `lookup(topic: str, limit: int = 5)` returns ranked evidence chunks with BM25 rank,
  dense rank, RRF score, and exact citation.
- `verify_claim(text: str)` splits assertions and returns a verdict, confidence,
  explanation, and citations for each assertion. Unsupported claims have no citations.

Resources: `cv://profile`, `cv://corpus`, and `cv://section/{section}`.
Prompt: `answer-from-cv(question)`.

## repo-intel

Tools:

- `list_repositories(owner, limit=20)`
- `repository_detail(owner, repository)`
- `search_repository(owner, repository, query, limit=10)`
- `latest_activity(owner, repository, limit=10)`

Resources: `github://capabilities` and `github://safety`.
Prompt: `investigate-repository(owner, repository, question)`.

Only GitHub GET endpoints are implemented. `GITHUB_TOKEN` is optional and only changes
the request rate limit; it is never serialized or logged.

## web-research

Tools:

- `search_web(query, limit=5)`
- `fetch_page(url, allowed_hosts=null, denied_hosts=null)`
- `research_web(query, limit=3)`

Resources: `research://policy` and `research://trust`.
Prompt: `source-backed-research(question)`.

Every fetch resolves and rejects non-public targets, applies explicit host policy,
checks robots.txt with fail-closed errors, paces requests per host, manually revalidates
redirects, caps response size, accepts only HTML/plain text, and marks the result
untrusted.

## ats-jobs

Tools:

- `detect_board(url)`
- `list_roles(board_url, query=null, location=null, limit=50)`
- `get_role(board_url, role_id)`

Resources: `ats://providers` and `ats://policy`.
Prompt: `find-open-roles(company_url, candidate_profile)`.

The read-only adapters use public GET contracts from
[Greenhouse](https://developers.greenhouse.io/job-board.html),
[Lever](https://github.com/lever/postings-api),
[Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api),
[Workable](https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page),
[SmartRecruiters](https://developers.smartrecruiters.com/docs/endpoints), and
[Recruitee](https://support.recruitee.com/en/articles/8213076-faq-api).

## outcome-ledger

Tools:

- `begin(intent: object)` returns `intent_` plus the SHA-256 hash of canonical JSON.
- `record(key, outcome: object)` commits the first proven outcome; an exact retry is
  idempotent and a different result is a typed conflict.
- `status(key)` returns the durable entry.

Resources: `ledger://semantics` and `ledger://durability`.
Prompt: `perform-idempotently(intent_description)`.

State transitions:

```text
new --begin--> pending --record--> recorded
                   |
               process restart
                   v
             outcome_unknown --reconcile + record--> recorded
```

Opening the SQLite database is the restart boundary. It atomically promotes every
leftover `pending` row to `outcome_unknown`; callers must reconcile with the external
system rather than repeat the effect blindly. The database uses WAL, `synchronous=FULL`,
and `BEGIN IMMEDIATE` transactions. Set `OUTCOME_LEDGER_DB` to override its platform data
directory.

## coding-workflows

Tools:

- `review_diff(diff_text, max_findings=50)` parses a standard unified diff and returns
  findings with `file`, old/new `line`, `side`, `severity`, `category`, `rule_id`, safe
  evidence, and rationale.
- `plan_change(request, repo_path=null, max_steps=7)` returns an ordered task graph. Every
  step has explicit dependencies, likely files, and acceptance criteria.
- `run_checks(repo_path=null, categories=null, timeout_seconds=60)` detects and executes
  configured format, lint, and test gates. Each gate reports argv, category, status,
  exit code, duration, and bounded redacted output; a nonzero exit or runner fault is
  returned as data rather than raised.
- `triage_failure(failure_text, repo_path=null, max_candidates=10)` maps traceback frames,
  test node IDs, symbols, and tool errors onto ranked files from the repository itself.
- `commit_message(staged_diff)` returns a conventional header plus a body summarizing the
  staged file and line impact.

Resources: `coding://toolchain` and `coding://checks`.
Prompt: `guarded-review(change_request)`.

The default repository is the server process working directory. Repository commands are
passed as argv directly, never through an MCP-side shell; output is size-capped and common
credential shapes are redacted. The runner strips common credential variables and places
both each gate and the entire MCP call under deadlines. Because tools such as `npm run`
execute scripts committed by the target repository, callers must use `run_checks` only on
repositories they trust.
