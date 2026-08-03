"""Secret and URL redaction at process boundaries."""

import re
from urllib.parse import urlsplit, urlunsplit

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|token|api[_-]?key)\s*[:=]?\s*[^\s,;]+"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


def redact(value: str) -> str:
    """Remove recognizable credentials from a string."""

    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def safe_source_url(url: str) -> str:
    """Keep a useful source URL while dropping credentials, query, and fragment."""

    parts = urlsplit(url)
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{hostname}{port}", parts.path, "", ""))


def safe_error_message(exc: Exception) -> str:
    """Return a deliberately generic message that cannot echo fetched data."""

    error_name = type(exc).__name__
    return redact(f"Operation failed safely ({error_name}).")[:300]
