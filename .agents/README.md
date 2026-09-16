# `.agents/`（Codex 侧）

Codex CLI 会从当前目录向上查找 `.agents/skills`，再回落到 `$HOME/.agents/skills` 和 `/etc/codex/skills`。

**部分 Codex 运行环境会把 `.agents/`、`.codex/` 挂载为只读**，所以本仓库不在这里放任何唯一事实源：

- 共享智能体约定的唯一事实源是仓库根的 `AGENTS.md`（可提交、不受挂载影响）；
- 若以后需要本机工作流 Skill，正文放在 `.claude/skills/<name>/SKILL.md`，这里只放**仓库内相对软链接**指向它，例如：

```text
.agents/skills/wx-capture -> ../../.claude/skills/wx-capture
```

目前还没有 Skill，`skills/` 是空目录占位。
