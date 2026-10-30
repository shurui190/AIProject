---
name: ai-session-log
version: 1.0.0
description: 部署和使用本地 AI 会话记录器（local AI session log）。当用户想要把 Codex/Claude Code/其他 AI 工具的会话记录为 Markdown+JSON、导入已有 transcript、按 session 或关键字检索历史、或按 record id 取回完整对话时使用。
metadata:
  short-description: Local AI session history logger
---

# AI Session Log

把不同 AI 工具的对话落到本地：**Markdown 供人看，JSON 供程序读，JSONL 做索引**。

- 实时记录：通过 hook 监听 `UserPromptSubmit` / `PostToolUse` / `Stop` / `Interrupt`
- 历史回填：导入 Codex rollout 和 Claude Code transcript
- 查询：按 session 浏览、按关键字搜索、按 record id 取完整 JSON
- 可移植：仅 Python 标准库，无第三方依赖

## 快速开始

```bash
# 1) 部署到 Codex（先 dry-run，确认后加 --apply）
python scripts/install_hooks.py --tool codex --root "D:/logs/ai-session"

# 2) 部署到 Claude Code
python scripts/install_hooks.py --tool claude --root "D:/logs/ai-session" --apply

# 3) 回填已有历史
python scripts/import_history.py --tool codex  --root "D:/logs/ai-session" --since 2026-09-01
python scripts/import_history.py --tool claude --root "D:/logs/ai-session" --since 2026-09-01
```

## 查询

```bash
python scripts/query_log.py --root "D:/logs/ai-session" sessions
python scripts/query_log.py --root "D:/logs/ai-session" show <session_id>
python scripts/query_log.py --root "D:/logs/ai-session" show <session_id> --format jsonl
python scripts/query_log.py --root "D:/logs/ai-session" turn 572
python scripts/query_log.py --root "D:/logs/ai-session" search "MISRA"
python scripts/query_log.py --root "D:/logs/ai-session" export <session_id> --format json --out session.json
```

`session_id` 支持最短唯一前缀（如 `3a0cb787`）。
`turn` 接受纯数字或 `#000572`。

## 数据结构

```text
<root>/
├── data/pairs/NNN/NNNNNN.json   # 单轮完整记录（唯一真值）
├── data/index.jsonl             # 全局索引，每轮一行
├── data/by-session/*.jsonl      # 按会话索引
├── data/turnmap.json            # 去重/配对
├── data/sessionmap.json         # session -> 文件/标题
└── md/*.md                      # 人可读视图（可重建）
```

单轮记录里 `file` 字段指向完整 JSON，其他会话或工具可以直接读取：

```bash
python scripts/query_log.py --root "D:/logs/ai-session" turn 572
```

## 不同工具的接入

| 工具 | 接入方式 | 事件配对 |
|---|---|---|
| Codex | `install_hooks.py --tool codex` | 有 `turn_id`，精确配对 |
| Claude Code | `install_hooks.py --tool claude` | 无 `turn_id`，挂到该 session 最新 open 记录 |
| 其他 | `install_hooks.py --tool generic` 打印通用命令 | 依赖 `session_id` + 事件别名 |

字段别名、事件名映射和手工测试样例见 [references/hook-events.md](references/hook-events.md)。

## 参考资料

- 部署步骤与信任/权限说明：[references/deployment.md](references/deployment.md)
- 存储布局与 JSON schema：[references/storage-layout.md](references/storage-layout.md)
- hook 事件格式与别名：[references/hook-events.md](references/hook-events.md)

## 约束

- 写入前会做键名 + 正则脱敏，但这**不是安全边界**；不要故意把密钥写进对话。
- `md/` 可随时删除；用 `query_log.py rebuild` 从 `data/pairs/` 重建。
- 修改 hook 配置前先备份；`install_hooks.py` 默认 dry-run，且只改含 `chatlog_hook.py` 的条目。
