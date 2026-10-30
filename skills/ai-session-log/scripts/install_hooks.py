#!/usr/bin/env python3
"""Register ai-session-log hooks with Codex or Claude Code.

Default is a dry run: it prints the plan. Pass --apply to write, and the
existing config is backed up first.

Examples:
    python install_hooks.py --tool codex --root D:/logs/ai-session
    python install_hooks.py --tool codex --root D:/logs/ai-session --apply
    python install_hooks.py --tool claude --apply
    python install_hooks.py --tool generic --root D:/logs/ai-session
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
sys.dont_write_bytecode = True
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "chatlog_hook.py"


def command_for(python: str, root: str | None, agent: str) -> str:
    parts = [f'"{python}"', f'"{HOOK}"']
    if root:
        parts += ["--root", f'"{Path(root).expanduser().resolve()}"']
    if agent:
        parts += ["--agent", agent]
    return " ".join(parts)


def entry(command: str, matcher: bool = False) -> dict:
    node = {"type": "command", "command": command}
    if os.name == "nt":
        node["commandWindows"] = command
    hook = {"hooks": [node]}
    if matcher:
        hook["matcher"] = "*"
    return hook


def is_ours(node: dict) -> bool:
    text = json.dumps(node, ensure_ascii=False)
    return "chatlog_hook.py" in text or "chatlog.py" in text


def strip_ours(container: dict) -> None:
    hooks = container.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        kept = [g for g in hooks[event] if not is_ours(g)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except ValueError as exc:
        raise SystemExit(f"refusing to edit invalid JSON: {path}\n  {exc}")


def backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    dest = path.with_name(path.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dest)
    return dest


def codex_plan(command: str, root: str) -> dict:
    return {
        "description": "ai-session-log: record each Q&A turn with tool-call trace to a local Markdown/JSONL log.",
        "hooks": {
            "UserPromptSubmit": [entry(command)],
            "PostToolUse": [entry(command, matcher=True)],
            "Stop": [entry(command)],
            "Interrupt": [entry(command)],
        },
    }


def claude_plan(command: str) -> dict:
    return {
        "hooks": {
            "UserPromptSubmit": [entry(command)],
            "PostToolUse": [entry(command, matcher=True)],
            "Stop": [entry(command)],
            "SubagentStop": [entry(command)],
        },
    }


def merge_codex(existing: dict, plan: dict) -> dict:
    out = dict(existing)
    out["description"] = plan["description"]
    hooks = dict(out.get("hooks") or {})
    strip_ours(out)
    hooks = dict(out.get("hooks") or {})
    for event, groups in plan["hooks"].items():
        hooks[event] = list(hooks.get(event) or []) + groups
    out["hooks"] = hooks
    return out


def merge_claude(existing: dict, plan: dict) -> dict:
    out = dict(existing)
    strip_ours(out)
    hooks = dict(out.get("hooks") or {})
    for event, groups in plan["hooks"].items():
        hooks[event] = list(hooks.get(event) or []) + groups
    out["hooks"] = hooks
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Install ai-session-log hooks")
    ap.add_argument("--tool", choices=["codex", "claude", "generic"], required=True)
    ap.add_argument("--root", default=str(Path.home() / "ai-session-log"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--agent", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    agent = args.agent or ("claude" if args.tool == "claude" else "codex")
    command = command_for(args.python, args.root, agent)
    if "msys" in str(Path(args.python)).lower():
        print("note: detected an MSYS Python; if the target agent runs outside MSYS, "
              "pass --python <Windows python.exe>.")

    if args.tool == "generic":
        print("Register this command as a hook in your tool:\n")
        print("  " + command + "\n")
        print("It reads one JSON event on stdin. Supported event names and field aliases:")
        print("  UserPromptSubmit / Stop / PostToolUse / Interrupt")
        print("See references/hook-events.md for the normalized schema.")
        return 0

    if args.tool == "codex":
        target = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "hooks.json"
        plan = codex_plan(command, args.root)
        merger = merge_codex
    else:
        target = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "settings.json"
        plan = claude_plan(command)
        merger = merge_claude

    existing = load_json(target)
    if args.uninstall:
        strip_ours(existing)
        merged = existing
    else:
        merged = merger(existing, plan)

    print(f"target : {target}")
    print(f"command: {command}")
    print()
    if args.uninstall:
        print("removing ai-session-log hook entries; remaining events: "
              + (", ".join(sorted((merged.get("hooks") or {}).keys())) or "(none)"))
    else:
        print("hooks to register:")
        print(json.dumps({"hooks": plan["hooks"]}, ensure_ascii=False, indent=2))
        preserved = [k for k in existing if k not in ("hooks", "description")]
        if preserved:
            print("\nother top-level keys preserved (not shown): " + ", ".join(preserved))

    if not args.apply:
        print("\n(dry run) re-run with --apply to write")
        return 0

    bak = backup(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {target}" + (f"\nbackup: {bak}" if bak else ""))

    if args.tool == "codex":
        root = Path(args.root).expanduser().resolve()
        print("\nCodex 首次运行会要求信任这 4 个 hook；请确认 hooks 状态为 enabled。")
        print("若存储目录不在当前 workspace 内，请把下面这段并入 ~/.codex/config.toml：\n")
        print("[sandbox_workspace_write]")
        print(f"writable_roots = [{json.dumps(str(root), ensure_ascii=False)}]")
    else:
        print("\n重启 Claude Code 后生效；新 hook 首次运行会要求确认。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
