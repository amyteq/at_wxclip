# Claude Code 项目说明

开始工作前依次阅读 `AGENTS.md`、`README.md`、`docs/项目背景.md` 和 `docs/开发计划.md`。

Claude Code 遵守 `AGENTS.md`：共享的智能体约定只写在那里，本文件不重复。代码、标识符、
commit message 用英文；面向人的说明文档默认用简体中文。

- 权限与工具配置：`.claude/settings.json`（提交进 Git）
- 本机个人配置：`.claude/settings.local.json`（不提交，模板见同名 `.example` 文件）
- 目录职责：`docs/目录说明.md`
- 提交前请通过 `make lint test`
