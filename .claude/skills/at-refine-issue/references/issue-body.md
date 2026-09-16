# Refined issue body

The shape a refined issue takes in this repository. Fill every section with observed facts; delete a section only when it genuinely does not apply, and say so rather than leaving a heading with no content. Keep the issue and its [task comment](task-comment.md) consistent — the acceptance criteria in particular are one list published twice.

Write soft-wrapped Markdown: one paragraph, list item, or table row per physical line, with no hard line break inside it. GitHub wraps to the reader's viewport, so a hard-wrapped paragraph renders ragged at every other width. Leave a blank line before every `---`, or the paragraph above it parses as a setext H2 and silently renders as a heading. The rule lives in the Engineering section of [`AGENTS.md`](../../../../AGENTS.md) and binds issue and comment bodies alike.

This repository runs no issue form, so a refined body is the only place the fields below get answered. Answer all of them, whether or not the issue was opened with them: goal, context, scope, non-goals, acceptance criteria, validation, evidence, owner and reviewer, and authorization. A missing field is reported, not invented — and a field this repository genuinely has no concept of (a priority scheme, a team queue) is left out rather than manufactured to fill a template.

## Language

Narrative sections are Simplified Chinese; the acceptance-criteria checklist and the validation-and-evidence table are English. That is not a style preference — the AC list in the body and the AC list in the task comment must be byte-identical, and they cannot be if the two are written in different languages. `AGENTS.md`'s rule that code and identifiers are English already covers a test specification, which is what an AC list is. The Goal, Scope, Non-goals, plan, and Context sections stay Chinese, because their reader is the human deciding whether the work is right.

## Template

````markdown
## Goal

One paragraph: what is true after this ships, and why it is worth doing. Name the deliverable, not the activity.

## Invocation

Only for a command, skill, CLI, or API surface. Give the exact usage line, each argument with its default and validation, and how an ambiguous or missing argument is resolved.

## Scope

- Bulleted, each item a thing that gets built or changed.
- Concrete enough that a reviewer can tell whether it happened.

## Non-goals

- Bulleted, each item something a reader might reasonably assume is included and is not.
- Cover the adjacent work this issue deliberately leaves alone.

## Implementation plan

1. Ordered steps, each one a unit of work with an observable end state.
2. Name the files or surfaces each step touches where they are already known.
3. End with validation and delivery, not with "and then ship it".

## Acceptance criteria

- [ ] **AC1 — Short label:** The observable outcome, plus how it is verified.
- [ ] **AC2 — Short label:** …

## Validation and evidence

| Scenario | Required observable result |
|---|---|
| … | … |

Name the checks that must run and what counts as passing. `make lint test` is this repository's check; say which validation is offline or mocked and which needs live capture, a real model call, or a WeChat client.

## Context, dependencies, and evidence

- [Linked issue / discussion / spec](url): what it settles for this work.
- Repository instructions the implementer must follow, and the plan entry in `docs/开发计划.md` this issue belongs to.
- Dependencies, with whether each one blocks or merely informs.

## Owner 与 reviewer

Owner: @handle · Reviewer: by codex

Owner is a GitHub account written with `@`; reviewer is the agent that reviews it, written without one (`by claude`, `by codex`). The two notations differ on purpose — one is an account GitHub can notify, the other is not.

## 授权

On <date> the user requested: "<verbatim request>".

What that authorizes, and what it does not. Refinement authority is never implementation authority.

## Execution

**Standalone task instructions:** [Implementation task, revision R](<real permalink>). The comment carries the complete work order and AC1–ACn; no prior chat context is required.

Status — keep exactly one of these two sentences, the one that matches the labels this issue actually carries:

- *Ready:* Ready for explicit human dispatch. Creating this issue and comment has not started implementation. Sending either prompt below authorizes the work described in the task comment.
- *Blocked:* **Not ready for dispatch:** `<the unresolved decision, named>` is open, so this issue carries `decision:needed` and `ai:blocked`. Creating this issue and comment has not started implementation, and the prompts below must not be sent until a human settles that decision and the block is lifted.

**Goal prompt** (where the harness supports `/goal`):

```text
/goal Execute from <real permalink>. <deliverable and where it lands>. Continue until the task's acceptance criteria are satisfied and <completion artifact> exists, or a stated stop condition applies.
```

**Plain-language prompt** (same task and authorization):

```text
Execute from <real permalink>. <the same sentence, without the slash command>.
```

The receiving session must follow the linked task-comment contract and repository instructions. The slash command itself provides no retrieval, provenance, or authorization check.
````

## Acceptance criteria that hold up

A criterion is finished when someone who was not in the conversation can decide pass or fail without asking anyone.

| Weak | Strong |
|---|---|
| The skill is well documented | The README documents the invocation, both arguments, and their defaults; `SKILL.md` frontmatter sets `argument-hint` and `disable-model-invocation: false` |
| Handles errors properly | A missing or ambiguous target produces a question and zero state-changing remote calls |
| Tests pass | `make lint test` passes, and the result is recorded in the PR with the command and counts |
| The decrypt path still works | `wxclip decrypt` on the recorded fixture reproduces the expected sha256, with `decode_key` passed as a string and the first 128 KiB compared byte for byte |
| Good validation | The recorded scenario matrix covers the listed cases, each with input, observed output, and a pass/fail line |

Numbering is a contract. `AC3` means the same thing in revision 1 and revision 4; a new criterion is appended as `ACn+1`, and a withdrawn one stays in place marked `(withdrawn, revision R)`. Renumbering breaks every comment, PR, and report that cited the old number.

Each criterion states its own evidence — the command and its result, the file path, the resolvable link, or the recorded scenario. Validation then reads as a checklist rather than a judgement call.

An issue that touches capture, decrypt, or download states how it was verified without committing captured media. Cite a sha256, a byte offset, a log line with the key redacted — never the file, and never a `decode_key` value.

## Honesty markers

Keep these visible rather than smoothing them away:

- **Assumption:** the drafter's inference, written as an assumption and flagged for the owner to confirm or correct.
- **Open decision:** a question whose two plausible answers produce different acceptance criteria. It keeps the issue `decision:needed` and `ai:blocked` until a human settles it, and it forces the *Blocked* status sentence in the Execution section — a body that says "ready for explicit human dispatch" while carrying `ai:blocked` tells the human the opposite of what its labels say.
- **Recommendation:** a suggestion the owner has not adopted. It never appears in Scope.
- **Confirmed decision:** cite the comment or discussion that settled it, so a later reader can check.

An issue that reads as fully settled when it is not is worse than one that shows its open questions. Refinement makes the state of knowledge legible; it does not manufacture certainty.
