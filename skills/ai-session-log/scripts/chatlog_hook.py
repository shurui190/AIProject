#!/usr/bin/env python3
"""Hook handler: record one AI-agent turn into the local session store.

Reads ONE hook event as JSON on stdin and writes/updates a record.

Verified adapters:
  * Codex        - uses `turn_id` for exact event pairing
  * Claude Code  - no `turn_id`; attaches to the session's latest open record

Any other tool that can run a command hook with JSON on stdin works through
the alias map below; see references/hook-events.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
sys.dont_write_bytecode = True
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import (  # noqa: E402
    MAX_STORED_TOOLS,
    clip,
    now_iso,
    redact,
    redact_text,
    Store,
)

OPEN_KEY = "__open__"

EVENT_ALIASES = {
    "userpromptsubmit": "UserPromptSubmit",
    "user_prompt_submit": "UserPromptSubmit",
    "promptsubmit": "UserPromptSubmit",
    "prompt_submit": "UserPromptSubmit",
    "beforeagent": "UserPromptSubmit",
    "before_agent": "UserPromptSubmit",
    "beforesubmitprompt": "UserPromptSubmit",
    "before_submit_prompt": "UserPromptSubmit",
    "stop": "Stop",
    "agentstop": "Stop",
    "agent_stop": "Stop",
    "afteragent": "Stop",
    "after_agent": "Stop",
    "subagentstop": "Stop",
    "subagent_stop": "Stop",
    "sessionend": "Stop",
    "session_end": "Stop",
    "interrupt": "Interrupt",
    "userinterrupt": "Interrupt",
    "user_interrupt": "Interrupt",
    "cancel": "Interrupt",
    "abort": "Interrupt",
    "posttooluse": "PostToolUse",
    "post_tool_use": "PostToolUse",
    "aftertool": "PostToolUse",
    "after_tool": "PostToolUse",
    "toolresult": "PostToolUse",
    "tool_result": "PostToolUse",
}

JSON_REQUIRED_EVENTS = {"Stop", "SubagentStop", "Interrupt"}


def first(event: dict, *keys, default=None):
    for key in keys:
        if key in event and event[key] not in (None, ""):
            return event[key]
    return default


def normalize_event_name(event: dict) -> str:
    raw = str(first(event, "hook_event_name", "hookEventName", "event", "type", default="")).strip()
    return EVENT_ALIASES.get(raw.lower().replace("-", "_"), EVENT_ALIASES.get(raw.lower(), raw))


def normalize_session(event: dict) -> str:
    return str(first(event, "session_id", "sessionId", "conversation_id",
                      "conversationId", "thread_id", "threadId", default="unknown-session"))


def normalize_prompt(event: dict) -> str:
    value = first(event, "prompt", "user_prompt", "userPrompt", "message", "input")
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "")


def normalize_answer(event: dict) -> str:
    value = first(event, "last_assistant_message", "lastAssistantMessage",
                  "assistant_message", "response", "answer", "output")
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "")


def normalize_tool(event: dict):
    name = str(first(event, "tool_name", "toolName", "tool", default="unknown"))
    tool_input = first(event, "tool_input", "toolInput", "arguments", "input", default=None)
    tool_response = first(event, "tool_response", "toolResponse", "result", "output", default=None)
    tool_use_id = str(first(event, "tool_use_id", "toolUseId", "call_id", "callId", default=""))
    return name, tool_input, tool_response, tool_use_id


def last_assistant_from_transcript(path: str) -> str:
    """Best-effort Claude Code fallback when Stop omits last_assistant_message."""
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return ""
        answer = ""
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                msg = obj.get("message")
                if obj.get("type") == "assistant" and isinstance(msg, dict):
                    texts = [b.get("text", "") for b in msg.get("content", [])
                             if isinstance(b, dict) and b.get("type") == "text"]
                    if any(t.strip() for t in texts):
                        answer = "\n".join(t for t in texts if t)
        return answer
    except OSError:
        return ""


def apply_common(rec: dict, event: dict, agent: str, room: str) -> None:
    cwd = first(event, "cwd", "workspace", "working_directory", "workingDirectory")
    model = first(event, "model", "model_name", "modelName")
    if cwd:
        rec["cwd"] = str(cwd)
    if model:
        rec["model"] = str(model)
    if agent:
        rec["agent"] = agent
    if room:
        rec["room"] = room


def handle_start(rec: dict, event: dict) -> None:
    prompt = normalize_prompt(event)
    if prompt.strip():
        rec["question"] = redact_text(prompt)
    rec["status"] = "open"
    rec["started_at"] = rec.get("started_at") or now_iso()
    rec["ended_at"] = None


def handle_stop(rec: dict, event: dict) -> None:
    answer = normalize_answer(event)
    if not answer.strip():
        answer = last_assistant_from_transcript(str(first(event, "transcript_path", "transcriptPath", default="")))
    if answer.strip():
        rec["answer"] = redact_text(answer)
        rec["status"] = "closed"
    else:
        rec["answer"] = ""
        rec["status"] = "closed" if rec.get("answer") else "no_answer"
    rec["ended_at"] = now_iso()


def handle_interrupt(rec: dict) -> None:
    if not rec.get("answer"):
        rec["status"] = "no_answer"
    rec["ended_at"] = rec.get("ended_at") or now_iso()


def handle_tool(rec: dict, event: dict) -> None:
    name, tool_input, tool_response, _ = normalize_tool(event)
    safe_input = redact(tool_input)

    if isinstance(safe_input, dict) and isinstance(safe_input.get("command"), str):
        summary = safe_input["command"]
    elif isinstance(safe_input, str):
        summary = safe_input
    else:
        summary = json.dumps(safe_input, ensure_ascii=False) if safe_input is not None else ""

    if isinstance(tool_response, str):
        response_text = tool_response
    elif tool_response is not None:
        response_text = json.dumps(redact(tool_response), ensure_ascii=False)
    else:
        response_text = ""

    tools = rec.setdefault("tools", [])
    tools.append({
        "name": name,
        "at": now_iso(),
        "summary": clip(redact_text(summary), 400),
        "output": clip(redact_text(response_text), 2000),
    })
    if len(tools) > MAX_STORED_TOOLS:
        del tools[:-MAX_STORED_TOOLS]


def process(store: Store, event: dict, agent: str, room: str) -> str:
    name = normalize_event_name(event)
    if name not in {"UserPromptSubmit", "Stop", "SubagentStop", "Interrupt", "PostToolUse"}:
        return name

    session = normalize_session(event)
    turn = str(first(event, "turn_id", "turnId", "request_id", "requestId", default="")).strip()
    has_turn_id = bool(turn)
    if not turn:
        tool_id = first(event, "tool_use_id", "toolUseId", default="")
        turn = f"{name or 'event'}:{tool_id or int(__import__('time').time() * 1000)}"

    store.ensure()
    with store.lock():
        mapping = store.load_turnmap()
        if has_turn_id:
            rec, _ = store.get_or_create(session, turn, mapping, agent, room)
        elif name == "UserPromptSubmit":
            rec, _ = store.get_or_create(session, turn, mapping, agent, room)
            mapping[f"{session}::{OPEN_KEY}"] = rec["id"]
            store.save_turnmap(mapping)
        else:
            rec = store.latest_open(session, mapping, agent, room)
            if rec is None:
                rec, _ = store.get_or_create(session, turn, mapping, agent, room)

        apply_common(rec, event, agent, room)

        if name == "UserPromptSubmit":
            handle_start(rec, event)
        elif name in ("Stop", "SubagentStop"):
            handle_stop(rec, event)
        elif name == "Interrupt":
            handle_interrupt(rec)
        else:
            handle_tool(rec, event)

        store.write_record(rec)

        if name in ("Stop", "SubagentStop", "Interrupt"):
            store.append_session_row(rec)
            store.append_global_row(rec)

    if name in ("Stop", "SubagentStop", "Interrupt"):
        try:
            store.rebuild_md(session)
        except Exception:
            with open(store.errors, "a", encoding="utf-8") as fh:
                fh.write(f"[{now_iso()}] rebuild_md failed\n{traceback.format_exc()}\n")
    return name


def read_event(path: str | None) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8-sig") if path else sys.stdin.buffer.read().decode("utf-8-sig", "replace")
    except Exception:
        return {}
    try:
        value = json.loads(raw) if raw.strip() else {}
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="ai-session-log hook handler")
    ap.add_argument("--root", help="store root (default: $CHATLOG_ROOT or ~/ai-session-log)")
    ap.add_argument("--agent", default=os.environ.get("CHATLOG_AGENT", "codex"))
    ap.add_argument("--room", default=os.environ.get("CHATLOG_ROOM", "private"))
    ap.add_argument("--event-file", help="read the event JSON from a file instead of stdin")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        print("chatlog_hook: import OK")
        print("store root:", Store(args.root).root)
        return 0

    name = ""
    try:
        name = process(Store(args.root), read_event(args.event_file), args.agent, args.room)
    except Exception:
        try:
            with open(Store(args.root).errors, "a", encoding="utf-8") as fh:
                fh.write(f"[{now_iso()}] hook failed\n{traceback.format_exc()}\n")
        except OSError:
            pass

    if name in JSON_REQUIRED_EVENTS:
        sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
