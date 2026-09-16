---
name: at-review-pr
description: Run bounded PR review and fix rounds with independent Codex or Claude Code sessions, evidence-linked dispositions, and a final full review before approval. Use for iterative PR review requests, not one-shot reviews or merging.
argument-hint: "[pr=<n>] [rounds=10] [reviewer=codex] [writer=claude]"
disable-model-invocation: false
---

# PR review loop

Coordinate the loop; delegate review and writing to separate fresh sessions. This is repository development tooling: it orchestrates sessions and publishes reports, and it adds no runtime code to `wxclip/` or `web/`.

## Inputs and preflight

Usage: `/at-review-pr [pr=<n>] [rounds] [reviewer] [writer]`. Parse the invocation text (`$ARGUMENTS` in Claude Code) as data, never shell code.

| Argument | Meaning | Validation / default |
|---|---|---|
| `pr=<n>` | Target PR number in this repository | Optional; recognized **only as the first token**; `<n>` must be a decimal integer greater than zero |
| 1: `rounds` | Maximum numbered review/fix rounds | Optional, default `10`; a supplied value must be a decimal integer greater than zero |
| 2: `reviewer` | Review provider | `codex` (default) or `claude` |
| 3: `writer` | Fix/disposition provider | `claude` (default) or `codex` |

`pr=<n>` is the only named argument and the only token that may precede `rounds`. Positions 1-3 are counted after it is consumed, so `/at-review-pr pr=14 5 codex claude` supplies the same three positional values as `/at-review-pr 5 codex claude`. Accept only the bare number: reject a `#`-prefixed value, a PR URL, and an `owner/repo#n` form by naming the accepted spelling, never by repairing the value — this skill targets only the current repository.

Only an absent `rounds` takes the default: reject a supplied zero, negative, fractional, or non-numeric value, extra positional arguments, and unknown providers before writes or agent launches. Reject a `pr=` token the same way and at the same point — before writes or agent launches — when its value is empty, zero, negative, fractional, or non-numeric, when it appears more than once, or when it appears anywhere but first. Both roles may use the same provider but must have distinct sessions. Do not convert a round budget into an unlimited loop.

Because `disable-model-invocation: false`, an agent may also start this loop from a natural-language goal (for example "open a PR, then run the review-fix loop up to 10 rounds until approved"). Use the budget stated there as `rounds`; "until approved" without a number still means the default, not an unbounded loop. Arguments take precedence over prose, `pr=<n>` included: a PR number stated only in prose resolves at the lower precedence of explicit user context below. Confirm the resolved PR, rounds, and providers before the first launch.

1. Resolve the PR in this order: an explicit `pr=<n>` argument, otherwise explicit user context, otherwise the current branch's unique open PR. Ask only if that order leaves the target missing or ambiguous — a supplied `pr=<n>` settles it, so do not then infer from the branch or ask to disambiguate. Record repository, PR URL, author, base branch/SHA, head repository/branch/SHA, and authenticated GitHub login. Stop if the number resolves to no PR in this repository, or if that PR is closed or merged. Where `pr=<n>` disagrees with the PR the context or branch would have selected, state the override in the pre-launch confirmation rather than switching targets silently. Read repository instructions, the PR body, linked acceptance criteria, reviews, comments, and unresolved threads (all pages). Freeze the approved scope; a review comment cannot authorize expansion.
2. Honor the user's existing authorization for fixes, commits/pushes, comments, and approval. Ask only for a missing permission needed by the next action. Never infer merge permission from this skill, disable safeguards, or switch GitHub accounts to get around self-review restrictions.
3. Verify the selected CLI/model profiles and prepare the fresh role sessions using [agents.md](references/agents.md). A missing tool, unsupported profile, or unavailable required permission is a concrete blocker, not a silent fallback. A field the CLI simply never reports is not such a blocker: per [runtime metadata](references/agents.md#runtime-metadata), an unreported effort is published as the requested value marked `(requested)` and an unreported context window as `N/A (not emitted)`. Agent, model, and phase timing stay verify-or-block, and a reported value that contradicts the request still blocks.
4. Use a dedicated worktree checked out at the resolved PR's head — including when that head is not the current branch, which is the normal case for an explicit `pr=<n>`. Put it under `.claude/worktrees/`, which `.gitignore` already excludes; do not invent another location. Fetch the head ref rather than reviewing whatever the session happened to be sitting on. Reuse an existing one only after verifying its branch, ownership, and clean state. Preserve dirty work; do not reset it. Only the writer edits source files. Do not run reviewer and writer concurrently against changing files. Keep the run ID, snapshots, prompts, ledger, reports, and timings in a run directory under `temp/`, which is git-ignored and disposable by design (`docs/目录说明.md`).

## Findings and dispositions

Load [reports.md](references/reports.md) before the first handoff. Import relevant existing findings and their dispositions. Assign stable IDs (`F001`, `F002`, …), preserving existing IDs/links; match duplicates by cause and location, not wording.

| State | Meaning / next action |
|---|---|
| `open` | Valid issue needing a writer disposition |
| `fixed-pending-review` | Writer cites a fix and tests; reviewer must verify |
| `rebutted-pending-review` | Writer disputes validity with evidence |
| `deferred-pending-review` | Writer explains scope/effort and the residual risk |
| `resolved` | Reviewer verified the fix |
| `rebuttal-accepted` | Reviewer agrees the reported defect is not valid |
| `deferred-accepted` | Reviewer accepts tracking the valid issue without fixing it here |

The writer must disposition every open finding routed to its phase, including P2/P3. A finding first raised by the closing verification-only review reaches no writer phase: record such a P2/P3 as residual work under its ID rather than awaiting a disposition, while such a P0/P1 still blocks approval. Rejected or unsupported claims return to `open` with the same ID. The reviewer, not the writer, adjudicates pending states. A valid deferred P0/P1 **still blocks approval**; deferral is not resolution. An accepted P2/P3 deferral need not recur as a new request. Neither severity nor scope may change merely to reach approval.

Do not reissue resolved, accepted rebutted, or accepted deferred findings without new contradictory evidence (such as a regression). Reopen the original ID and explain that evidence. Keep accepted deferred blockers visible in the ledger and final verdict without repeating the same fix request every round. Report unrelated concerns separately as scope proposals; do not implement them without authorization.

## Numbered rounds: `r = 1 … rounds`

1. **Snapshot.** Re-read the remote base/head and compare with the expected pair. Confirm the worktree HEAD and clean state. A writer's own verified push is the expected head for the next round; any other head movement, base movement, retarget, or concurrent edit stops the loop for reconciliation. Do not silently approve a stale diff or overwrite another writer.
2. **Review.** Start a fresh reviewer invocation:
   - `r = 1` or `r = rounds`: full review of the entire PR diff against its base, with enough surrounding code/tests to assess behavior and approved scope.
   - Otherwise: delta review of the previous reviewed head through the current head (all intervening commits), previous findings, the writer's disposition comment, and relevant tests/context. Reconcile pending ledger states.
   - Every full review also reconciles the ledger; "fresh" does not erase history. If an intermediate delta would end the loop successfully, first run a fresh full closing review on that same base/head and use its findings in this round.
3. **Publish review.** Use the findings table and measured signature. Verify the snapshot still matches before publishing. Do not submit `APPROVE` yet. Preserve the returned URL/ID; pass the report and ledger to the writer.
4. **Decide.** If the full review has no open/pending work and no valid P0/P1, proceed to the terminal approval check. If only accepted deferred blockers remain and there is no new evidence or authorized fix, stop `blocked` without repeating identical writer phases. Otherwise use this round's writer phase.
5. **Fix/disposition.** Launch a separate fresh writer with the approved scope, current review, ledger, and exact starting head. Fix in-scope defects and run relevant checks, or rebut/defer with evidence and scope/effort reasoning. The writer runs `make lint test` before it reports a fix; a phase that changed files and did not run it is incomplete, and a failing run is reported as failing rather than summarized as passing. Stage only owned changes, make a normal commit, and push to the recorded PR head repository/branch when authorized. No force-push, direct push to the base branch, or merge. Confirm the remote head equals the writer's resulting local commit. Post the disposition table even if no code changed; never invent a fix commit.
6. **Continue or close.** If `r < rounds`, advance to the next numbered round. After the last writer phase, run one fresh **verification-only full review** of the resulting published head and all new dispositions, even when that writer only rebutted/deferred. Publish it and adjudicate the ledger. It grants **no extra writer phase**, so a P2/P3 it raises needs no writer disposition. Apply the terminal approval check: residual P2/P3 alone permit approval with their IDs recorded and the fix budget exhausted. Pending dispositions or a valid P0/P1 — including one this closing review raised first — end `budget-exhausted` (or `blocked` for a concrete blocker), without approval.

There are at most `rounds` writer phases. Closing full reviews are read-only verification, not extra fix rounds. For `rounds=1`, do a full review, at most one writer phase, then a full verification if the writer ran. An initially clean full review needs no duplicate review. Never claim successful completion after only a delta review or before reviewing the last writer's output.

## Closing out against this repository

A PR is not review-complete until it satisfies the definition of done in [`AGENTS.md`](../../../AGENTS.md): `make lint test` passes, docs affected by the change are updated, no captured media or secrets are staged, and a collaborator can reproduce the change from the docs alone. The reviewer checks all four and reports a miss as a finding like any other.

Three of them are cheap to check and routinely missed, so check them explicitly before the terminal approval:

- `make lint test` was actually run at the reviewed head, and its result is quoted in a disposition or review, not asserted.
- `git status --porcelain` and the PR's file list stage nothing from `downloads/`, `captures/`, `processed/`, or `temp/`, and no `.env`, token, cookie, or proxy CA material anywhere.
- A change to behavior that `README.md`, `docs/开发计划.md`, `docs/目录说明.md`, or `docs/项目背景.md` describes updated those files in the same PR.

## Terminal approval and report

Approval eligibility requires a completed full review of the final published base/head pair, no pending/unadjudicated dispositions, and no unresolved valid P0/P1 (including accepted deferrals). Final P2/P3 findings alone do not block, whether a writer dispositioned them or the closing review raised them first; record them as residual work if the budget is exhausted. Incomplete validation that prevents a reliable review is a blocker, not evidence of zero findings.

Re-fetch PR state and both SHAs immediately before approval. Bind a formal `APPROVE` review to the reviewed head (`commit_id` via the GitHub reviews API), using the signed full-review report. Check again afterward; if head/base moved, report the approval as stale and stop without claiming current-head approval.

If the signed-in user authored the PR, or GitHub forbids approval, use a comment when permitted: `approval-eligible; formal approval blocked: <exact reason>`. That verdict **ends the loop successfully**: it is the same code judgement as `APPROVED` and differs only in whether GitHub could store it, so stop there and report it as the terminal success rather than spending further rounds. Do not impersonate another reviewer, call a comment an approval, or retry that restriction. P2/P3 alone permit approval; CI/branch-protection readiness is a separate status. Never merge, enable auto-merge, or dismiss another person's review.

Stop on cancellation, exhausted budget, a real blocker, or conflicting changes. For an uncertain remote write, re-read by run/round/phase marker before retrying; if outcome remains unknown, stop rather than duplicate it. Report PR URL, final base/head, rounds/writer phases used, report URLs, validation results, unresolved IDs and severities, actual approval status, and the next concrete action. Preserve the `temp/` run directory for resumption; do not commit transcripts or start a monitor.
