# at-review-pr 验证记录

记录日期：2026-09-17 · 分支：`dev/skills-port` · 对应 issue：#16

这份记录是 `at-review-pr` 移植后的离线验证。**全程没有改动任何 GitHub 状态**：没有发评论、没有提交 review、没有加 label。真实 PR 上跑一遍完整循环不在这条 issue 的范围里（那需要一条正在飞的 PR），所以下面分两类：**已执行**的检查给出命令与原始输出，**推演**的场景逐条对照写下来的规则说明会发生什么。

## 环境

| 项 | 值 |
|---|---|
| `codex --version` | `codex-cli 0.154.0` |
| `claude --version` | `2.1.273 (Claude Code)` |
| `gh --version` | `gh version 2.100.0 (2026-09-04)` |
| `gh api user --jq .login` | `harrysun2006` |

这三个版本正是 `references/agents.md` 里声明「已对照验证」的那几个，所以启动形态的检查结果对本机有效；换一台机器要重跑 `codex exec --help` 与 `claude --help`。

## 启动形态（AC5，已执行）

`references/agents.md` 里的四条启动命令全部通过 `bash -n`：

```text
bash blocks found: 4
form 1: bash -n exit=0
form 2: bash -n exit=0
form 3: bash -n exit=0
form 4: bash -n exit=0
```

四条命令用到的每个 flag 都在本机 help 里存在，取值也对得上：

| Flag | 来源 | help 里的形态 |
|---|---|---|
| `-C` | `codex exec --help` | `-C, --cd <DIR>` |
| `--model` | `codex exec --help` | `-m, --model <MODEL>` |
| `--config` | `codex exec --help` | `-c, --config <key=value>` |
| `--sandbox` | `codex exec --help` | `-s, --sandbox <SANDBOX_MODE>`，可选值含 `read-only`、`workspace-write` |
| `--ephemeral` | `codex exec --help` | `--ephemeral` |
| `-p` | `claude --help` | `-p, --print` |
| `--model` | `claude --help` | `--model <model>` |
| `--effort` | `claude --help` | `--effort <level>`，可选值 `low, medium, high, xhigh, max` |
| `--no-session-persistence` | `claude --help` | `--no-session-persistence` |
| `--output-format` | `claude --help` | `--output-format <format>`，可选值 `"text", "json", "stream-json"` |
| `--tools` | `claude --help` | `--tools <tools...>` |
| `--strict-mcp-config` | `claude --help` | `--strict-mcp-config` |
| `--mcp-config` | `claude --help` | `--mcp-config <configs...>` |

两处要留意：`--effort xhigh` 是 help 明确列出的合法取值，所以 `agents.md` 里「传了非法值只会打 warning 然后静默回落」的那条，判据是 stderr 干净而不是退出码；`--output-format json` 是单结果模式，不发 `init` 事件，所以 Claude 那边能验的是 model / context / session / completion 四项，tools 与 permission mode 只有 `stream-json` 才有。

## 场景矩阵（AC6）

| 场景 | 输入 | 观察到的结果 | 结论 | 方式 |
|---|---|---|---|---|
| 轮数为零 | `/at-review-pr 0` | 在任何写操作与会话启动之前拒绝，并指出接受的写法：十进制正整数 | PASS | 推演（`SKILL.md:21`「Only an absent `rounds` takes the default」） |
| 轮数是小数 | `/at-review-pr 1.5` | 同上，按非整数拒绝 | PASS | 推演（同上） |
| 未知 provider | `/at-review-pr 3 gemini claude` | 拒绝，`reviewer` 只接受 `codex` / `claude` | PASS | 推演（`SKILL.md:14-17` 参数表） |
| 多出一个位置参数 | `/at-review-pr 3 codex claude extra` | 拒绝，位置参数只有三个 | PASS | 推演（`SKILL.md:21`） |
| `pr=` 写法不对 | `/at-review-pr pr=#14`、`pr=https://…`、`pr=amyteq/at_wxclip#14` | 拒绝并说明只接受裸数字，不做「修正」 | PASS | 推演（`SKILL.md:19`） |
| `pr=` 不在首位 | `/at-review-pr 3 pr=14` | 拒绝，`pr=` 只在第一个 token 位置被识别 | PASS | 推演（`SKILL.md:19-21`） |
| 目标缺失或有歧义 | 不给 `pr=`，当前分支没有唯一的 open PR | 提一个问题，零个改变状态的远程调用 | PASS | 推演（`SKILL.md:24`） |
| `pr=` 与分支不一致 | `pr=14`，但当前分支对应另一条 PR | 以 `pr=` 为准，并在启动前的确认里写明这次覆盖，不静默切换 | PASS | 推演（`SKILL.md:24`） |
| 最后一个 writer 阶段之后 | 预算用尽 | 跑一次只读的完整 review；它不再授予额外的 writer 阶段 | PASS | 推演（`SKILL.md:63`） |
| 批准前 head 变了 | 远程 head 与快照不符 | 停下来对账，不批准过期的 diff | PASS | 推演（`SKILL.md:56`、`SKILL.md:82`） |
| PR 由当前登录账号创建 | GitHub 拒绝自评 | 走 `approval-eligible` 评论路径，并**以成功收尾**，不再多跑一轮 | PASS | 推演（`SKILL.md:84`） |

上面标「推演」的行都不需要联网：判据是写下来的规则本身，行末的 `文件:行` 就是出处。真正要联网的只有一条 —— 对着一条在飞的 PR 跑完整循环，那条明确在 #16 的非目标里。

## 那条「假发现」是什么

`references/agents.md` 的 handoff packet 一节要求每个阶段的 validation 行只写**真的跑过**的工具和命令，同时又要求 coordinator 在据此否定一个阶段之前，先用 `command -v <name>` 和 help 确认这个工具确实不存在。

两条规则一起才成立，原因是这个失效模式：reviewer 看到 validation 行里一个仓库树里搜不到的工具名，就判它是编造的。但**宿主机上的工具本来就不出现在仓库树里** —— `gh`、`codex`、`claude`、`make` 都是这样。「这个仓库里没用过它」不等于「它不存在」。把一份诚实的报告判成编造，比放过一条含糊的 validation 行更糟：前者会让一个真实修复被退回，还会教会后面的会话少写证据。

所以判据是可执行的，不是印象：跑 `command -v`，跑它的 help，两样都拿不到才算数。

## 仓库检查（AC8）

```text
$ make lint test
.venv/bin/python -m ruff check .
All checks passed!
.venv/bin/python -m ruff format --check .
57 files already formatted
.venv/bin/python -m pytest
404 passed in 20.66s

$ git diff --check
(无输出，exit=0)
```

这次改动只有 Markdown、软链接和文档，不碰 `wxclip/`、`web/`、`tests/`，所以 404 passed 是**未变的基线**，不是新增覆盖。`git status --porcelain` 里没有来自 `downloads/`、`captures/`、`processed/`、`temp/` 的任何条目，也没有 `.env`。
