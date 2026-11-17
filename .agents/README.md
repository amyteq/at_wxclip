# `.agents/`（Codex 侧）

Codex CLI 会从当前目录向上查找 `.agents/skills`，再回落到 `$HOME/.agents/skills` 和 `/etc/codex/skills`。

**部分 Codex 运行环境会把 `.agents/`、`.codex/` 挂载为只读**，所以本仓库不在这里放任何唯一事实源：

- 共享智能体约定的唯一事实源是仓库根的 `AGENTS.md`（可提交、不受挂载影响）；
- 若以后需要本机工作流 Skill，正文放在 `.claude/skills/<name>/SKILL.md`，这里只放**仓库内相对软链接**指向它，例如：

```text
.agents/skills/wx-capture -> ../../.claude/skills/wx-capture
```

目前没有软链接：仓库原先自带的两个开发流程 skill 已退役到 `.claude/archived/`，改由 gitmunk 的 `dev-skills` 插件提供，见 `README.md` 的「开发流程 skill」一节。
