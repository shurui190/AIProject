# 存储布局与数据模型

## 目录

```text
<root>/
├── data/
│   ├── counter.json              # {"last_id": N} 全局自增 ID
│   ├── turnmap.json              # "<session>::<turn>" -> record id
│   ├── sessionmap.json           # session id -> {file,title,agent,updated_at}
│   ├── index.jsonl               # 全局索引，每轮一行
│   ├── by-session/
│   │   └── <title>__<sid8>.jsonl # 会话内索引
│   ├── pairs/
│   │   └── <NNN>/
│   │       └── <NNNNNN>.json     # 单轮完整记录
│   ├── errors.log
│   └── .lock
└── md/
    ├── <title>__<sid8>.md
    └── <title>__<sid8>-part<N>.md
```

- 单文件超过 **200 轮**或 **5 MB** 自动分段。
- `md/` 是可读视图，随时可由 `query_log.py rebuild` 从 `data/pairs/` 重建。
- `data/pairs/` 是唯一真值来源。

## 单轮记录（`data/pairs/NNN/NNNNNN.json`）

```json
{
  "id": 123,
  "turn_id": "<turn-id>",
  "session_id": "<session-id>",
  "agent": "codex",
  "room": "private",
  "started_at": "2026-01-01T09:00:00+08:00",
  "ended_at": "2026-01-01T09:01:00+08:00",
  "cwd": "<project-dir>",
  "model": "<model-name>",
  "question": "...",
  "answer": "...",
  "status": "closed",
  "tools": [
    { "name": "Bash", "at": "...", "summary": "...", "output": "..." }
  ]
}
```

`status`：`open`（进行中）/ `closed`（已完成）/ `no_answer`（被打断或无回复）。

## 全局索引（`data/index.jsonl`）

每行是单轮摘要，便于快速 grep：

```json
{"id":123,"ts":"...","ended_at":"...","session":"...","agent":"codex",
 "room":"private","status":"closed","tools":54,"title":"...","file":"pairs/000/000123.json"}
```

`file` 字段就是该轮的完整 JSON 相对路径——其他会话/工具可以直接按它读取完整内容。

## 程序化访问

```python
import json
from pathlib import Path

root = Path(r"D:\logs\ai-session")
idx = [json.loads(l) for l in (root/"data/index.jsonl").read_text(encoding="utf-8").splitlines() if l]
rec = json.loads((root/"data"/idx[-1]["file"]).read_text(encoding="utf-8"))
print(rec["question"], rec["answer"])
```

按 session 读取全部轮次：

```bash
python scripts/query_log.py --root D:/logs/ai-session show <session_id> --format jsonl
```

## 去重与幂等

- Codex：以 `session_id::turn_id` 为键，重复导入自动跳过。
- Claude：无 `turn_id`，以 `(started_at[:19], question[:80])` 指纹去重。
- 所有写入都在跨进程文件锁内完成。

## 脱敏

写入前会：

- 按键名屏蔽 `token/secret/password/credential/api_key/...`；
- 正则屏蔽 `sk-*`、`ghp_*`、`AKIA*`、JWT、32 位以上十六进制；
- 把 `auth.json`、`.env`、`credentials.json`、`id_rsa` 等路径替换为 `<sensitive-path>`。

这只能降低误泄漏概率，**不是**完整的安全边界；不要故意把密钥写进对话。
