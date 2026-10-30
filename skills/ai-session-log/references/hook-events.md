# Hook 事件格式

`chatlog_hook.py` 从 stdin 读一个 JSON 对象。字段名允许别名，未知字段忽略。

## 1. 归一化后的事件

| 字段 | 必填 | 说明 |
|---|---|---|
| `hook_event_name` | 是 | `UserPromptSubmit` / `PostToolUse` / `Stop` / `SubagentStop` / `Interrupt` |
| `session_id` | 是 | 会话 ID；用于按会话聚合 |
| `turn_id` | 否 | 轮次 ID；有则精确配对，无则用“最新 open 记录”兜底 |
| `prompt` | UserPromptSubmit | 用户提问 |
| `last_assistant_message` | Stop | 最终回答 |
| `tool_name` | PostToolUse | 工具名 |
| `tool_input` | PostToolUse | 工具参数（dict / str） |
| `tool_response` | PostToolUse | 工具输出 |
| `cwd` / `model` | 否 | 记录环境信息 |
| `transcript_path` | 否 | Claude Stop 事件里可读最近 assistant 文本作为兜底 |

## 2. 字段别名

脚本会依次尝试：

```text
session_id      : session_id, sessionId, conversation_id, conversationId, thread_id, threadId
turn_id         : turn_id, turnId, request_id, requestId
prompt          : prompt, user_prompt, userPrompt, message, input
answer          : last_assistant_message, lastAssistantMessage, assistant_message,
                  response, answer, output
tool_name       : tool_name, toolName, tool
tool_input      : tool_input, toolInput, arguments, input
tool_response   : tool_response, toolResponse, result, output
cwd             : cwd, workspace, working_directory, workingDirectory
model           : model, model_name, modelName
```

事件名别名包括：`before_agent` / `beforeAgent` / `beforeSubmitPrompt` → `UserPromptSubmit`；
`after_agent` / `afterAgent` / `subagent_stop` / `session_end` → `Stop`；
`after_tool` / `afterTool` / `tool_result` → `PostToolUse`；
`cancel` / `abort` / `user_interrupt` → `Interrupt`。

## 3. Codex

`~/.codex/hooks.json`：

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent codex" }] }],
    "PostToolUse":      [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent codex" }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent codex" }] }],
    "Interrupt":        [{ "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent codex" }] }]
  }
}
```

Codex 会带 `turn_id`，因此 UserPromptSubmit / PostToolUse / Stop 能精确配对到同一轮。
Codex 要求 hook 经过信任（`~/.codex/config.toml` 的 `[hooks.state]`），首次运行按提示确认即可。

## 4. Claude Code

`~/.claude/settings.json`：

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent claude" }] }],
    "PostToolUse":      [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent claude" }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent claude" }] }],
    "SubagentStop":     [{ "hooks": [{ "type": "command", "command": "python .../chatlog_hook.py --agent claude" }] }]
  }
}
```

Claude Code **不提供 `turn_id`**，脚本用 `session_id + __open__` 指针把 PostToolUse/Stop 挂到该会话最新的 open 记录上。
`Stop` 事件若无 `last_assistant_message`，脚本会尝试读 `transcript_path` 的最后一条 assistant 文本。

## 5. 其他工具（generic）

任何支持“执行命令并把 JSON 写到 stdin”的工具都可以接入：

```bash
python scripts/chatlog_hook.py --root /path/to/store --agent cursor
```

只要事件里能提供 `hook_event_name` + `session_id`，以及对应的 `prompt` / `last_assistant_message` / `tool_*`，即可记录。

## 6. 手工测试

```bash
echo '{"hook_event_name":"UserPromptSubmit","session_id":"demo","turn_id":"t1","prompt":"hello"}' | python scripts/chatlog_hook.py --root ./tmp-log --agent generic
echo '{"hook_event_name":"Stop","session_id":"demo","turn_id":"t1","last_assistant_message":"hi"}' | python scripts/chatlog_hook.py --root ./tmp-log --agent generic
python scripts/query_log.py --root ./tmp-log show demo
```
