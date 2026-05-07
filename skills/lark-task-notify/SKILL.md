---
name: lark-task-notify
description: >
  飞书任务通知：在执行长时间思考任务时，通过飞书 CLI (lark-cli) 向用户发送"正在思考中"的进度通知，任务完成后发送结构化结果摘要。
  当用户提到"飞书通知"、"lark 通知"、"任务完成通知"、"思考通知"、"lark-task-notify"、或者当 Claude 判断当前任务较复杂可能需要较长时间时，应使用此 skill。
  也适用于用户说"完成后告诉我"、"做完通知我"、"思考中通知我"等场景。
---

# 飞书任务通知 (lark-task-notify)

## 何时使用

当 Claude 判断当前任务较复杂（涉及多步骤操作、大量代码修改、长时间分析等），预计用户需要等待较长时间时，主动使用此 skill 发送飞书通知。

典型触发场景：
- 多文件重构、大规模代码修改
- 需要多步调试的问题排查
- 复杂的数据分析或报告生成
- 用户明确要求"完成后通知我"

## 前置条件

### 1. 飞书 CLI 已安装并配置

确认 `lark-cli` 可用且已认证。运行 `lark-cli auth status` 检查。

### 2. 配置通知目标

你需要知道自己的飞书 open_id（`ou_` 开头）或与 bot 的 P2P chat_id（`oc_` 开头）。

**获取方式（任选其一）：**

- 如果已登录 user 身份（`lark-cli auth login`）：
  ```bash
  lark-cli contact +search-user --query "你的名字" --as user --format pretty
  ```
  记下返回的 `open_id`。

- 如果只有 bot 身份：先在飞书客户端给应用机器人发一条消息建立 P2P 会话，然后：
  ```bash
  lark-cli im chats list --as bot
  ```
  找到对应 chat 的 `chat_id`。

### 3. 保存配置

在 skill 目录下创建 `.lark-notify.json`（与本 SKILL.md 同目录）：

```json
{
  "notify_target": {
    "user_id": "ou_xxxxx",
    "chat_id": "oc_xxxxx"
  },
  "prefer": "chat_id"
}
```

`prefer` 决定优先使用哪种方式发送：`chat_id` 或 `user_id`。

> **安全提示**：`.lark-notify.json` 已包含个人 ID，上传 GitHub 前建议将真实 ID 替换为占位符（如 `ou_xxxxx`），或加入 `.gitignore`。

## 工作流程

### 第一步：开始思考时 — 发送"思考中"通知

当判断任务较复杂时，**在开始处理之前**发送通知：

```bash
lark-cli im +messages-send \
  --{{发送方式}} "{{TARGET_ID}}" \
  --markdown "⏳ **Claude 任务进行中**
任务：{{简要任务描述，20字以内}}
状态：正在思考/执行中，请稍候…" \
  --as bot
```

其中 `{{发送方式}}` 根据配置：
- `prefer: "chat_id"` → 使用 `--chat-id`
- `prefer: "user_id"` → 使用 `--user-id`

### 第二步：任务完成后 — 发送结构化结果

任务完成后，发送结构化摘要通知：

```bash
lark-cli im +messages-send \
  --{{发送方式}} "{{TARGET_ID}}" \
  --markdown "✅ **Claude 任务完成**
---
**任务**：{{任务简要描述}}
**状态**：{{成功/失败}}
**关键结论**：
{{核心结果，要点列表}}
**输出位置**：
{{修改的文件路径或输出文件路径}}
{{如有后续建议，列出}}" \
  --as bot
```

### 第三步：任务失败时 — 发送失败通知

如果任务执行中途出错：

```bash
lark-cli im +messages-send \
  --{{发送方式}} "{{TARGET_ID}}" \
  --markdown "❌ **Claude 任务失败**
---
**任务**：{{任务简要描述}}
**错误原因**：{{错误信息}}
**当前进度**：{{已完成到哪一步}}" \
  --as bot
```

## 读取配置的步骤

每次发送通知前，按以下顺序查找配置文件：

1. Skill 目录下的 `.lark-notify.json`（与本 SKILL.md 同目录）
2. 当前项目目录下的 `.lark-notify.json`
3. 用户主目录 `~/.lark-notify.json`

如果未找到配置文件，提示用户先完成配置（参见"前置条件"第3步），不要跳过通知直接继续。

## 消息格式规范

- 使用 `--markdown` 参数发送，飞书会自动渲染富文本
- 任务描述控制在 20 字以内，简洁明了
- 关键结论用要点列表，每项一行，以 `- ` 开头
- 输出位置列出具体文件路径，方便用户直接查看
- 不要在消息中包含大段代码，只放结论和路径

## 注意事项

- 发送飞书消息本身不应打断主要任务流程。发送通知是轻量操作，应快速完成。
- 如果飞书 CLI 发送失败，不要因此阻塞主任务。记录错误并继续执行主要任务。
- 不要对简单任务（单文件修改、简单查询等）发送"思考中"通知，避免打扰用户。
- 同一个任务只发一次"思考中"通知和一次"完成/失败"通知，不要重复发送。
