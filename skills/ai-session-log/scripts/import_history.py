#!/usr/bin/env python3
"""Backfill history from Codex rollouts or Claude Code transcripts.

Examples:
    python import_history.py --tool codex
    python import_history.py --tool claude --since 2026-09-01
    python import_history.py --tool codex --path ~/.codex/sessions --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
sys.dont_write_bytecode = True
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import TZ, clip, now_iso, redact_text, short_hash, Store  # noqa: E402

TAG_RE = re.compile(
    r"<(system-reminder|ide_opened_file|command-name|command-message|"
    r"local-command-stdout|local-command-stderr)>.*?</\1>",
    re.DOTALL,
)


def as_local(ts: str) -> str:
    if not ts:
        return now_iso()
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).isoformat(timespec="seconds")
    except ValueError:
        return now_iso()


def as_epoch(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), TZ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return now_iso()


def clean_prompt(text: str) -> str:
    text = TAG_RE.sub("", text or "")
    return text.strip()


def join_blocks(content, wanted="text") -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == wanted:
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------
# Codex rollouts
# --------------------------------------------------------------------------

def load_codex_titles(codex_home: Path) -> dict:
    titles = {}
    path = codex_home / "session_index.jsonl"
    if not path.is_file():
        return titles
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            sid = obj.get("id")
            title = (obj.get("thread_name") or "").strip()
            if sid and title:
                titles[sid] = title
    return titles


def parse_codex_file(path: Path):
    session_id = ""
    cwd = model = None
    turns = {}
    order = []
    current = None
    calls = {}

    def ensure(turn_id):
        nonlocal current
        if not turn_id:
            turn_id = current or f"turn-{len(turns) + 1}"
        if turn_id not in turns:
            turns[turn_id] = {
                "turn_id": turn_id, "started_at": None, "ended_at": None,
                "question": "", "answer": "", "status": "no_answer", "tools": [],
            }
            order.append(turn_id)
        current = turn_id
        return turns[turn_id]

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            typ = obj.get("type")
            payload = obj.get("payload") or {}
            ts = obj.get("timestamp")

            if typ == "session_meta":
                session_id = payload.get("session_id") or payload.get("id") or ""
                cwd = payload.get("cwd") or cwd
                continue

            if typ == "turn_context":
                if payload.get("cwd"):
                    cwd = payload["cwd"]
                ensure(payload.get("turn_id"))
                continue

            if typ == "event_msg":
                etype = payload.get("type")
                turn_id = payload.get("turn_id") or payload.get("root_turn_id")
                if etype == "task_started":
                    rec = ensure(turn_id)
                    rec["started_at"] = as_epoch(payload.get("started_at")) if payload.get("started_at") else as_local(ts)
                elif etype == "item_completed":
                    item = payload.get("item") or {}
                    if item.get("type") == "UserMessage":
                        rec = ensure(turn_id)
                        text = join_blocks(item.get("content"))
                        if text and not rec["question"]:
                            rec["question"] = redact_text(text)
                            rec.setdefault("started_at", as_local(ts))
                elif etype == "task_complete":
                    rec = ensure(turn_id)
                    rec["answer"] = redact_text(str(payload.get("last_agent_message") or ""))
                    rec["ended_at"] = as_local(ts)
                    rec["status"] = "closed" if rec["answer"].strip() else "no_answer"
                elif etype == "turn_aborted":
                    rec = ensure(turn_id)
                    rec["status"] = "no_answer"
                    rec["ended_at"] = as_local(ts)
                continue

            if typ == "response_item":
                ptype = payload.get("type")
                meta = payload.get("internal_chat_message_metadata_passthrough") or {}
                turn_id = meta.get("turn_id")
                if ptype == "function_call":
                    rec = ensure(turn_id)
                    entry = {
                        "name": payload.get("name") or "unknown",
                        "at": as_local(ts),
                        "summary": clip(str(payload.get("arguments") or ""), 400),
                        "output": "",
                    }
                    rec["tools"].append(entry)
                    if payload.get("call_id"):
                        calls[payload["call_id"]] = entry
                elif ptype == "function_call_output":
                    entry = calls.get(payload.get("call_id"))
                    if entry is not None:
                        entry["output"] = clip(str(payload.get("output") or ""), 2000)
                continue

    if not session_id:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", path.name)
        session_id = m.group(1) if m else path.stem

    records = []
    for turn_id in order:
        rec = turns[turn_id]
        if not (rec["question"].strip() or rec["answer"].strip()):
            continue
        rec["started_at"] = rec.get("started_at") or now_iso()
        records.append({
            "turn_id": turn_id,
            "session_id": session_id,
            "agent": "codex",
            "room": "private",
            "cwd": cwd,
            "model": model,
            "started_at": rec["started_at"],
            "ended_at": rec["ended_at"],
            "question": rec["question"],
            "answer": rec["answer"],
            "status": rec["status"],
            "tools": rec["tools"],
        })
    return session_id, records


def import_codex(store: Store, base: Path, since=None, limit=None, dry=False) -> int:
    codex_home = base.parent.parent if base.name == "sessions" else Path.home() / ".codex"
    titles = load_codex_titles(codex_home)
    files = sorted(base.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if since:
        files = [p for p in files if datetime.fromtimestamp(p.stat().st_mtime).date().isoformat() >= since]
    if limit:
        files = files[-limit:]

    imported = 0
    for path in files:
        try:
            session_id, records = parse_codex_file(path)
        except Exception as exc:
            print(f"  ! {path}: {exc}")
            continue
        if not records:
            continue
        if titles.get(session_id):
            store.set_session_title(session_id, titles[session_id])
        for rec in records:
            key = f"{session_id}::{rec['turn_id']}"
            if key in store.load_turnmap():
                continue
            if dry:
                imported += 1
                continue
            store.insert_record(rec, rec["turn_id"])
            imported += 1
        store.rebuild_md(session_id)
    return imported


# --------------------------------------------------------------------------
# Claude Code transcripts
# --------------------------------------------------------------------------

def parse_claude_file(path: Path):
    session_id = path.stem
    title = ""
    turns = []
    current = None
    calls = {}

    def finish():
        nonlocal current
        if current is None:
            return
        if not current["question"].strip():
            current = None
            return
        current["status"] = "closed" if current["answer"].strip() else "no_answer"
        turns.append(current)
        current = None

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            typ = obj.get("type")
            ts = obj.get("timestamp") or ""

            if typ == "ai-title":
                title = (obj.get("aiTitle") or "").strip() or title
                continue
            if typ not in ("user", "assistant"):
                continue
            # Meta / compaction / sidechain entries are context injection, not user turns.
            if (obj.get("isMeta") or obj.get("isCompactSummary")
                    or obj.get("isVisibleInTranscriptOnly") or obj.get("isSidechain")):
                continue

            msg = obj.get("message") or {}
            role = msg.get("role")
            content = msg.get("content")
            blocks = content if isinstance(content, list) else []

            if role == "user":
                texts, tool_results = [], []
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        tool_results.append(b)
                    elif b.get("type") == "text":
                        texts.append(str(b.get("text", "")))
                if tool_results:
                    for b in tool_results:
                        entry = calls.get(b.get("tool_use_id"))
                        if entry is not None:
                            raw = b.get("content")
                            entry["output"] = clip(
                                raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                                2000,
                            )
                prompt = clean_prompt("\n".join(texts))
                if prompt.startswith(("<task-notification>", "<command-name>", "<command-message>",
                                      "<system-reminder>", "<local-command")):
                    prompt = ""
                if prompt:
                    finish()
                    current = {
                        "question": redact_text(prompt),
                        "answer": "",
                        "started_at": as_local(ts),
                        "ended_at": None,
                        "tools": [],
                    }
                continue

            if role == "assistant":
                if current is None:
                    continue
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        text = str(b.get("text", "")).strip()
                        if text:
                            current["answer"] = (current["answer"] + "\n" + text).strip()
                    elif b.get("type") == "tool_use":
                        entry = {
                            "name": b.get("name") or "unknown",
                            "at": as_local(ts),
                            "summary": clip(json.dumps(b.get("input"), ensure_ascii=False), 400),
                            "output": "",
                        }
                        current["tools"].append(entry)
                        if b.get("id"):
                            calls[b["id"]] = entry
                current["ended_at"] = as_local(ts) if ts else current.get("ended_at")
                continue

    finish()

    records = []
    for rec in turns:
        rec = dict(rec)
        rec["answer"] = redact_text(rec.get("answer") or "")
        rec["session_id"] = session_id
        rec["agent"] = "claude"
        rec["room"] = "private"
        rec["cwd"] = None
        rec["model"] = None
        rec["turn_id"] = "claude-" + short_hash(f"{rec['started_at']}|{rec['question'][:200]}", 16)
        rec["ended_at"] = rec.get("ended_at") or rec.get("started_at")
        records.append(rec)
    return session_id, title, records


def import_claude(store: Store, base: Path, since=None, limit=None, dry=False) -> int:
    files = sorted(
        [p for p in base.rglob("*.jsonl") if "subagents" not in p.parts],
        key=lambda p: p.stat().st_mtime,
    )
    if since:
        files = [p for p in files if datetime.fromtimestamp(p.stat().st_mtime).date().isoformat() >= since]
    if limit:
        files = files[-limit:]

    imported = 0
    for path in files:
        try:
            session_id, title, records = parse_claude_file(path)
        except Exception as exc:
            print(f"  ! {path}: {exc}")
            continue
        if not records:
            continue
        if title:
            store.set_session_title(session_id, title)
        existing = store.session_records(session_id)
        fingerprints = {(r.get("started_at", "")[:19], (r.get("question") or "")[:80]) for r in existing}
        for rec in records:
            fp = (rec.get("started_at", "")[:19], (rec.get("question") or "")[:80])
            if fp in fingerprints:
                continue
            if dry:
                imported += 1
                continue
            store.insert_record(rec, rec["turn_id"])
            fingerprints.add(fp)
            imported += 1
        store.rebuild_md(session_id)
    return imported


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Import existing AI transcripts into ai-session-log")
    ap.add_argument("--tool", choices=["codex", "claude"], required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--path", default=None, help="transcript root (defaults to ~/.codex/sessions or ~/.claude/projects)")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD lower bound on file mtime")
    ap.add_argument("--limit", type=int, default=None, help="import only the N most recent files")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = Store(args.root).ensure()
    if args.tool == "codex":
        base = Path(args.path).expanduser() if args.path else Path.home() / ".codex" / "sessions"
        if not base.is_dir():
            print(f"not found: {base}")
            return 1
        n = import_codex(store, base, args.since, args.limit, args.dry_run)
    else:
        base = Path(args.path).expanduser() if args.path else Path.home() / ".claude" / "projects"
        if not base.is_dir():
            print(f"not found: {base}")
            return 1
        n = import_claude(store, base, args.since, args.limit, args.dry_run)

    verb = "would import" if args.dry_run else "imported"
    print(f"{verb} {n} turn(s) into {store.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
