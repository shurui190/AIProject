#!/usr/bin/env python3
"""Query and export recorded AI sessions.

Examples:
    python query_log.py sessions
    python query_log.py show 3a0cb787
    python query_log.py turn 572
    python query_log.py search "MISRA"
    python query_log.py export 3a0cb787 --format jsonl --out session.jsonl
    python query_log.py rebuild
"""

from __future__ import annotations

import argparse
import json
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Store, atomic_write_text, now_iso, read_json  # noqa: E402


def resolve_session(store: Store, token: str) -> str:
    token = token.strip()
    ids = set(store.load_sessionmap().keys())
    ids.update(store.all_session_ids())
    if token in ids:
        return token
    matches = sorted(i for i in ids if i.startswith(token))
    if not matches:
        raise SystemExit(f"no session matches: {token}")
    if len(matches) > 1:
        raise SystemExit("ambiguous session prefix, matches:\n  " + "\n  ".join(matches))
    return matches[0]


def sessions_table(store: Store, agent=None, json_out=False):
    rows = []
    for sid in sorted(set(store.load_sessionmap()) | set(store.all_session_ids())):
        recs = store.session_records(sid)
        if not recs:
            continue
        if agent and recs[-1].get("agent") != agent:
            continue
        rows.append({
            "session_id": sid,
            "title": store.session_title(sid) or (recs[0].get("question") or "").splitlines()[0][:60],
            "agent": recs[-1].get("agent"),
            "turns": len(recs),
            "first": recs[0].get("started_at"),
            "last": recs[-1].get("ended_at") or recs[-1].get("started_at"),
        })
    rows.sort(key=lambda r: r["last"] or "", reverse=True)
    if json_out:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("(no sessions recorded yet)")
        return
    print(f"{'SESSION':38} {'AGENT':8} {'TURNS':>5}  {'LAST':19}  TITLE")
    for r in rows:
        print(f"{r['session_id'][:38]:38} {str(r['agent'])[:8]:8} {r['turns']:5d}  "
              f"{str(r['last'])[:19]:19}  {r['title']}")


def cmd_show(store: Store, token: str, fmt: str, limit=None, offset=0):
    sid = resolve_session(store, token)
    recs = store.session_records(sid)
    if limit:
        recs = recs[offset:offset + limit]
    elif offset:
        recs = recs[offset:]

    if fmt == "json":
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return
    if fmt == "jsonl":
        for rec in recs:
            print(json.dumps(rec, ensure_ascii=False))
        return

    title = store.session_title(sid) or sid
    print(f"# {title}\n\n- session_id: `{sid}`\n- turns: {len(recs)}\n")
    for rec in recs:
        print(store.render_pair(rec))


def cmd_turn(store: Store, token: str, fmt: str):
    try:
        rid = int(token.lstrip("#"))
    except ValueError:
        raise SystemExit(f"not a record id: {token}")
    rec = store.load_record(rid)
    if rec is None:
        raise SystemExit(f"record not found: {rid}")
    if fmt == "text":
        print(f"# {rid:06d} · {rec.get('started_at')} · {rec.get('status')}")
        print("\n## Q\n" + (rec.get("question") or ""))
        print("\n## A\n" + (rec.get("answer") or ""))
        if rec.get("tools"):
            print(f"\n## tools ({len(rec['tools'])})")
            for item in rec["tools"]:
                print(f"- {item.get('name')} · {item.get('summary')}")
        return
    print(json.dumps(rec, ensure_ascii=False, indent=2))


def cmd_search(store: Store, keyword: str, agent=None, limit=30):
    needle = keyword.lower()
    hits = []
    for sid in sorted(set(store.load_sessionmap()) | set(store.all_session_ids())):
        recs = store.session_records(sid)
        if agent and recs and recs[-1].get("agent") != agent:
            continue
        for rec in recs:
            hay = "\n".join([
                rec.get("question") or "",
                rec.get("answer") or "",
                "\n".join(str(t.get("summary") or "") for t in rec.get("tools") or []),
            ]).lower()
            if needle in hay:
                src = rec.get("question") or rec.get("answer") or ""
                pos = src.lower().find(needle)
                snippet = src[max(0, pos - 40):pos + 120].replace("\n", " ") if pos >= 0 else src[:120]
                hits.append((rec, snippet))
    hits.sort(key=lambda x: x[0].get("started_at") or "", reverse=True)
    for rec, snippet in hits[:limit]:
        print(f"#{int(rec['id']):06d}  {rec.get('session_id')}  [{rec.get('agent')}]  {rec.get('started_at')}")
        print(f"    {snippet}")
    print(f"\n{len(hits)} match(es)" + (f", showing first {limit}" if len(hits) > limit else ""))


def cmd_export(store: Store, token: str, fmt: str, out: str | None):
    sid = resolve_session(store, token)
    recs = store.session_records(sid)
    if fmt == "md":
        body = f"# {store.session_title(sid) or sid}\n\n- session_id: `{sid}`\n- turns: {len(recs)}\n\n" + \
               "".join(store.render_pair(r) for r in recs)
    elif fmt == "json":
        body = json.dumps(recs, ensure_ascii=False, indent=2)
    else:
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n"
    if out:
        atomic_write_text(Path(out).expanduser().resolve(), body)
        print(f"wrote {len(recs)} turn(s) -> {out}")
    else:
        print(body)


def cmd_stats(store: Store):
    ids = sorted(set(store.load_sessionmap()) | set(store.all_session_ids()))
    total = 0
    agents = {}
    first = last = None
    for sid in ids:
        recs = store.session_records(sid)
        total += len(recs)
        if recs:
            a = recs[-1].get("agent") or "unknown"
            agents[a] = agents.get(a, 0) + len(recs)
            f = recs[0].get("started_at")
            l = recs[-1].get("ended_at") or recs[-1].get("started_at")
            first = min(first or f, f) if f else first
            last = max(last or l, l) if l else last
    print(f"root:     {store.root}")
    print(f"sessions: {len(ids)}")
    print(f"turns:    {total}")
    print(f"agents:   {json.dumps(agents, ensure_ascii=False)}")
    print(f"range:    {first}  ->  {last}")


def cmd_rebuild(store: Store):
    ids = store.all_session_ids()
    idx_tmp = store.index.with_suffix(".jsonl.tmp")
    if idx_tmp.exists():
        idx_tmp.unlink()
    rows = []
    smap = {}
    for sid in ids:
        recs = store.session_records(sid)
        if not recs:
            continue
        title = store.session_title(sid) or (recs[0].get("question") or "").splitlines()[0][:80]
        store.set_session_title(sid, title)
        smap[sid] = {
            "file": str(store.session_file(sid).relative_to(store.data).as_posix()),
            "title": title,
            "agent": recs[-1].get("agent"),
            "updated_at": now_iso(),
        }
        for rec in recs:
            rows.append({
                "id": rec["id"], "ts": rec.get("started_at"), "ended_at": rec.get("ended_at"),
                "session": sid, "agent": rec.get("agent"), "room": rec.get("room"),
                "status": rec.get("status"), "tools": len(rec.get("tools") or []),
                "title": (rec.get("question") or "").strip().splitlines()[0][:80] if rec.get("question") else "",
                "file": f"pairs/{int(rec['id']) // 1000:03d}/{int(rec['id']):06d}.json",
            })
        store.rebuild_md(sid)
    store.save_sessionmap(smap)
    atomic_write_text(store.index, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""))
    print(f"rebuilt {len(ids)} session(s), {len(rows)} turn(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Query ai-session-log history")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sessions", help="list sessions")
    p.add_argument("--agent")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("show", help="show a full session")
    p.add_argument("session")
    p.add_argument("--format", choices=["md", "json", "jsonl"], default="md")
    p.add_argument("--limit", type=int)
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("turn", help="show one record by id")
    p.add_argument("id")
    p.add_argument("--format", choices=["json", "text"], default="json")

    p = sub.add_parser("search", help="search questions/answers/tools")
    p.add_argument("keyword")
    p.add_argument("--agent")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("export", help="export a session")
    p.add_argument("session")
    p.add_argument("--format", choices=["md", "json", "jsonl"], default="md")
    p.add_argument("--out")

    sub.add_parser("stats", help="store statistics")
    sub.add_parser("rebuild", help="rebuild index/sessionmap/markdown from pairs")

    args = ap.parse_args()
    store = Store(args.root).ensure()

    if args.cmd == "sessions":
        sessions_table(store, args.agent, args.json)
    elif args.cmd == "show":
        cmd_show(store, args.session, args.format, args.limit, args.offset)
    elif args.cmd == "turn":
        cmd_turn(store, args.id, args.format)
    elif args.cmd == "search":
        cmd_search(store, args.keyword, args.agent, args.limit)
    elif args.cmd == "export":
        cmd_export(store, args.session, args.format, args.out)
    elif args.cmd == "stats":
        cmd_stats(store)
    elif args.cmd == "rebuild":
        cmd_rebuild(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
