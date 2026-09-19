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
| 给的是 PR 链接而不是 issue | `/at-refine-issue https://github.com/amyteq/at_wxclip/pull/14` | REST 对象里 `is_pull_request: true`，停下来问，什么都不写 | PASS | 推演（`SKILL.md:34`「`is_pull_request: true` means the number is a pull request and not a valid target — stop and ask」、`SKILL.md:35`「zero state-changing remote calls」） |
| 目标缺失或有歧义 | 不给参数，会话里有两条候选 issue | 提一个问题，零个改变状态的远程调用 | PASS | 推演（`SKILL.md:26`「Two candidates … means ask. Do not guess」、`SKILL.md:35`「A missing or ambiguous target produces a question and zero state-changing remote calls」） |
| issue 已关闭或被锁 | `locked: true` 或 `state: "closed"` | 需要本次会话里人的明确确认，才允许写 | PASS | 推演（`SKILL.md:34`「needs the human's explicit confirmation in this session before any write to that issue」） |
| 目标在另一个仓库 | 一条指向别的 repo 的完整 issue URL | 用 URL 的仓库，并按**那个**仓库的约定细化；它没有的东西就报告或问，不把本仓库的约定搬过去 | PASS | 推演（`SKILL.md:25`「refine against *that* repository's conventions」「never import this repository's conventions into another repository's issue」） |
| 另一个仓库：模板里与仓库绑定的那些值 | 同上，开始往模板里填 | 每个与仓库绑定的值都是具名槽位：中间列是要求，第三列只是本仓库的实例；换仓库就按那个仓库的等价物填，没有等价物就报告，不照抄第三列，也不把没替换的 `<slot>` 发出去 | PASS | 推演（`task-comment.md:57`「the third column is this repository's *instance*, not the requirement」、`task-comment.md:70`「fill them from *that* repository's own instructions and never from the third column」、`issue-body.md:21`「report any slot that repository has no equivalent for」） |
| 重复运行，已有自己的 marker comment | 同一 repo / issue / 作者，正文仍含必需小节，且 issue 的 Execution 正指着它 | 采纳并**原地编辑**，revision 不变、永久链接不变，不发第二条 | PASS | 推演（`SKILL.md:186` 现行版判定、`SKILL.md:234`「Edit in place, keeping revision `r`」、`task-comment.md:19`「Editing a comment in place to add detail keeps its revision」） |
| 替换过一次，链上有两条 revision | 两条都过 marker / 作者 / 必需小节三项检查 | 三项检查判不出哪条是现行的；由 issue 的 Execution 链接定，其余一律不编辑、不重指、不进 launch prompt | PASS | 推演（`SKILL.md:175`「It cannot ask "is it the live one"」、`SKILL.md:177`「The issue's Execution section names the current revision, and nothing else does」、`SKILL.md:186`） |
| Execution 指向的 id 不在候选里 | 那条 comment 作者不对、缺小节，或已被删 | 停下来报告那个 id 和它没过的那项检查，不拿另一条候选顶上 | PASS | 推演（`SKILL.md:187`「Never substitute another candidate for it」） |
| 多条候选，而 issue 没指明 | 没有 Execution 小节，或里面有两个以上 id | 停下来，列出每条候选的 `revision` / `updated_at` / 永久链接并问，不按 revision 号大小猜 | PASS | 推演（`SKILL.md:189`「Stop. Report every candidate … and ask which is current」、`SKILL.md:177`「Not the highest `revision=` number」） |
| marker 命中但作者不是本次运行的账号 | 另一个 login 写的 marker comment | 不采纳、不编辑、不作为 launch prompt 的指向；停下来报告 | PASS | 推演（`SKILL.md:166`「never edited, never adopted as this run's work, and never linked from the issue as the task」） |
| marker 只是前缀匹配 | 正文以 marker 前缀开头，但首行不是本 repo / issue 的完整 marker | 当候选处理，三项检查过不了就不采纳 | PASS | 推演（`SKILL.md:163`「Adopt one only when all three hold」、`SKILL.md:165`「a prefix match alone is not enough」） |
| 该有的都齐了，但没有 `decision:confirmed` | AC 有、owner 有、issue 是 open | 不打 `ai:ready`；打 `decision:needed` + `ai:blocked`，Execution 一节用 *Blocked* 那句状态 | PASS | 推演（`SKILL.md:125`「`decision:confirmed` is present where a decision is involved」、`SKILL.md:129`「Do not paper over it to reach `ai:ready`」、`issue-body.md:96` 的 *Blocked* 状态句） |
| 需要一个不存在的 label | 判定需要某个仓库里没有的 label | 报告缺失，不创建 | PASS | 推演（`SKILL.md:127`「A needed-but-missing label is reported, not created」） |
| comment 发出去了但 issue 正文更新失败 | 第 1 步成功、第 2 步失败 | 按 marker 重新取回同一条 comment 复用其永久链接，只修 issue 正文；不发第二条 | PASS | 推演（`SKILL.md:188` 现行版判定的第三行、`SKILL.md:239`「Re-fetch it by marker, reuse that same permalink, and repair only the issue body」） |
| 写操作结果未知（超时） | 连接中断 | 不盲目重试：按 marker 重查，只采纳过三项检查的候选，再按 Execution 链接定哪条是现行的 | PASS | 推演（`SKILL.md:238`「Do not retry blindly. Re-query by marker」） |
| 分不清是否已派工 | 没有 `--- dispatched … ---`，也没有人明说 | fail-closed，按**已派工**处理，因此只允许追加 | PASS | 推演（`SKILL.md:200`「treat the comment as dispatched unless this run can point to evidence that it is not」） |
| 判「是否已派工」要看的信号带不带时间戳 | 想知道 label / assignee 是不是在 task comment 之后加的 | `--json assignees,labels` 只给当前状态、没有时间戳，判不了「之后加的」；改用 timeline 按 `created_at` 过滤，取不到时间戳的信号一律回落到 fail-closed | PASS | 推演（`SKILL.md:213`「reports only the *current* assignees and labels and carries no timestamps」、`SKILL.md:222`「Any signal this run cannot timestamp is not evidence that the task is undispatched」） |

**行号按本记录所在提交时的 `.claude/skills/at-refine-issue/SKILL.md`（以及同目录 `references/` 下的两份）计**；行号会随后续编辑漂移，引号里的原文和下面的小节归属才是耐久的锚点，对不上时以后者为准重新定位：目标解析在 “Inputs and preflight”（`SKILL.md:14-37`），晋级条件在 “Readiness, not dispatch”（`SKILL.md:123-131`），marker 采纳、现行版判定与派工判定都在 “Revisions, concurrency, recovery”（`SKILL.md:147-251`，其中现行版判定在 “Which revision is current” `SKILL.md:173-196`、派工判定在 “Has this task been dispatched?” `SKILL.md:198-251`）。两份 reference 里，槽位表在 `task-comment.md:55-70` 与 `issue-body.md:9-21`，现行版判定的同一条规则在 `task-comment.md:30-53`。

`ai:ready` 的晋级条件与「只用已存在的 label」这条，出处是 `AGENTS.md` 的 Engineering 一节和 `.github/labels.yml` 的头注释，两处必须一致。原版把这套规则放在一个 triage skill 和一份工程实践文档里，那两样本仓库都不引入，规则因此改挂在上面两处 —— label 名进来了，判定标准也得跟着进来。

## 一条没有验证的东西

这两条记录都没有对真实 PR 或真实 issue 做过一次完整的写入循环。#16 的非目标里写明了这一点：跑完整循环需要一条正在飞的 PR。第一次真实使用建议挑一条低风险的 PR，并在事后把观察到的差异补进这两份记录。
