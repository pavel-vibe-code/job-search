#!/usr/bin/env python3
"""Notion REST API helper for the api_token auth path.

Replaces MCP tool calls in environments where MCP isn't available — primarily
Cloud Routines, which run in fresh containers without IDE-side MCP state. Same
operations, same data shapes; transport is direct HTTPS to api.notion.com.

## Auth

Token resolution order (first one that resolves wins):
  1. --token CLI flag
  2. NOTION_API_TOKEN environment variable (preferred for Routines)
  3. ~/.config/ai50-job-search/notion-token file (preferred for local dev)

The script never logs or echoes the token. To test auth: `users-me`.

## Subcommands

  users-me                — verify token; prints the integration's name + workspace
  search                  — POST /v1/search; find a parent page or database
  create-database         — POST /v1/databases; DDL via JSON schema
  create-pages            — POST /v1/pages (multiple); same shape as MCP create-pages
  update-page             — PATCH /v1/pages/<id>; properties + optional body replace
  fetch-page              — GET /v1/pages/<id>; properties only
  fetch-page-body         — GET /v1/blocks/<id>/children; concatenate children to text
  query-database          — POST /v1/databases/<id>/query; paginated, filter-aware
  delete-page             — PATCH /v1/pages/<id> with archived=true (no real delete)
  hydrate-state           — list state-DB rows + parallel body fetch → assemble JSON
  discover                — resolve Notion artifact IDs from names + cache (self-healing
                            for moved/deleted artifacts; used by run-job-search pre-flight)

Each command prints structured JSON on stdout, errors on stderr.
Exit codes: 0=success, 1=API error, 2=auth/config error, 3=usage error.

## Body handling (state-DB use case)

Notion's per-rich-text-block 2000-char limit forced v2.1.0 to truncate state.
v2.2.x stores job IDs in a code-block child of the page body. To handle arrays
larger than 2000 chars, this helper splits content across multiple rich_text
elements inside the SAME code block — Notion treats them as one logical block
but each element stays under the limit. Up to ~100 KB per page body in practice.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

NOTION_API_BASE  = "https://api.notion.com/v1"
# Notion-Version: bumped from 2022-06-28 to 2025-09-03 to support the data
# sources abstraction. Notion silently migrated existing single-source DBs to
# the new format; on 2022-06-28, querying a migrated DB returns an error
# instead of results. The 2025-09-03 contract:
#   - GET /databases/{id} now returns a data_sources: [{id, name}] array
#   - POST /data_sources/{ds_id}/query is the canonical query endpoint
#     (legacy POST /databases/{id}/query still works for some single-source
#     DBs but breaks on multi-source — resolve first, then dispatch)
#   - Page parents that target a database can use either {database_id} (auto-
#     resolved to primary data source) or {data_source_id} explicitly. We
#     keep {database_id} for create_pages — Notion's compat shim handles it.
NOTION_VERSION   = "2025-09-03"
RICH_TEXT_LIMIT  = 1900  # safety margin under Notion's 2000-char per-element limit
HTTP_TIMEOUT     = 30
DEFAULT_TOKEN_FILE = "~/.config/ai50-job-search/notion-token"
# ISO 8601 date / datetime — used by pack_properties to auto-pack date strings
# as Notion date properties. Anchored: must be the entire string.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


# ── Auth ──────────────────────────────────────────────────────────────────────

def resolve_token(cli_token: Optional[str]) -> Optional[str]:
    """Token resolution: --token > env > file. Returns None if none found."""
    if cli_token:
        return cli_token.strip()
    env = os.environ.get("NOTION_API_TOKEN")
    if env:
        return env.strip()
    path = Path(DEFAULT_TOKEN_FILE).expanduser()
    if path.exists():
        try:
            return path.read_text().strip()
        except Exception:
            return None
    return None


def auth_error_and_exit():
    print(json.dumps({
        "error": "no_token",
        "message": "No Notion token found. Set NOTION_API_TOKEN env var, "
                   "or write the token to ~/.config/ai50-job-search/notion-token, "
                   "or pass --token. Mint a token at https://www.notion.so/profile/integrations.",
    }), file=sys.stderr)
    sys.exit(2)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def http_request(method: str, path: str, token: str, body: Optional[dict] = None) -> tuple[Optional[dict], Optional[str], int]:
    """Make a Notion API request. Returns (parsed_body, error_msg, http_status).
    On non-2xx responses, parsed_body is the error JSON if Notion returned one.
    """
    url = NOTION_API_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(), headers={
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
        "Accept":         "application/json",
        "User-Agent":     "ai50-job-search/2.2.2",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = resp.read()
            if not payload:
                return {}, None, resp.status
            try:
                return json.loads(payload.decode("utf-8")), None, resp.status
            except Exception as e:
                return None, f"json_parse_error:{e}", resp.status
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")
        except Exception:
            pass
        try:
            err_json = json.loads(body_text) if body_text else {}
        except Exception:
            err_json = {"raw": body_text[:500]}
        return err_json, f"http_{e.code}", e.code
    except urllib.error.URLError as e:
        return None, f"url_error:{getattr(e, 'reason', e)}", 0
    except Exception as e:
        return None, f"error:{e}", 0


# ── Body / rich-text helpers ──────────────────────────────────────────────────

def split_rich_text(content: str, limit: int = RICH_TEXT_LIMIT) -> list[dict]:
    """Split a long string into multiple rich_text elements under Notion's
    per-element char limit. All elements share the same code-block parent so
    the rendered page shows one continuous code block."""
    if not content:
        return [{"type": "text", "text": {"content": ""}}]
    chunks = [content[i:i + limit] for i in range(0, len(content), limit)]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def code_block(content: str, language: str = "json") -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": split_rich_text(content),
            "language": language,
        },
    }


def paragraph_block(content: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": split_rich_text(content)},
    }


def render_content_to_blocks(content: str) -> list[dict]:
    """Parse a markdown-ish content string into Notion blocks. Recognises:
      - Fenced ```json ... ``` blocks → code blocks (json)
      - ```lang ... ``` → code blocks (with language)
      - Otherwise → paragraph blocks split on double-newlines."""
    if not content:
        return []
    blocks = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            language = line[3:].strip() or "plain text"
            j = i + 1
            body = []
            while j < len(lines) and not lines[j].startswith("```"):
                body.append(lines[j])
                j += 1
            blocks.append(code_block("\n".join(body), language=language))
            i = j + 1
        else:
            # collect until blank line
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith("```"):
                para.append(lines[i])
                i += 1
            if para:
                blocks.append(paragraph_block("\n".join(para)))
            i += 1
    return blocks


def extract_code_block_text(blocks_response: dict) -> Optional[str]:
    """Given the JSON returned by GET /blocks/<id>/children, find the first
    code block and reassemble its full text from rich_text elements. Returns
    None if no code block found."""
    for blk in blocks_response.get("results", []):
        if blk.get("type") == "code":
            rt = blk.get("code", {}).get("rich_text", [])
            return "".join(e.get("text", {}).get("content", "") for e in rt)
    return None


# ── Property packing ──────────────────────────────────────────────────────────

def pack_properties(props_in: dict) -> dict:
    """Take a flat {name: value} dict (like the MCP create-pages shape) and
    convert to Notion's nested property objects. Heuristics:
      - Keys like 'date:NAME:start' → date property NAME with start=value
      - Numeric values → number property
      - Boolean values → checkbox
      - Strings starting with 'http://' or 'https://' → URL
      - Strings keyed under 'userDefined:URL' → URL property named URL
      - Other strings, name 'Title' or 'Name' or first encountered title → title
      - Other strings → rich_text (or select if value matches a known options enum — caller's job)
    The select-vs-rich_text distinction needs schema awareness; pass it in via
    pre-built nested objects when needed (e.g. {"Status": {"select": {"name": "New"}}}).
    """
    out: dict = {}
    expanded_dates: dict[str, dict] = {}
    title_assigned = False
    for k, v in props_in.items():
        # Pre-expanded date format: "date:Name:start" / "date:Name:end" / "date:Name:is_datetime"
        if k.startswith("date:"):
            parts = k.split(":", 2)
            if len(parts) == 3:
                _, name, sub = parts
                expanded_dates.setdefault(name, {})
                if sub == "is_datetime":
                    expanded_dates[name]["_is_datetime"] = bool(v)
                else:
                    expanded_dates[name][sub] = v
            continue
        # userDefined:URL → URL property called URL
        if k.startswith("userDefined:"):
            actual_name = k.split(":", 1)[1]
            out[actual_name] = {"url": v}
            continue
        # Pre-built nested: passes through unchanged
        if isinstance(v, dict) and any(t in v for t in ("title", "rich_text", "select", "number", "url", "date", "checkbox", "multi_select")):
            out[k] = v
            continue
        # Title — first 'Title'/'Name' or 'title' key wins
        if k in ("Title", "Name", "title", "name") and not title_assigned and isinstance(v, str):
            out[k] = {"title": [{"type": "text", "text": {"content": v}}]}
            title_assigned = True
            continue
        # Number
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = {"number": v}
            continue
        # Bool
        if isinstance(v, bool):
            out[k] = {"checkbox": v}
            continue
        # URL
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            out[k] = {"url": v}
            continue
        # ISO date / datetime heuristic — saves callers from needing the
        # "date:Name:start" prefix syntax for the common case of just passing
        # a date string. Matches "2026-05-04" or "2026-05-04T08:00:00..." but
        # NOT free-form text that happens to start with digits.
        if isinstance(v, str) and _ISO_DATE_RE.match(v):
            out[k] = {"date": {"start": v}}
            continue
        # Default: rich_text
        if isinstance(v, str):
            out[k] = {"rich_text": [{"type": "text", "text": {"content": v}}]}
            continue
        # Lists become multi_select
        if isinstance(v, list):
            out[k] = {"multi_select": [{"name": str(item)} for item in v]}
            continue
    # Finalise dates
    for name, parts in expanded_dates.items():
        date_obj = {}
        if "start" in parts:
            date_obj["start"] = parts["start"]
        if parts.get("end"):
            date_obj["end"] = parts["end"]
        if date_obj:
            out[name] = {"date": date_obj}
    # If no title assigned but there's a "Title" key as nested, that's already
    # handled by the pre-built passthrough above.
    return out


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_users_me(args, token):
    body, err, status = http_request("GET", "/users/me", token)
    if err:
        print(json.dumps({"error": err, "status": status, "body": body}), file=sys.stderr)
        sys.exit(2 if status in (401, 403) else 1)
    print(json.dumps({
        "ok": True,
        "name": body.get("name", ""),
        "type": body.get("type", ""),
        "bot": body.get("bot", {}),
        "workspace": (body.get("bot") or {}).get("workspace_name", ""),
    }, indent=2))


def cmd_search(args, token):
    payload = {"query": args.query, "page_size": args.limit}
    if args.type:
        payload["filter"] = {"property": "object", "value": args.type}
    body, err, status = http_request("POST", "/search", token, payload)
    if err:
        print(json.dumps({"error": err, "status": status, "body": body}), file=sys.stderr)
        sys.exit(1)
    results = body.get("results", [])
    out = [{
        "id":     r.get("id"),
        "object": r.get("object"),
        "title":  _extract_title(r),
        "url":    r.get("url"),
    } for r in results]
    print(json.dumps({"results": out, "count": len(out)}, indent=2))


def _extract_title(obj: dict) -> str:
    """Pull a human-readable title from a page or database object."""
    # Database
    if obj.get("object") == "database":
        rt = obj.get("title", [])
        return "".join(e.get("text", {}).get("content", "") for e in rt) or "(untitled database)"
    # Page — find the title-typed property
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            rt = prop.get("title", [])
            return "".join(e.get("text", {}).get("content", "") for e in rt) or "(untitled page)"
    return "(unknown)"


def cmd_create_database(args, token):
    """Create a Notion database under a parent page, then apply the schema's
    properties to its data source.

    Notion-Version 2025-09-03 changed two things vs 2022-06-28:
      1. The `parent` object now requires an explicit `type` discriminator
         (e.g. `{"type": "page_id", "page_id": "..."}`). Without it, some
         workspaces error out on creation (caught in production 2026-05-09).
      2. Properties no longer live on the database itself — they live on a
         data_source within the database. Sending `properties` at the
         database-create level is silently no-op'd: Notion returns 200 OK
         and a database with one auto-named "Name" title property, but
         every other property in the schema is dropped. Fix: create the DB
         (which auto-creates one data_source named "Name"), then PATCH the
         data_source — rename the auto title to whatever our schema's title
         is, then add the rest of the properties in a second PATCH call.
         (One PATCH would fail with `validation_error: Cannot create new
         title property` — Notion rejects two title properties in one call.)
    """
    with open(args.schema) as f:
        schema = json.load(f)
    # Strip underscore-prefixed keys (e.g. "_comment") — they're documentation in
    # the schema files; Notion rejects unknown property types.
    properties = {k: v for k, v in schema.items() if not k.startswith("_")}

    payload = {
        "parent": {"type": "page_id", "page_id": args.parent_page_id},
        "title": [{"type": "text", "text": {"content": args.title}}],
    }
    body, err, status = http_request("POST", "/databases", token, payload)
    if err:
        print(json.dumps({"error": err, "status": status, "body": body}), file=sys.stderr)
        sys.exit(1)

    db_id = body.get("id")
    data_sources = body.get("data_sources") or []
    ds_id = data_sources[0].get("id") if data_sources else None

    # Apply schema properties via the data_source. The DB starts with one
    # auto-created data_source carrying a default `Name` title property.
    if ds_id and properties:
        # Identify our schema's title field (there must be exactly one).
        title_field = next((k for k, v in properties.items() if v.get("title") is not None), None)

        # Step 1: rename the auto `Name` title → our schema's title field name
        # (keeping it as title type — Notion allows renaming the title property
        # but not adding a second title or changing its type).
        if title_field and title_field != "Name":
            _, perr, pstatus = http_request(
                "PATCH", f"/data_sources/{ds_id}", token,
                {"properties": {"Name": {"name": title_field}}},
            )
            if perr:
                print(json.dumps({
                    "error": "data_source_title_rename_failed",
                    "status": pstatus, "detail": perr,
                    "db_id": db_id, "ds_id": ds_id,
                }), file=sys.stderr)
                sys.exit(1)

        # Step 2: add all non-title properties.
        non_title_props = {k: v for k, v in properties.items() if v.get("title") is None}
        if non_title_props:
            _, perr, pstatus = http_request(
                "PATCH", f"/data_sources/{ds_id}", token,
                {"properties": non_title_props},
            )
            if perr:
                print(json.dumps({
                    "error": "data_source_properties_apply_failed",
                    "status": pstatus, "detail": perr,
                    "db_id": db_id, "ds_id": ds_id,
                }), file=sys.stderr)
                sys.exit(1)

    print(json.dumps({
        "ok": True,
        "id": db_id,
        "data_source_id": ds_id,
        "url": body.get("url"),
        "title": args.title,
    }, indent=2))


def cmd_create_pages(args, token):
    with open(args.pages) as f:
        pages = json.load(f)
    if not isinstance(pages, list):
        print(json.dumps({"error": "pages file must be a JSON array"}), file=sys.stderr)
        sys.exit(3)

    parent = _resolve_parent(args.parent_id, args.parent_type)
    created = []
    errors = []
    for i, page in enumerate(pages):
        props_in = page.get("properties", {})
        content  = page.get("content", "")
        payload = {
            "parent": parent,
            "properties": pack_properties(props_in),
        }
        if content:
            payload["children"] = render_content_to_blocks(content)
        body, err, status = http_request("POST", "/pages", token, payload)
        if err:
            errors.append({"index": i, "error": err, "status": status, "body": body})
            continue
        created.append({
            "id": body.get("id"),
            "url": body.get("url"),
            "properties_summary": _summarise_properties(body.get("properties") or {}),
        })

    out = {"created": created, "errors": errors, "count": len(created)}
    print(json.dumps(out, indent=2))
    if errors and not created:
        sys.exit(1)


def _resolve_parent(parent_id: str, parent_type: str) -> dict:
    if parent_type == "page":
        return {"page_id": parent_id}
    if parent_type == "database":
        # Notion-Version 2025-09-03 still accepts {database_id} parents on
        # /pages and auto-resolves to the primary data source; we keep this
        # path so existing orchestrators can pass a database ID directly.
        return {"database_id": parent_id}
    if parent_type == "data_source":
        # 2025-09-03 made data_source_id a first-class parent type. MCP-side
        # tooling already exposes data_source IDs; this lets api_token mode
        # consume them too without round-tripping through the parent DB.
        return {"data_source_id": parent_id}
    raise ValueError(f"unsupported parent_type: {parent_type}")


def _summarise_properties(props: dict) -> dict:
    """Compact summary of a Notion page's properties.

    Includes rich_text / checkbox / multi_select / select fields — feedback-recycle
    needs Feedback Comment (rich_text), Key Factors (rich_text), Recycled
    (checkbox), Match Quality (select), so the summary preserves them all.
    """
    summary = {}
    for name, val in props.items():
        t = val.get("type")
        if t == "title":
            rt = val.get("title", [])
            summary[name] = "".join(e.get("text", {}).get("content", "") for e in rt)[:80]
        elif t == "number":
            summary[name] = val.get("number")
        elif t == "select":
            sel = val.get("select")
            summary[name] = sel.get("name") if sel else None
        elif t == "url":
            summary[name] = val.get("url")
        elif t == "date":
            d = val.get("date")
            summary[name] = (d or {}).get("start")
        elif t == "rich_text":
            rt = val.get("rich_text", [])
            summary[name] = "".join(e.get("text", {}).get("content", "") for e in rt)
        elif t == "checkbox":
            summary[name] = val.get("checkbox")
        elif t == "multi_select":
            summary[name] = [s.get("name") for s in val.get("multi_select", [])]
        elif t == "status":
            sel = val.get("status")
            summary[name] = sel.get("name") if sel else None
    return summary


def cmd_update_page(args, token):
    payload: dict = {}
    if args.properties:
        with open(args.properties) as f:
            props_in = json.load(f)
        payload["properties"] = pack_properties(props_in)
    if args.archive:
        payload["archived"] = True
    # `--replace-content` is a content-only update (delete children + append
    # new blocks) that doesn't need a PATCH /pages call at all. Accept any of
    # the three update modes; skip the PATCH if only --replace-content was set.
    if not payload and not args.replace_content:
        print(json.dumps({"error": "nothing to update — pass --properties, --replace-content, or --archive"}), file=sys.stderr)
        sys.exit(3)

    if payload:
        body, err, status = http_request("PATCH", f"/pages/{args.page_id}", token, payload)
        if err:
            print(json.dumps({"error": err, "status": status, "body": body}), file=sys.stderr)
            sys.exit(1)
    else:
        body, err, status = None, None, None

    # If --replace-content is set, delete existing children + append new
    if args.replace_content:
        with open(args.replace_content) as f:
            new_content = f.read()
        new_blocks = render_content_to_blocks(new_content)
        # 1) List existing children, delete each
        cb, cerr, _ = http_request("GET", f"/blocks/{args.page_id}/children?page_size=100", token)
        if cerr:
            print(json.dumps({"error": "fetch_children_failed", "detail": cerr}), file=sys.stderr)
            sys.exit(1)
        for child in (cb or {}).get("results", []):
            child_id = child.get("id")
            if child_id:
                http_request("DELETE", f"/blocks/{child_id}", token)
        # 2) Append new
        if new_blocks:
            ab, aerr, _ = http_request("PATCH", f"/blocks/{args.page_id}/children", token, {"children": new_blocks})
            if aerr:
                print(json.dumps({"error": "append_children_failed", "detail": aerr}), file=sys.stderr)
                sys.exit(1)

    # body is None when only --replace-content was requested (no PATCH made).
    # Fall back to args.page_id + sensible defaults.
    print(json.dumps({
        "ok": True,
        "id": (body or {}).get("id", args.page_id),
        "archived": bool((body or {}).get("archived", False)),
    }, indent=2))


def cmd_fetch_page(args, token):
    body, err, status = http_request("GET", f"/pages/{args.page_id}", token)
    if err:
        print(json.dumps({"error": err, "status": status, "body": body}), file=sys.stderr)
        sys.exit(1)
    out = {
        "id":         body.get("id"),
        "url":        body.get("url"),
        "archived":   body.get("archived"),
        "properties": _summarise_properties(body.get("properties") or {}),
    }
    if args.include_body:
        cb, cerr, _ = http_request("GET", f"/blocks/{args.page_id}/children?page_size=100", token)
        if cerr:
            out["body_error"] = cerr
        else:
            out["body_text"] = extract_code_block_text(cb) or ""
    print(json.dumps(out, indent=2))


def cmd_fetch_page_body(args, token):
    cb, err, status = http_request("GET", f"/blocks/{args.page_id}/children?page_size=100", token)
    if err:
        print(json.dumps({"error": err, "status": status}), file=sys.stderr)
        sys.exit(1)
    text = extract_code_block_text(cb) or ""
    print(json.dumps({"id": args.page_id, "body_text": text, "block_count": len(cb.get("results", []))}))


def _resolve_data_source_ids(database_id: str, token: str) -> tuple[list, Optional[str]]:
    """Resolve the data_source IDs for a database (Notion-Version 2025-09-03+).

    Every database now exposes a `data_sources: [{id, name}]` array — even
    single-source DBs, which get one auto-generated source. Multi-source DBs
    can have several (Notion-side migration outcome or explicit user setup).

    Returns (list_of_data_source_ids, None) on success — at least one ID
    when the DB exists. Returns ([], error_str) when retrieval fails or
    the response shape is missing data_sources (older DB pre-migration).
    """
    body, err, _ = http_request("GET", f"/databases/{database_id}", token)
    if err:
        return [], f"retrieve_database:{err}"
    data_sources = body.get("data_sources") or []
    ds_ids = [ds.get("id") for ds in data_sources if ds.get("id")]
    if not ds_ids:
        return [], "no_data_sources"
    return ds_ids, None


def _query_paginated(endpoint: str, token: str, base_payload: dict, limit: Optional[int] = None) -> tuple[list, Optional[str], int]:
    """Drain pagination at `endpoint` (POST). Returns (rows, error_str, last_status).
    Honours `limit` as an early-exit (the caller will slice precisely afterwards).
    """
    rows: list = []
    cursor = None
    while True:
        payload = dict(base_payload)
        if cursor:
            payload["start_cursor"] = cursor
        body, err, status = http_request("POST", endpoint, token, payload)
        if err:
            return rows, err, status
        rows.extend(body.get("results", []))
        if not body.get("has_more"):
            break
        cursor = body.get("next_cursor")
        if limit and len(rows) >= limit:
            break
    return rows, None, 200


def cmd_query_database(args, token):
    payload: dict = {"page_size": args.page_size}
    if args.filter:
        with open(args.filter) as f:
            payload["filter"] = json.load(f)

    # 2025-09-03: query via /data_sources/{ds_id}/query, not /databases/{id}/query.
    # Multi-source DBs (Notion's migration may have promoted single-source DBs
    # to the new format; some users explicitly group multiple sources) need
    # each source queried separately; combine results before slicing to limit.
    ds_ids, err = _resolve_data_source_ids(args.database_id, token)
    if err:
        # Fallback to legacy endpoint — older DBs that pre-date the migration
        # still answer the database-level query. If this also fails, surface.
        rows, qerr, status = _query_paginated(
            f"/databases/{args.database_id}/query", token, payload, args.limit
        )
        if qerr:
            print(json.dumps({
                "error": qerr,
                "status": status,
                "stage": "query_database_legacy",
                "resolve_data_sources_error": err,
            }), file=sys.stderr)
            sys.exit(1)
        all_results = rows
    else:
        all_results: list = []
        for ds_id in ds_ids:
            rows, qerr, status = _query_paginated(
                f"/data_sources/{ds_id}/query", token, payload,
                # Subtract already-collected so per-source pagination short-circuits
                # once we've hit the user-supplied limit across sources.
                (args.limit - len(all_results)) if args.limit else None,
            )
            if qerr:
                print(json.dumps({
                    "error": qerr,
                    "status": status,
                    "stage": "query_data_source",
                    "data_source_id": ds_id,
                }), file=sys.stderr)
                sys.exit(1)
            all_results.extend(rows)
            if args.limit and len(all_results) >= args.limit:
                break

    sliced = all_results[: args.limit] if args.limit else all_results
    out = [{
        "id":         r.get("id"),
        "url":        r.get("url"),
        "title":      _extract_title(r),
        "properties": _summarise_properties(r.get("properties") or {}),
    } for r in sliced]
    print(json.dumps({"results": out, "count": len(out)}, indent=2))


def cmd_delete_page(args, token):
    body, err, status = http_request("PATCH", f"/pages/{args.page_id}", token, {"archived": True})
    if err:
        print(json.dumps({"error": err, "status": status, "body": body}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"ok": True, "id": body.get("id"), "archived": True}))


def cmd_hydrate_state(args, token):
    """Query the state DB, fetch every row's body in parallel, assemble JSON.

    This replaces the orchestrator's sequential-fetch pre-flight loop in
    cloud mode. For 50 companies, it cuts hydration from ~5 minutes (50
    serial MCP calls) to ~5 seconds (parallel HTTPS).
    """
    # 1) Query the database for all rows. Per Notion-Version 2025-09-03,
    # databases expose a data_sources array; query each source separately
    # (single-source DBs return one ID and this is one iteration).
    rows: list = []
    ds_ids, err = _resolve_data_source_ids(args.database_id, token)
    if err:
        # Pre-migration DB — fall back to legacy endpoint.
        legacy_rows, qerr, status = _query_paginated(
            f"/databases/{args.database_id}/query", token, {"page_size": 100}
        )
        if qerr:
            print(json.dumps({
                "error": qerr, "status": status,
                "stage": "query_database_legacy",
                "resolve_data_sources_error": err,
            }), file=sys.stderr)
            sys.exit(1)
        rows = legacy_rows
    else:
        for ds_id in ds_ids:
            src_rows, qerr, status = _query_paginated(
                f"/data_sources/{ds_id}/query", token, {"page_size": 100}
            )
            if qerr:
                print(json.dumps({
                    "error": qerr, "status": status,
                    "stage": "query_data_source",
                    "data_source_id": ds_id,
                }), file=sys.stderr)
                sys.exit(1)
            rows.extend(src_rows)

    # 2) For each row, parallel-fetch the body (children blocks)
    state = {}
    fetch_errors = []

    def fetch_body(row):
        page_id = row.get("id")
        # Extract company key (title) and last_checked from properties
        props = row.get("properties") or {}
        title_prop = next((p for p in props.values() if p.get("type") == "title"), {})
        rt = title_prop.get("title", []) if title_prop else []
        company_key = "".join(e.get("text", {}).get("content", "") for e in rt)
        last_checked_prop = props.get("Last checked", {})
        last_checked = (last_checked_prop.get("date") or {}).get("start") if last_checked_prop else None
        # Fetch body
        cb, cerr, _ = http_request("GET", f"/blocks/{page_id}/children?page_size=100", token)
        if cerr:
            return company_key, None, f"body_fetch:{cerr}"
        body_text = extract_code_block_text(cb) or "[]"
        # Strip ```json ... ``` fence if it slipped through
        bt = body_text.strip()
        if bt.startswith("```"):
            lines = bt.split("\n")
            bt = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        try:
            job_ids = json.loads(bt)
        except Exception as e:
            return company_key, None, f"body_parse:{e}"
        return company_key, {"job_ids": job_ids, "last_checked": last_checked}, None

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futs = [ex.submit(fetch_body, r) for r in rows]
        for fut in as_completed(futs):
            ck, data, err = fut.result()
            if err:
                fetch_errors.append({"company_key": ck, "error": err})
            elif ck:
                state[ck] = data

    # 3) Assemble the same shape the script expects in /tmp/ai50-state.json
    assembled = {}
    for company_key, data in state.items():
        if company_key == "_meta":
            continue
        assembled[company_key] = {
            "last_checked": data.get("last_checked"),
            "company_name": company_key,  # best-effort; the user can store a friendlier name
            "jobs": {jid: {} for jid in data.get("job_ids", [])},
        }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(assembled, f, indent=2)
    print(json.dumps({
        "ok": True,
        "rows_queried": len(rows),
        "rows_assembled": len(assembled),
        "fetch_errors": fetch_errors,
        "output": args.output,
    }, indent=2))


# ── Discover ──────────────────────────────────────────────────────────────────

# Internal artifact key → (cache field name, Notion object kind, recreate policy)
# - kind: "page" or "database" — drives which API endpoint validates a cached ID
# - policy: "recreate_ok" → orchestrator may rebuild the empty shell
#           "abort_if_missing" → has user JSON content; never auto-recreate
DISCOVER_SPEC = {
    "parent_page":             ("parent_page_id",             "page",     "recreate_ok"),
    "tracker_db":              ("tracker_database_id",        "database", "recreate_ok"),
    "hot_list_page":           ("hot_list_parent_page_id",    "page",     "recreate_ok"),
    "state_db":                ("tracker_state_database_id",  "database", "recreate_ok"),
    "run_log_db":              ("run_log_database_id",        "database", "recreate_ok"),
    "profile_page":            ("profile_page_id",            "page",     "abort_if_missing"),
    "extended_companies_page": ("extended_companies_page_id", "page",     "abort_if_missing"),
}

DISCOVER_KEYS = {k: v[0] for k, v in DISCOVER_SPEC.items()}


def _block_title(child: dict, ctype: str) -> str:
    """Extract a title from a child_page or child_database block. Strips whitespace
    so user renames with stray spaces / NBSP still match."""
    raw = (child.get(ctype) or {}).get("title", "") or ""
    return raw.strip()


def _norm(s: str) -> str:
    """Normalise a title for comparison: strip + casefold (Notion search is
    case-insensitive; users habitually rename with case drift)."""
    return (s or "").strip().casefold()


def _now_utc_iso() -> str:
    """Timestamp suitable for `_resolved_at` — ISO8601, second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: str, data: dict) -> None:
    """Write JSON atomically: tmp file in same dir, then os.replace.
    Creates the parent directory if it doesn't exist."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cache.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass
        raise


def _verify_cached_id(token: str, cached_id: str, kind: str) -> tuple[str, Optional[dict]]:
    """Return ("ok"|"missing"|"no_access"|"transient", body|None).
    - ok: 2xx and not archived
    - missing: 404 (deleted) or 2xx archived
    - no_access: 401/403 (token lost permission)
    - transient: 429/5xx, network errors (status 0), URL errors — DO NOT poison
                 cache; treat as cached-still-OK and let the actual call surface
                 the failure rather than silently dropping the ID
    """
    endpoint = f"/{'pages' if kind == 'page' else 'databases'}/{cached_id}"
    body, err, status = http_request("GET", endpoint, token)
    if err is None and not (body or {}).get("archived"):
        return ("ok", body)
    if err is None and (body or {}).get("archived"):
        return ("missing", None)  # 2xx but archived
    if status == 404:
        return ("missing", None)
    if status in (401, 403):
        return ("no_access", None)
    # Anything else — 429, 5xx, status 0 (network error), or unexpected 4xx —
    # is transient from the cache's POV. Don't drop the ID.
    return ("transient", None)


def _list_all_children(token: str, parent_id: str) -> tuple[list, Optional[str]]:
    """Paginated listing of /blocks/{parent}/children. Returns (results, error).
    Workspaces with >100 blocks above the artifact children would silently miss
    them otherwise."""
    out: list = []
    cursor: Optional[str] = None
    while True:
        path = f"/blocks/{parent_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        body, err, _ = http_request("GET", path, token)
        if err:
            return out, err
        out.extend((body or {}).get("results", []))
        if not (body or {}).get("has_more"):
            return out, None
        cursor = (body or {}).get("next_cursor")
        if not cursor:
            return out, None


def cmd_discover(args, token):
    # ── Read config ──
    cfg_path = args.config
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(json.dumps({"error": "config_not_found", "path": cfg_path}), file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": "config_parse_failed", "path": cfg_path, "detail": str(e)}), file=sys.stderr)
        sys.exit(2)

    names = (cfg.get("notion") or {}).get("names") or {}
    expected_keys = list(DISCOVER_KEYS.keys())
    missing_names = [k for k in expected_keys if not names.get(k)]
    if missing_names:
        print(json.dumps({
            "error": "names_missing",
            "missing": missing_names,
            "config": cfg_path,
            "hint": "Add notion.names.{parent_page,tracker_db,hot_list_page,state_db,profile_page,extended_companies_page} to connectors.json.",
        }), file=sys.stderr)
        sys.exit(3)

    # ── Workspace identity (sanity-check token vs cache) ──
    ws_body, ws_err, ws_status = http_request("GET", "/users/me", token)
    if ws_err:
        if ws_status in (401, 403):
            print(json.dumps({"error": "auth_failed", "status": ws_status, "body": ws_body}), file=sys.stderr)
            sys.exit(2)
        # transient — abort with clear status
        print(json.dumps({"error": "users_me_failed", "status": ws_status, "detail": ws_err}), file=sys.stderr)
        sys.exit(1)
    workspace_name = ((ws_body or {}).get("bot") or {}).get("workspace_name", "")
    workspace_id = ((ws_body or {}).get("bot") or {}).get("workspace_id", "")

    # ── Initialise result map ──
    result = {k: {"name": names[k], "id": None, "status": "missing"} for k in expected_keys}

    # ── Read cache (preserve unknown keys; invalidate if workspace mismatched) ──
    cached: dict = {}
    if args.cache_file and os.path.exists(args.cache_file):
        try:
            with open(args.cache_file) as f:
                cached = json.load(f) or {}
        except Exception:
            cached = {}
    cached_ws = cached.get("_workspace_id")
    workspace_changed = bool(cached_ws and workspace_id and cached_ws != workspace_id)
    if workspace_changed:
        # Token now points at a different workspace; ignore cached IDs entirely
        print(json.dumps({
            "warning": "workspace_changed",
            "cached_workspace": cached_ws,
            "current_workspace": workspace_id,
            "detail": "Token's workspace differs from cached workspace; cache invalidated for this run.",
        }), file=sys.stderr)
        cached = {}

    # ── Step 1: verify cached IDs ──
    for key in expected_keys:
        cached_id = cached.get(DISCOVER_KEYS[key])
        if not cached_id:
            continue
        kind = DISCOVER_SPEC[key][1]
        verdict, _ = _verify_cached_id(token, cached_id, kind)
        if verdict == "ok":
            result[key]["id"] = cached_id
            result[key]["status"] = "cached"
        elif verdict == "no_access":
            result[key]["id"] = cached_id  # keep ID for diagnostic; orchestrator reads no_access status
            result[key]["status"] = "no_access"
        elif verdict == "transient":
            # Don't poison the cache — assume the cached ID is still good and
            # mark as cached. Orchestrator will hit the same 5xx on the actual
            # call and surface it; we don't compound the failure here.
            result[key]["id"] = cached_id
            result[key]["status"] = "cached"
        # verdict == "missing" → leave status="missing" so we fall through

    # ── Step 2: find parent via search if not cached ──
    if result["parent_page"]["status"] == "missing":
        sb, serr, sstatus = http_request("POST", "/search", token, {
            "query": names["parent_page"],
            "filter": {"property": "object", "value": "page"},
            "page_size": 25,
        })
        if serr is None:
            target = _norm(names["parent_page"])
            matches: list[str] = []
            for hit in (sb or {}).get("results", []):
                if hit.get("archived"):
                    continue
                if _norm(_extract_title(hit)) == target:
                    matches.append(hit.get("id"))
            if len(matches) == 1:
                result["parent_page"]["id"] = matches[0]
                result["parent_page"]["status"] = "discovered"
            elif len(matches) > 1:
                # Don't auto-pick — for an `abort_if_missing` pivot like the
                # parent we need a deterministic answer. Surface ambiguity.
                result["parent_page"]["status"] = "ambiguous"
                result["parent_page"]["candidates"] = matches
        elif sstatus in (401, 403):
            print(json.dumps({"error": "search_auth_failed", "status": sstatus}), file=sys.stderr)
            sys.exit(2)

    parent_id = result["parent_page"]["id"] if result["parent_page"]["status"] in ("cached", "discovered") else None

    # ── Step 3: list parent's children (paginated) and match by title ──
    if parent_id:
        children, cerr = _list_all_children(token, parent_id)
        if cerr is None:
            # Build candidate-list maps so we can detect collisions before binding
            page_candidates: dict[str, list[str]] = {}    # name → [block_id]
            db_candidates:   dict[str, list[str]] = {}
            for child in children:
                ctype = child.get("type")
                cid = child.get("id")
                if not cid:
                    continue
                if ctype == "child_page":
                    page_candidates.setdefault(_norm(_block_title(child, "child_page")), []).append(cid)
                elif ctype == "child_database":
                    db_candidates.setdefault(_norm(_block_title(child, "child_database")), []).append(cid)

            for k, candidates in (
                ("hot_list_page",           page_candidates),
                ("profile_page",            page_candidates),
                ("extended_companies_page", page_candidates),
                ("tracker_db",              db_candidates),
                ("state_db",                db_candidates),
            ):
                if result[k]["status"] != "missing":
                    continue
                hits = candidates.get(_norm(names[k]), [])
                if len(hits) == 1:
                    result[k]["id"] = hits[0]
                    result[k]["status"] = "discovered"
                elif len(hits) > 1:
                    result[k]["status"] = "ambiguous"
                    result[k]["candidates"] = hits

    # ── Step 4: write cache (atomic, preserves unknown keys) ──
    # Preserve IDs whose status is cached/discovered/no_access. The no_access case
    # means we couldn't verify the ID this run (token lost permission), but the
    # ID itself is still likely valid — keep it so a future run with re-granted
    # permission resolves on the fast path instead of forcing re-discovery.
    if args.cache_file:
        cache_out = {**cached}  # preserve unknown user-added keys
        for k in expected_keys:
            field = DISCOVER_KEYS[k]
            if result[k]["id"] and result[k]["status"] in ("cached", "discovered", "no_access"):
                cache_out[field] = result[k]["id"]
            else:
                cache_out.pop(field, None)
        cache_out["_resolved_at"] = _now_utc_iso()
        cache_out["_workspace_id"] = workspace_id
        cache_out["_workspace_name"] = workspace_name
        try:
            _atomic_write_json(args.cache_file, cache_out)
        except Exception as e:
            print(json.dumps({"warning": "cache_write_failed", "detail": str(e), "path": args.cache_file}), file=sys.stderr)

    # ── Step 5: report ──
    # `ok` = pipeline can proceed without orchestrator intervention. False if:
    #   - parent missing (nothing to anchor on);
    #   - any ambiguous match (don't auto-pick);
    #   - any abort_if_missing artifact unresolved (orchestrator must abort
    #     anyway — this signals it without forcing a parse of `result`).
    has_ambiguous = any(v["status"] == "ambiguous" for v in result.values())
    blocking_missing = [
        k for k, v in result.items()
        if DISCOVER_SPEC[k][2] == "abort_if_missing" and v["status"] in ("missing", "no_access")
    ]
    ok = bool(parent_id) and not has_ambiguous and not blocking_missing

    summary = {
        "ok": ok,
        "workspace": {"id": workspace_id, "name": workspace_name},
        "result": {
            k: {**v, "kind": DISCOVER_SPEC[k][1], "recreate_policy": DISCOVER_SPEC[k][2]}
            for k, v in result.items()
        },
        "summary": {
            "cached":     [k for k, v in result.items() if v["status"] == "cached"],
            "discovered": [k for k, v in result.items() if v["status"] == "discovered"],
            "missing":    [k for k, v in result.items() if v["status"] == "missing"],
            "no_access":  [k for k, v in result.items() if v["status"] == "no_access"],
            "ambiguous":  [k for k, v in result.items() if v["status"] == "ambiguous"],
        },
    }
    if not parent_id:
        summary["error"] = "parent_not_found"
        summary["hint"] = (
            f"Parent page '{names['parent_page']}' not found in workspace "
            f"'{workspace_name}'. Either re-run setup, restore the parent page "
            "in Notion, or set NOTION_PARENT_ANCHOR_ID to recreate under a known anchor."
        )
    elif has_ambiguous:
        summary["error"] = "ambiguous_match"
        summary["hint"] = (
            "Multiple Notion artifacts match by name — pick distinct names in "
            "config/connectors.json[notion.names], or archive the duplicates."
        )
    elif blocking_missing:
        summary["error"] = "user_content_missing"
        summary["hint"] = (
            f"User-content artifacts missing or inaccessible: {blocking_missing}. "
            "These hold your profile/extended-companies JSON and are NEVER auto-recreated. "
            "Re-run 'set up the plugin' to recreate from scratch."
        )

    print(json.dumps(summary, indent=2))
    if not ok:
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Shared parent parser so --token works either before OR after the
    # subcommand name. Argparse with sub_parsers normally requires parent-
    # parser flags to come first, which is unintuitive when the help text
    # shows examples like `notion-api.py users-me --token <X>`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--token", help="Notion API token (overrides env + file).")

    p = argparse.ArgumentParser(prog="notion-api.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("users-me", parents=[common], help="Verify token; print integration name + workspace.")

    sp = sub.add_parser("search", parents=[common], help="Find a page or database by query.")
    sp.add_argument("--query", required=True)
    sp.add_argument("--type", choices=["page", "database"], default=None)
    sp.add_argument("--limit", type=int, default=10)

    sp = sub.add_parser("create-database", parents=[common], help="Create a database under a parent page.")
    sp.add_argument("--parent-page-id", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--schema", required=True, help="Path to JSON file with Notion property schema.")

    sp = sub.add_parser("create-pages", parents=[common], help="Create one or more pages from a JSON file.")
    sp.add_argument("--pages", required=True, help="JSON file: array of {properties, content}.")
    sp.add_argument("--parent-id", required=True)
    sp.add_argument("--parent-type", required=True, choices=["page", "database", "data_source"])

    sp = sub.add_parser("update-page", parents=[common], help="Update a page's properties and/or replace its body.")
    sp.add_argument("--page-id", required=True)
    sp.add_argument("--properties", help="JSON file: flat {name: value} dict.")
    sp.add_argument("--replace-content", help="File: new body content (markdown-ish; supports ```code``` fences).")
    sp.add_argument("--archive", action="store_true", help="Archive (soft-delete) the page.")

    sp = sub.add_parser("fetch-page", parents=[common], help="Fetch a page's properties (and optionally body).")
    sp.add_argument("--page-id", required=True)
    sp.add_argument("--include-body", action="store_true")

    sp = sub.add_parser("fetch-page-body", parents=[common], help="Fetch a page's body text (concatenated code-block content).")
    sp.add_argument("--page-id", required=True)

    sp = sub.add_parser("query-database", parents=[common], help="Query a database with optional filter; paginated.")
    sp.add_argument("--database-id", required=True)
    sp.add_argument("--filter", help="JSON file: Notion filter object.")
    sp.add_argument("--page-size", type=int, default=100)
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("delete-page", parents=[common], help="Archive (soft-delete) a page.")
    sp.add_argument("--page-id", required=True)

    sp = sub.add_parser("hydrate-state", parents=[common], help="Query state DB, parallel-fetch all bodies, assemble state JSON.")
    sp.add_argument("--database-id", required=True)
    sp.add_argument("--output", required=True, help="Output path (e.g. /tmp/ai50-state.json).")
    sp.add_argument("--max-workers", type=int, default=10)

    sp = sub.add_parser("discover", parents=[common], help="Resolve Notion artifact IDs from names + cache; self-healing.")
    sp.add_argument("--config", required=True, help="Path to connectors.json (reads notion.names).")
    sp.add_argument("--cache-file", help="Path to cached-ids.json (read + write). Optional; if omitted, every run re-discovers.")

    args = p.parse_args()
    token = resolve_token(args.token)
    if not token:
        auth_error_and_exit()

    handlers = {
        "users-me":        cmd_users_me,
        "search":          cmd_search,
        "create-database": cmd_create_database,
        "create-pages":    cmd_create_pages,
        "update-page":     cmd_update_page,
        "fetch-page":      cmd_fetch_page,
        "fetch-page-body": cmd_fetch_page_body,
        "query-database":  cmd_query_database,
        "delete-page":     cmd_delete_page,
        "hydrate-state":   cmd_hydrate_state,
        "discover":        cmd_discover,
    }
    handlers[args.cmd](args, token)


if __name__ == "__main__":
    main()
