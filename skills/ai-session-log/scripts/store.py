#!/usr/bin/env python3
"""Shared storage layer for ai-session-log.

Stdlib only. Compatible with the original chatlog.py layout:

    <root>/
      data/counter.json
      data/turnmap.json
      data/sessionmap.json
      data/index.jsonl
      data/by-session/<title>__<sid8>.jsonl
      data/pairs/<NNN>/<NNNNNN>.json
      data/errors.log
      md/<title>__<sid8>.md
      md/<title>__<sid8>-part<N>.md
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
MAX_TOOL_OUTPUT = 2000
MAX_STORED_TOOLS = 300
TURNMAP_LIMIT = 2000

STATUS_LABEL = {
    "open": "进行中",
    "closed": "已完成",
    "no_answer": "无回复（被打断）",
}

SENSITIVE_KEY_PARTS = {
    "token", "secret", "password", "passwd", "pwd", "credential",
    "credentials", "apikey", "auth", "authorization", "bearer",
}
SENSITIVE_KEY_SUBSTR = (
    "api_key", "api-key", "access_key", "secret_key", "private_key",
)
SENSITIVE_PATH_RE = re.compile(
    r"(auth\.json|\.env(?:\.[A-Za-z0-9_]+)?|credentials(?:\.json)?|\.netrc|id_rsa)",
    re.IGNORECASE,
)
REDACT_VALUE_RES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)
UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# --------------------------------------------------------------------------
# time / io
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(path: Path):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line.rstrip("\n") + "\n")


def safe_name(value: str) -> str:
    cleaned = UNSAFE_FILENAME_RE.sub("_", str(value or "")).strip("._ ")
    return cleaned[:120] or "unknown-session"


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:n]


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------

def _is_sensitive_key(key: str) -> bool:
    low = key.lower()
    if any(s in low for s in SENSITIVE_KEY_SUBSTR):
        return True
    parts = [p for p in re.split(r"[^a-z0-9]+", low) if p]
    return bool(set(parts) & SENSITIVE_KEY_PARTS)


def redact_text(value: str) -> str:
    out = value
    for rx in REDACT_VALUE_RES:
        out = rx.sub("<redacted>", out)
    if SENSITIVE_PATH_RE.search(out):
        out = SENSITIVE_PATH_RE.sub("<sensitive-path>", out)
    return out


def redact(obj, depth: int = 0):
    if depth > 12:
        return "<max-depth>"
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if isinstance(k, str) and _is_sensitive_key(k)
                else redact(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


# --------------------------------------------------------------------------
# cross-process lock
# --------------------------------------------------------------------------

if os.name == "nt":
    import msvcrt

    def _try_lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fh):
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh):
        fcntl.flock(fh, fcntl.LOCK_UN)


class FileLock:
    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = path
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        if self._fh.tell() == 0:
            self._fh.write(b"\0")
            self._fh.flush()
        deadline = time.time() + self.timeout
        while True:
            try:
                _try_lock(self._fh)
                return self
            except OSError:
                if time.time() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise TimeoutError(f"could not lock {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fh is None:
            return False
        try:
            _unlock(self._fh)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None
        return False


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------

def default_root() -> Path:
    return Path(os.environ.get("CHATLOG_ROOT") or (Path.home() / "ai-session-log"))


class Store:
    def __init__(self, root=None):
        self.root = Path(root).expanduser().resolve() if root else default_root().expanduser()
        self.data = self.root / "data"
        self.pairs = self.data / "pairs"
        self.by_session = self.data / "by-session"
        self.md = self.root / "md"
        self.counter = self.data / "counter.json"
        self.turnmap = self.data / "turnmap.json"
        self.sessionmap = self.data / "sessionmap.json"
        self.index = self.data / "index.jsonl"
        self.errors = self.data / "errors.log"
        self.lockfile = self.data / ".lock"

    def ensure(self) -> "Store":
        for d in (self.data, self.pairs, self.by_session, self.md):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def lock(self) -> FileLock:
        return FileLock(self.lockfile)

    # ---- record paths -------------------------------------------------
    def pair_path(self, record_id: int) -> Path:
        return self.pairs / f"{record_id // 1000:03d}" / f"{record_id:06d}.json"

    def load_record(self, record_id: int):
        return read_json(self.pair_path(int(record_id)))

    def write_record(self, rec: dict) -> None:
        atomic_write_json(self.pair_path(int(rec["id"])), rec)

    # ---- counters / maps ---------------------------------------------
    def next_id(self) -> int:
        data = read_json(self.counter, {}) or {}
        new_id = int(data.get("last_id", 0)) + 1
        atomic_write_json(self.counter, {"last_id": new_id})
        return new_id

    def load_turnmap(self) -> dict:
        data = read_json(self.turnmap, {})
        return data if isinstance(data, dict) else {}

    def save_turnmap(self, mapping: dict) -> None:
        if len(mapping) > TURNMAP_LIMIT:
            mapping = dict(list(mapping.items())[-TURNMAP_LIMIT:])
        atomic_write_json(self.turnmap, mapping)

    def load_sessionmap(self) -> dict:
        data = read_json(self.sessionmap, {})
        return data if isinstance(data, dict) else {}

    def save_sessionmap(self, mapping: dict) -> None:
        atomic_write_json(self.sessionmap, mapping)

    # ---- session files ------------------------------------------------
    def session_file(self, session: str) -> Path:
        smap = self.load_sessionmap()
        entry = smap.get(session)
        rel = entry.get("file") if isinstance(entry, dict) else entry
        if rel:
            return self.data / rel
        # backwards compatible lookup: anything ending with the short id
        sid8 = safe_name(session)[:8]
        matches = sorted(self.by_session.glob(f"*{sid8}*.jsonl"))
        if matches:
            return matches[-1]
        return self.by_session / f"{safe_name(session)}.jsonl"

    def session_records(self, session: str):
        path = self.session_file(session)
        ids, seen = [], set()
        for row in read_jsonl(path):
            try:
                rid = int(row["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if rid not in seen:
                seen.add(rid)
                ids.append(rid)
        ids.sort()
        out = []
        for rid in ids:
            rec = self.load_record(rid)
            if rec and rec.get("session_id") == session:
                out.append(rec)
        return out

    def all_session_ids(self):
        ids = set()
        for p in self.pairs.glob("*/*.json"):
            rec = read_json(p)
            if rec and rec.get("session_id"):
                ids.add(rec["session_id"])
        return sorted(ids)

    def session_title(self, session: str) -> str:
        smap = self.load_sessionmap()
        entry = smap.get(session) or {}
        return entry.get("title") or ""

    # ---- writes -------------------------------------------------------
    def new_record(self, session: str, turn: str, agent="codex", room="private") -> dict:
        return {
            "id": 0,
            "turn_id": turn,
            "session_id": session,
            "agent": agent,
            "room": room,
            "started_at": now_iso(),
            "ended_at": None,
            "cwd": None,
            "model": None,
            "question": "",
            "answer": "",
            "status": "open",
            "tools": [],
        }

    def get_or_create(self, session: str, turn: str, mapping=None, agent="codex", room="private"):
        mapping = self.load_turnmap() if mapping is None else mapping
        key = f"{session}::{turn}"
        rid = mapping.get(key)
        created = False
        if rid is None:
            rid = self.next_id()
            mapping[key] = rid
            self.save_turnmap(mapping)
            rec = self.new_record(session, turn, agent, room)
            rec["id"] = rid
            self.write_record(rec)
            created = True
            return rec, created
        rec = self.load_record(int(rid))
        if rec is None:
            rec = self.new_record(session, turn, agent, room)
            rec["id"] = int(rid)
            self.write_record(rec)
            created = True
        return rec, created

    def latest_open(self, session: str, mapping=None, agent="codex", room="private"):
        mapping = self.load_turnmap() if mapping is None else mapping
        rid = mapping.get(f"{session}::__open__")
        if rid is None:
            return None
        rec = self.load_record(int(rid))
        if rec is None or rec.get("status") != "open":
            return None
        return rec

    def insert_record(self, rec: dict, turn_key: str) -> dict:
        """Allocate an id for a fully-formed record and persist it."""
        rid = self.next_id()
        rec["id"] = rid
        self.write_record(rec)
        mapping = self.load_turnmap()
        mapping[f"{rec['session_id']}::{turn_key}"] = rid
        self.save_turnmap(mapping)
        self.append_session_row(rec)
        if rec.get("status") in ("closed", "no_answer"):
            self.append_global_row(rec)
        return rec

    # ---- indexes ------------------------------------------------------
    def _title_of(self, rec: dict) -> str:
        q = (rec.get("question") or "").strip()
        return q.splitlines()[0][:80] if q else ""

    def append_session_row(self, rec: dict) -> None:
        path = self.session_file(rec["session_id"])
        append_line(path, json.dumps({
            "id": rec["id"],
            "ts": rec.get("started_at"),
            "ended_at": rec.get("ended_at"),
            "status": rec.get("status"),
            "title": self._title_of(rec),
            "tools": len(rec.get("tools") or []),
            "file": f"pairs/{int(rec['id']) // 1000:03d}/{int(rec['id']):06d}.json",
        }, ensure_ascii=False))
        smap = self.load_sessionmap()
        smap[rec["session_id"]] = {
            "file": str(path.relative_to(self.data).as_posix()),
            "title": self.session_title(rec["session_id"]) or self._title_of(rec),
            "agent": rec.get("agent"),
            "updated_at": now_iso(),
        }
        self.save_sessionmap(smap)

    def append_global_row(self, rec: dict) -> None:
        append_line(self.index, json.dumps({
            "id": rec["id"],
            "ts": rec.get("started_at"),
            "ended_at": rec.get("ended_at"),
            "session": rec.get("session_id"),
            "agent": rec.get("agent"),
            "room": rec.get("room"),
            "status": rec.get("status"),
            "tools": len(rec.get("tools") or []),
            "title": self._title_of(rec),
            "file": f"pairs/{int(rec['id']) // 1000:03d}/{int(rec['id']):06d}.json",
        }, ensure_ascii=False))

    def set_session_title(self, session: str, title: str) -> None:
        if not title:
            return
        smap = self.load_sessionmap()
        entry = smap.get(session) or {}
        entry["title"] = title
        entry.setdefault("file", str(self.session_file(session).relative_to(self.data).as_posix()))
        entry["updated_at"] = now_iso()
        smap[session] = entry
        self.save_sessionmap(smap)

    # ---- markdown -----------------------------------------------------
    def render_pair(self, rec: dict) -> str:
        rid = int(rec["id"])
        started = (rec.get("started_at") or "")[:16].replace("T", " ")
        status = rec.get("status", "open")
        lines = [f"## #{rid:06d} · {started} · {STATUS_LABEL.get(status, status)}", ""]
        question = (rec.get("question") or "").strip()
        lines += ["**Q**", "", question or "*(未记录到问题内容)*", ""]
        answer = (rec.get("answer") or "").strip()
        lines += ["**A**", ""]
        if answer:
            lines.append(answer)
        elif status == "no_answer":
            lines.append("*(该轮被中断，无最终回复)*")
        else:
            lines.append("*(尚未产生最终回复)*")
        lines.append("")
        tools = rec.get("tools") or []
        if tools:
            lines += [f"**工具调用痕迹（{len(tools)}）**", ""]
            for item in tools:
                name = item.get("name", "?")
                summary = (item.get("summary") or "").replace("\n", " ").strip()
                if len(summary) > 160:
                    summary = summary[:160] + "..."
                lines.append(f"- `{name}` · {summary}" if summary else f"- `{name}`")
            lines.append("")
        lines += ["---", ""]
        return "\n".join(lines)

    def rebuild_md(self, session: str, max_pairs=200, max_bytes=5 * 1024 * 1024) -> list:
        records = self.session_records(session)
        if not records:
            return []
        title = self.session_title(session) or session
        sid8 = safe_name(session)[:8]
        base = f"{safe_name(title)}__{sid8}" if title != session else safe_name(session)

        parts, current, size = [], [], 0
        for rec in records:
            chunk = self.render_pair(rec)
            n = len(chunk.encode("utf-8"))
            if current and (len(current) >= max_pairs or size + n > max_bytes):
                parts.append(current)
                current, size = [], 0
            current.append(chunk)
            size += n
        if current:
            parts.append(current)

        header = (
            f"# {title}\n\n"
            f"> 由 ai-session-log 自动生成，请勿手改；数据源为 `data/pairs/`。\n\n"
            f"- session_id: `{session}`\n"
            f"- 共 {len(records)} 轮对话\n\n---\n\n"
        )
        written = []
        for i, chunk in enumerate(parts, 1):
            name = f"{base}.md" if i == 1 else f"{base}-part{i}.md"
            body = header if i == 1 else f"# {title}（第 {i} 部分）\n\n"
            atomic_write_text(self.md / name, body + "".join(chunk))
            written.append(name)
        for stale in self.md.glob(f"{base}-part*.md"):
            if stale.name not in written:
                try:
                    stale.unlink()
                except OSError:
                    pass
        return written
