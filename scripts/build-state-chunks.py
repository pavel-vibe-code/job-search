#!/usr/bin/env python3
"""Convert /tmp/ai50-state.json into small per-chunk page payloads on disk.

Deterministic, agent-free first half of Pass 4 (state-DB persist). The
orchestrator runs this script, then loops over the produced chunk files and
issues one `notion-create-pages` (or `notion-update-page`) call per chunk.
Keeping the chunks on disk means the agent's transcript never carries the
full JSON content — it only carries file paths.

## Adaptive chunking (v2.2.2)

Earlier versions used a fixed `--chunk-size` (default 5). That broke for
companies with > ~300 jobs (Databricks 829, OpenAI 651, Anthropic 453):
their per-row body alone exceeded the Read tool's 25k-token limit, so the
orchestrator couldn't read the chunk file to dispatch the call.

This version chunks adaptively:
  • Companies with len(job_ids) > `--big-row-threshold` (default 200) get
    their OWN chunk (chunk-size = 1 effective). They're heavy enough that
    grouping them with anything else risks exceeding tool limits.
  • Smaller companies are grouped in batches of `--small-chunk-size`
    (default 5).

Manifest reports `kind: "big" | "small"` per chunk so the orchestrator can
route big chunks through a higher-rate-limit path if needed.

Usage:
    python3 build-state-chunks.py --state-file /tmp/ai50-state.json \\
        --output-dir /tmp/state-chunks \\
        --small-chunk-size 5 --big-row-threshold 200 \\
        --date 2026-04-28
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date


def build_chunks(
    state_path: str,
    output_dir: str,
    small_chunk_size: int,
    big_row_threshold: int,
    run_date: str,
) -> dict:
    with open(state_path) as f:
        state = json.load(f)

    keys = sorted(k for k in state.keys() if k != "_meta")
    rows: list[tuple[str, int, dict]] = []  # (key, job_count, payload)
    for k in keys:
        company_state = state.get(k, {})
        if not isinstance(company_state, dict):
            continue
        jobs = company_state.get("jobs", {})
        if not isinstance(jobs, dict):
            jobs = {}
        job_ids = list(jobs.keys())
        body = "```json\n" + json.dumps(job_ids) + "\n```"
        # "Company key" is the state DB's TITLE column — its value must be in
        # Notion's nested {"title": [...]} shape. notion-api.py's
        # pack_properties() heuristic only auto-treats keys named Title/Name as
        # titles, so we pre-pack here to be explicit. The MCP-tool path
        # accepts plain-string title values, so this nested form is also a
        # tighter contract that works on both transports (the MCP path's
        # passthrough handles dicts that already look like Notion property
        # objects).
        payload = {
            "properties": {
                "Company key": {"title": [{"type": "text", "text": {"content": k}}]},
                "date:Last checked:start": run_date,
                "Job count": len(job_ids),
            },
            "content": body,
        }
        rows.append((k, len(job_ids), payload))

    # Split rows into "big" (> threshold) and "small" buckets, preserving order.
    chunks: list[dict] = []
    small_buffer: list[dict] = []

    def flush_small():
        nonlocal small_buffer
        if small_buffer:
            chunks.append({"kind": "small", "rows": small_buffer})
            small_buffer = []

    for key, n, payload in rows:
        if n > big_row_threshold:
            # Flush any pending small batch first to preserve sort order
            flush_small()
            chunks.append({"kind": "big", "rows": [payload]})
        else:
            small_buffer.append(payload)
            if len(small_buffer) >= small_chunk_size:
                flush_small()
    flush_small()

    os.makedirs(output_dir, exist_ok=True)
    manifest = {
        "total_rows": len(rows),
        "small_chunk_size": small_chunk_size,
        "big_row_threshold": big_row_threshold,
        "chunk_count": len(chunks),
        "big_chunks": sum(1 for c in chunks if c["kind"] == "big"),
        "small_chunks": sum(1 for c in chunks if c["kind"] == "small"),
        "run_date": run_date,
        "chunks": [],
    }
    for i, chunk in enumerate(chunks):
        path = os.path.join(output_dir, f"chunk-{i}.json")
        with open(path, "w") as f:
            json.dump(chunk["rows"], f)
        size_bytes = os.path.getsize(path)
        manifest["chunks"].append({
            "index": i,
            "kind": chunk["kind"],
            "path": path,
            "rows": len(chunk["rows"]),
            "bytes": size_bytes,
            "company_keys": [r["properties"]["Company key"] for r in chunk["rows"]],
            "max_job_count": max((r["properties"]["Job count"] for r in chunk["rows"]), default=0),
        })

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    manifest["manifest_path"] = manifest_path
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state-file", required=True, help="Path to /tmp/ai50-state.json")
    p.add_argument("--output-dir", default="/tmp/state-chunks", help="Where to write chunk-*.json + manifest.json")
    p.add_argument("--small-chunk-size", type=int, default=5,
                   help="Rows per chunk for companies with <= big-row-threshold jobs. Default 5.")
    p.add_argument("--big-row-threshold", type=int, default=200,
                   help="Companies with > N jobs become their own (chunk-size=1) batch. Default 200.")
    # Backwards-compat alias
    p.add_argument("--chunk-size", type=int, default=None,
                   help="DEPRECATED — use --small-chunk-size. If set, used as small-chunk-size.")
    p.add_argument("--date", default=str(date.today()), help="ISO date for 'Last checked' property")
    args = p.parse_args()

    small = args.chunk_size if args.chunk_size is not None else args.small_chunk_size
    if small < 1 or small > 10:
        print("--small-chunk-size must be 1..10", file=sys.stderr)
        sys.exit(2)
    if args.big_row_threshold < 1:
        print("--big-row-threshold must be >= 1", file=sys.stderr)
        sys.exit(2)

    manifest = build_chunks(args.state_file, args.output_dir, small, args.big_row_threshold, args.date)
    summary = {
        "manifest_path": manifest["manifest_path"],
        "total_rows": manifest["total_rows"],
        "chunk_count": manifest["chunk_count"],
        "big_chunks": manifest["big_chunks"],
        "small_chunks": manifest["small_chunks"],
        "small_chunk_size": manifest["small_chunk_size"],
        "big_row_threshold": manifest["big_row_threshold"],
        "chunks": [{
            "path": c["path"], "rows": c["rows"], "kind": c["kind"],
            "max_job_count": c["max_job_count"], "bytes": c["bytes"],
        } for c in manifest["chunks"]],
    }
    json.dump(summary, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
