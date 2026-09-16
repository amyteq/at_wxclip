# at-refine-issue 验证记录

记录日期：2026-09-17 · 分支：`dev/skills-port` · 对应 issue：#16

同样是离线验证，**没有改动任何 GitHub 状态**：没有改 issue 正文、没有发评论、没有加 label。#10 与 #11 只作为只读样本读过。下面两节里，「已执行」的给出真实输出，「推演」的对照写下来的规则说明会发生什么。

## 已执行：append / rewrite 的字节前缀判定

这是整个 skill 里唯一一条机器可判的核心规则 —— 一条已经派工的任务 comment，只允许**追加**，改写就必须停下来重新派工。判据是「旧正文是否仍是新正文的精确字节前缀」：

```bash
[ "$(head -c "$(stat -c%s old.md)" new.md | sha256sum | cut -d' ' -f1)" \
= "$(sha256sum old.md | cut -d' ' -f1)" ] && echo APPENDED || echo REWRITTEN
```

对四种正文各跑一次，`old.md` 是 176 字节的原始任务 comment：

| 候选正文 | 相对原文做了什么 | 大小 | 判定 | 期望 | 结论 |
|---|---|---|---|---|---|
| `unchanged.md` | 一字未改 | 176 B | `APPENDED` | `APPENDED` | PASS |
| `appended.md` | 末尾追加一个 `--- appended … ---` 块 | 236 B | `APPENDED` | `APPENDED` | PASS |
| `rewritten.md` | 改了标题里的一个词 | 188 B | `REWRITTEN` | `REWRITTEN` | PASS |
| `truncated.md` | 从中间截断 | 120 B | `REWRITTEN` | `REWRITTEN` | PASS |

截断这一行值得单独说：新正文比旧的短，`head -c 176` 只能取到 120 字节，哈希自然对不上，所以「删掉一段」被正确地归进 `REWRITTEN` 而不是漏判成追加。

## 已执行：issue 正文与 task comment 的 AC 列表逐字节相同

skill 要求这两份 AC 是**同一份、发布两次**。这条检查把两边的 AC 清单抽出来，逐字节比：

```text
=== AC list identity: issue body vs task comment ===
matching pair          body=85b1b74514393ef6  comment=85b1b74514393ef6  -> IDENTICAL
drifted pair (zh/en)   body=85b1b74514393ef6  comment=f681c1621b1212e3  -> DIFFERS
    first drift:
      body:    - [ ] **AC2 — No dangling references:** every relative Markdown link resolves on disk.
      comment: - [ ] **AC2 — No dangling references:** 所有相对链接都能在磁盘上解析.
```

第二行正是 #16 里那条语言决定的由来：body 用中文、comment 用英文，这条不变量直接不成立 —— 不是「难以维护」，是**判不了**。所以 AC 列表与验证矩阵两边都用英文，叙述段落保持中文，规则写在 `references/issue-body.md` 的「Language」一节。

## 推演：目标解析与 marker 采纳

| 场景 | 输入 | 观察到的结果 | 结论 | 方式 |
|---|---|---|---|---|
| 给的是 PR 链接而不是 issue | `/at-refine-issue https://github.com/amyteq/at_wxclip/pull/14` | REST 对象里 `is_pull_request: true`，停下来问，什么都不写 | PASS | 推演（`SKILL.md:29`、`SKILL.md:36`） |
| 目标缺失或有歧义 | 不给参数，会话里有两条候选 issue | 提一个问题，零个改变状态的远程调用 | PASS | 推演（`SKILL.md:30`、`SKILL.md:37`） |
| issue 已关闭或被锁 | `locked: true` 或 `state: "closed"` | 需要本次会话里人的明确确认，才允许写 | PASS | 推演（`SKILL.md:36`） |
| 重复运行，已有自己的 marker comment | 同一 repo / issue / 作者，正文仍含必需小节 | 采纳唯一候选并**原地编辑**，revision 不变、永久链接不变，不发第二条 | PASS | 推演（`SKILL.md:192`、`task-comment.md:33`） |
| marker 命中但作者不是本次运行的账号 | 另一个 login 写的 marker comment | 不采纳、不编辑、不作为 launch prompt 的指向；停下来报告 | PASS | 推演（`SKILL.md:186-188`） |
| marker 只是前缀匹配 | 正文以 marker 前缀开头，但首行不是本 repo / issue 的完整 marker | 当候选处理，三项检查过不了就不采纳 | PASS | 推演（`SKILL.md:186`） |
| 该有的都齐了，但没有 `decision:confirmed` | AC 有、owner 有、issue 是 open | 不打 `ai:ready`；打 `decision:needed` + `ai:blocked`，Execution 一节用 *Blocked* 那句状态 | PASS | 推演（`SKILL.md:121`、`SKILL.md:125`、`issue-body.md:82`） |
| 需要一个不存在的 label | 判定需要某个仓库里没有的 label | 报告缺失，不创建 | PASS | 推演（`SKILL.md:123`） |
| comment 发出去了但 issue 正文更新失败 | 第 1 步成功、第 2 步失败 | 按 marker 重新取回同一条 comment 复用其永久链接，只修 issue 正文；不发第二条 | PASS | 推演（`SKILL.md:201`） |
| 写操作结果未知（超时） | 连接中断 | 不盲目重试：按 marker 重查，只采纳过三项检查的候选 | PASS | 推演（`SKILL.md:200`） |
| 分不清是否已派工 | 没有 `--- dispatched … ---`，也没有人明说 | fail-closed，按**已派工**处理，因此只允许追加 | PASS | 推演（`SKILL.md:164`） |

`ai:ready` 的晋级条件与「只用已存在的 label」这条，出处是 `AGENTS.md` 的 Engineering 一节和 `.github/labels.yml` 的头注释，两处必须一致。原版把这套规则放在一个 triage skill 和一份工程实践文档里，那两样本仓库都不引入，规则因此改挂在上面两处 —— label 名进来了，判定标准也得跟着进来。

## 一条没有验证的东西

这两条记录都没有对真实 PR 或真实 issue 做过一次完整的写入循环。#16 的非目标里写明了这一点：跑完整循环需要一条正在飞的 PR。第一次真实使用建议挑一条低风险的 PR，并在事后把观察到的差异补进这两份记录。
