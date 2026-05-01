#!/usr/bin/env python3
"""Detect whether Notion MCP is connected to this Claude Code installation.

Returns JSON describing the resolved server name + tool prefix, or a flag
indicating no Notion MCP was found.

Used by the setup wizard to (a) confirm Notion is reachable before walking
the user through database creation, and (b) capture the actual MCP tool
prefix for connectors.json so agents know which UUID their tool names live
under.

Usage:
    python3 detect-notion-mcp.py
    # → {"detected": true, "name": "notion", "prefix": "mcp__notion__",
    #    "install_method": "cli", "transport": "sse", ...}

Exits 0 if detected, 1 if not detected, 2 if `claude mcp list` itself failed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys


def run_mcp_list() -> tuple[str, str, int]:
    try:
        proc = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except FileNotFoundError:
        return "", "claude CLI not found on PATH", 127
    except subprocess.TimeoutExpired:
        return "", "claude mcp list timed out after 15s", 124
    except Exception as e:
        return "", f"unexpected error: {e}", 1


# Known Claude Code output shapes for `claude mcp list`. Each entry can yield
# a server name. We match notion case-insensitively against either the server
# name itself or the URL/transport hint so connector-installed Notion (which
# may show as a UUID in the server-name column) is still detected via its
# `https://mcp.notion.com` URL.
NAME_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_\-]+)\s*[:\s].*?(?P<url>https?://\S+)?",
    re.MULTILINE,
)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def parse_for_notion(stdout: str) -> dict | None:
    """Return {name, install_method, transport_hint} if a Notion server is found, else None."""
    for line in stdout.splitlines():
        if not line.strip():
            continue
        # Direct match on the server name being literally 'notion'
        line_lc = line.lower()
        if "notion" not in line_lc:
            continue
        # Pull the first token as candidate server name
        m = re.match(r"\s*([A-Za-z0-9_\-]+)", line)
        if not m:
            continue
        name = m.group(1)
        url_match = re.search(r"https?://\S+", line)
        transport_hint = ""
        if url_match:
            transport_hint = url_match.group(0)
        # Heuristic: if the first token is a UUID, it's a connector-installed server
        # whose visible "name" is the UUID. If it's a normal token, CLI-installed.
        install_method = "connector" if UUID_RE.match(name) else "cli"
        # Final sanity: if URL hint mentions notion.com (or the name is 'notion'),
        # accept this line.
        if name.lower() == "notion" or "notion.com" in line_lc:
            return {
                "name": name,
                "install_method": install_method,
                "transport_hint": transport_hint,
                "raw_line": line.strip(),
            }
    return None


def main():
    stdout, stderr, code = run_mcp_list()
    if code not in (0, 1):
        # claude CLI failure (not "no servers" — that's exit 0 with empty list)
        print(json.dumps({
            "detected": False,
            "error": stderr.strip() or f"claude mcp list exited {code}",
            "raw_stdout": stdout,
            "raw_stderr": stderr,
        }, indent=2))
        sys.exit(2)

    parsed = parse_for_notion(stdout)
    if parsed is None:
        print(json.dumps({
            "detected": False,
            "raw_stdout": stdout,
            "hint": "Run: claude mcp add notion --transport sse https://mcp.notion.com/sse",
        }, indent=2))
        sys.exit(1)

    prefix = f"mcp__{parsed['name']}__"
    out = {
        "detected": True,
        "name": parsed["name"],
        "prefix": prefix,
        "install_method": parsed["install_method"],
        "transport_hint": parsed["transport_hint"],
        "raw_line": parsed["raw_line"],
    }
    print(json.dumps(out, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
