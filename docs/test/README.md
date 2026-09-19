# 测试与验证记录

放**人工/半自动验证的记录**：跑了什么、看到什么、判据是什么。自动化测试在 `tests/`，这里只收那些跑不进 pytest 的东西 —— 需要真实机器、真实客户端、或者外部 CLI 的验证。

| 文件 | 内容 |
|---|---|
| `at-review-pr.md` | `at-review-pr` skill 的离线验证：启动形态与 flag 核对、参数与循环场景矩阵、仓库检查 |
| `at-refine-issue.md` | `at-refine-issue` skill 的离线验证：字节前缀判定、AC 列表逐字节比对、目标解析与 marker 采纳场景 |

## 两个 skill 确实被 harness 发现（AC1）

两条记录共用这一项，所以写在索引里。在仓库根起一个全新的 Claude Code 会话（`claude -p`，不带 MCP server），问它列出名字以 `at-` 开头的 skill：

```text
Two skills start with "at-": at-refine-issue and at-review-pr.
```

也就是说 `.claude/skills/<name>/SKILL.md` 这个落位确实被 harness 认出来了，不需要额外注册。要注意的是**那份清单只给 name 和 description，不给 `argument-hint`** —— 所以「frontmatter 有 `argument-hint` 和 `disable-model-invocation: false`」这半条是另外用 YAML 解析验的，不是从清单里读出来的：

```text
.claude/skills/at-review-pr/SKILL.md:    keys=['argument-hint', 'description', 'disable-model-invocation', 'name']  name='at-review-pr'     disable-model-invocation=False
.claude/skills/at-refine-issue/SKILL.md: keys=['argument-hint', 'description', 'disable-model-invocation', 'name']  name='at-refine-issue'  disable-model-invocation=False
```

两份 frontmatter 都能被 `yaml.safe_load` 解析，四个必需字段齐全，`name` 与目录名一致。

写记录的两条规矩：**已执行**的给原始输出，**推演**的注明是推演并给出规则出处（`文件:行`）；改动了 GitHub 状态的要写清楚改了什么，没改的也明说没改。
