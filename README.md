# AIProject

AI development tools, skills, and utilities collection. / AI 开发工具、技能和实用脚本合集。

## Overview / 概述

This repository collects AI-related development tools, primarily custom skills for [Claude Code](https://claude.ai/code) and integrations with platforms like [Feishu/Lark](https://open.feishu.cn/).

本仓库收集 AI 相关的开发工具，主要包括 [Claude Code](https://claude.ai/code) 的自定义 Skill 以及与飞书等平台的集成工具。

## Skills / 技能

Custom skills for Claude Code CLI that extend its capabilities.

Claude Code CLI 的自定义 Skill，扩展其能力。

| Skill | Description / 描述 |
|-------|-------------------|
| [lark-task-notify](./skills/lark-task-notify/) | Feishu/Lark notification for long-running Claude tasks / 飞书任务通知：长时间思考任务进度提醒与结果摘要 |

## Structure / 目录结构

```
AIProject/
├── skills/                  # Claude Code custom skills / 自定义 Skill
│   └── lark-task-notify/    # Feishu task notification / 飞书任务通知
│       ├── SKILL.md         # Skill definition / Skill 定义文件
│       ├── .lark-notify.json # Notification config (template) / 通知配置（模板）
│       └── evals/           # Test cases / 测试用例
└── README.md
```

## Getting Started / 快速开始

1. Clone the repository:
   ```bash
   git clone https://github.com/shurui190/AIProject.git
   ```

2. Copy the skill you need to your Claude Code skills directory:
   ```bash
   cp -r skills/lark-task-notify ~/.claude/skills/
   ```

3. Follow the setup instructions in each skill's `SKILL.md`.

---

## License

MIT
