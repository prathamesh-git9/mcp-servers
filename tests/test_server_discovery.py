"""Every server package in this monorepo must be reachable by the guard suite.

`test_protocol_contracts.py` proves a great deal about each server: that the
manifest matches the live protocol, that every tool carries input and output
schemas, that the shared boundary converts exceptions without leaking internals.
It proves all of it against a hardcoded dict of six factories.

That is the gap. Adding a seventh server package means remembering to wire it
into two places by hand -- the manifest and that dict -- and nothing forces
either. Forget both and the contract suite still passes on the six it knows
about, while the new server ships having been checked by nothing at all. The
failure is silent and looks exactly like success.

So these tests derive the server list from the filesystem instead of trusting a
literal, and compare it against the manifest the rest of the suite is anchored
to. A package that appears in `packages/` without being declared fails here,
which is the only place that can notice.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGES = ROOT / "packages"

# The manifest is the seam the contract suite drives from, so it is the right
# thing to hold the filesystem against.
MANIFEST = json.loads((ROOT / "docs" / "manifest.json").read_text(encoding="utf-8"))


def discovered_server_packages() -> set[str]:
    """Return every package directory that ships an MCP server module.

    Presence of `src/<module>/server.py` is the definition of "is a server
    here", so a new package is picked up by existing on disk rather than by
    anyone remembering to register it.
    """

    found: set[str] = set()
    for package in sorted(PACKAGES.iterdir()):
        if not package.is_dir():
            continue
        for module in sorted((package / "src").glob("*/")):
            if (module / "server.py").exists():
                found.add(package.name)
    return found


def declared_server_names() -> set[str]:
    return {server["name"] for server in MANIFEST["servers"]}


def test_every_server_package_is_declared_in_the_manifest() -> None:
    """The test that makes the contract suite self-maintaining."""

    undeclared = discovered_server_packages() - declared_server_names()

    assert not undeclared, (
        f"server packages present on disk but absent from docs/manifest.json: "
        f"{sorted(undeclared)}. A server missing from the manifest is skipped by "
        f"the protocol contract suite and ships unchecked."
    )


def test_every_declared_server_has_a_package() -> None:
    """The inverse: a manifest entry with no code is a stale promise."""

    orphaned = declared_server_names() - discovered_server_packages()

    assert not orphaned, (
        f"declared in docs/manifest.json with no package on disk: {sorted(orphaned)}"
    )


def test_discovery_finds_the_servers_and_skips_the_shared_library() -> None:
    """Pin the discovery rule itself, so a change to it cannot quietly find nothing.

    `common` is the one package with no `server.py`; if discovery ever returns
    it, or returns an empty set, the two tests above would pass vacuously.
    """

    discovered = discovered_server_packages()

    assert "common" not in discovered
    assert len(discovered) >= 6


def test_every_declared_tool_has_a_description() -> None:
    """A tool without a description is unusable by the client choosing it."""

    missing = [
        f"{server['name']}.{tool['name']}"
        for server in MANIFEST["servers"]
        for tool in server["tools"]
        if not tool.get("description", "").strip()
    ]

    assert not missing, f"tools declared without a description: {missing}"


def test_every_server_declares_at_least_one_tool() -> None:
    empty = [server["name"] for server in MANIFEST["servers"] if not server.get("tools")]

    assert not empty, f"servers declaring no tools: {empty}"
