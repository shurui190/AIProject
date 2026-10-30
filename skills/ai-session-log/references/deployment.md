# 在新电脑上部署

## 0. 前置条件

- Python 3.9+（仅标准库，无第三方依赖）
- 目标工具已安装：Codex CLI / Claude Code / 其他支持 command hook 的工具

## 1. 放置 skill

把整个 `ai-session-log/` 目录放到：

```text
~/.codex/skills/ai-session-log/          # Codex（$CODEX_HOME/skills）
```

或放到你的集中技能目录（例如 `<your-skills-dir>/ai-session-log`），再软链/复制到 `~/.codex/skills`。

## 2. 选择存储目录

默认是 `~/ai-session-log`。建议集中放一个长期保留的位置：

```bash
python scripts/install_hooks.py --tool codex --root "D:/logs/ai-session"
```

默认只打印计划（dry run），确认后加 `--apply`。

## 3. Codex

```bash
python scripts/install_hooks.py --tool codex --root "D:/logs/ai-session" --apply
```

会写入 `~/.codex/hooks.json`（自动备份 `hooks.json.bak-*`），注册：

- `UserPromptSubmit`
- `PostToolUse`
- `Stop`
- `Interrupt`

**信任**：Codex 首次运行会要求确认这几个 hook；确认后 `~/.codex/config.toml` 的 `[hooks.state]` 会记录 `trusted_hash` 与 `enabled = true`。

**写权限**：如果存储目录不在当前 workspace 内，把安装脚本打印的这段并入 `~/.codex/config.toml`：

```toml
[sandbox_workspace_write]
writable_roots = ['D:\logs\ai-session']
```

## 4. Claude Code

```bash
python scripts/install_hooks.py --tool claude --root "D:/logs/ai-session" --apply
```

写入 `~/.claude/settings.json`（自动备份），注册：

- `UserPromptSubmit`
- `PostToolUse`（matcher `*`）
- `Stop`
- `SubagentStop`

重启 Claude Code 生效。

## 5. 导入已有历史

```bash
# Codex: ~/.codex/sessions/**/rollout-*.jsonl
python scripts/import_history.py --tool codex --root "D:/logs/ai-session" --since 2026-09-01

# Claude Code: ~/.claude/projects/**/*.jsonl
python scripts/import_history.py --tool claude --root "D:/logs/ai-session" --since 2026-09-01
```

先加 `--dry-run` 看数量，再正式导入。

## 6. 验证

```bash
python scripts/query_log.py --root "D:/logs/ai-session" stats
python scripts/query_log.py --root "D:/logs/ai-session" sessions
python scripts/query_log.py --root "D:/logs/ai-session" show <session_id>
```

手工灌一条测试事件：

```bash
echo '{"hook_event_name":"UserPromptSubmit","session_id":"demo","turn_id":"t1","prompt":"hello"}' | python scripts/chatlog_hook.py --root "D:/logs/ai-session"
echo '{"hook_event_name":"Stop","session_id":"demo","turn_id":"t1","last_assistant_message":"hi"}' | python scripts/chatlog_hook.py --root "D:/logs/ai-session"
python scripts/query_log.py --root "D:/logs/ai-session" show demo
```

## 7. 卸载

```bash
python scripts/install_hooks.py --tool codex --uninstall --apply
python scripts/install_hooks.py --tool claude --uninstall --apply
```

只移除包含 `chatlog_hook.py` / `chatlog.py` 的条目，不动其他 hook。

## 8. 注意事项

- 存储目录会持续增长；建议定期 `query_log.py stats` 观察。
- `md/` 只是视图，删了可以用 `query_log.py rebuild` 重建。
- 不要把 `data/` 放进公共仓库；里面是完整对话。
- Windows 路径在 JSON 命令里务必加引号，安装脚本已自动处理。

## 9. Windows 下的 Python 路径

如果当前 shell 的 `python` 是 MSYS/Cygwin 版本（例如 `C:\msys64\...\python.exe`），建议显式指定 Windows Python，避免 Codex/Claude 在非 MSYS 环境中找不到运行时：

```bash
python scripts/install_hooks.py --tool codex --root "D:/logs/ai-session" --python "C:/Users/<you>/AppData/Local/Programs/Python/Python313/python.exe"
```

安装脚本检测到 MSYS Python 时会打印提醒。
