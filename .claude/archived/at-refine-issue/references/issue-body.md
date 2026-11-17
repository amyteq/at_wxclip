# Refined issue body

The shape a refined issue takes in this repository. Fill every section with observed facts; delete a section only when it genuinely does not apply, and say so rather than leaving a heading with no content. Keep the issue and its [task comment](task-comment.md) consistent — the acceptance criteria in particular are one list published twice.

Write soft-wrapped Markdown: one paragraph, list item, or table row per physical line, with no hard line break inside it. GitHub wraps to the reader's viewport, so a hard-wrapped paragraph renders ragged at every other width. Leave a blank line before every `---`, or the paragraph above it parses as a setext H2 and silently renders as a heading. That rule is this repository's, from the Engineering section of [`AGENTS.md`](../../../../AGENTS.md), and it binds issue and comment bodies alike; for a target resolved to another repository, follow that repository's Markdown convention where it states one.

This repository runs no issue form, so a refined body is the only place the fields below get answered. Answer all of them, whether or not the issue was opened with them: goal, context, scope, non-goals, acceptance criteria, validation, evidence, owner and reviewer, and authorization. Where the resolved repository *does* run an issue form, keep its fields and add these; the requirement is that every field is answered, not that this exact shape wins. A missing field is reported, not invented — and a field the resolved repository genuinely has no concept of (a priority scheme, a team queue) is left out rather than manufactured to fill a template.

## Repository-bound slots

The template below is written for `amyteq/at_wxclip`, so some of what it names is this repository's instance rather than the requirement. [task-comment.md](task-comment.md#repository-bound-slots) carries the shared slot table; these are the ones the issue body uses.

| Slot | The general requirement it stands for | `amyteq/at_wxclip`'s instance |
|---|---|---|
| `<repository check>` | The one command that must pass before work is reported done | `make lint test` |
| `<plan of record>` | The document the milestone this work belongs to is tracked against | `docs/development-plan.md` |
| `<live actions>` | The validation that cannot run offline, so an issue must say whether it needs them | live capture, a real model call, or a WeChat client |
| `<forbidden evidence>` | The values that may never be quoted as evidence | captured media, a `decode_key` value, or proxy CA material |
| `<reviewer notation>` | How the repository writes the reviewer, distinct from an account GitHub can notify | the agent name without an `@`: `by claude`, `by codex` |
| `<blocked labels>` | The labels that make an unresolved material decision visibly block dispatch | `decision:needed` and `ai:blocked` |

Fill them from the resolved repository's own instructions, never from the third column, and report any slot that repository has no equivalent for.

## Language

In this repository, narrative sections are Simplified Chinese; the acceptance-criteria checklist and the validation-and-evidence table are English. One half of that is a preference and the other half is not, and they are worth separating.

The part that binds everywhere: **the AC list in the body and the AC list in the task comment are one list, so they must be in one language.** Byte-identity is the check ([the validation record](../../../../docs/test/at-refine-issue.md) shows it catching a zh/en pair), and two languages do not merely make it hard — they make it undecidable.

The part that is this repository's: *which* language each half uses. `AGENTS.md`'s rule that code and identifiers are English already covers a test specification, which is what an AC list is, and the Goal, Scope, Non-goals, plan, and Context sections stay Chinese because their reader is the human deciding whether the work is right. For a target resolved to another repository, follow that repository's own language convention for both halves and say in the report which one you applied — the byte-identity requirement survives the substitution, because it is about sameness rather than about English.

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

Name the checks that must run and what counts as passing. `<repository check>` is this repository's check; say which validation is offline or mocked and which needs `<live actions>`.

## Context, dependencies, and evidence

- [Linked issue / discussion / spec](url): what it settles for this work.
- Repository instructions the implementer must follow, and the entry in `<plan of record>` this issue belongs to.
- Dependencies, with whether each one blocks or merely informs.

## Owner 与 reviewer

Owner: @handle · Reviewer: <reviewer notation>

Owner is a GitHub account written with `@`; reviewer is `<reviewer notation>`. The two notations differ on purpose — one is an account GitHub can notify, the other is not.

## 授权

On <date> the user requested: "<verbatim request>".

What that authorizes, and what it does not. Refinement authority is never implementation authority.

## Execution

**Standalone task instructions:** [Implementation task, revision R](<real permalink>). The comment carries the complete work order and AC1–ACn; no prior chat context is required.

Status — keep exactly one of these two sentences, the one that matches the labels this issue actually carries:

- *Ready:* Ready for explicit human dispatch. Creating this issue and comment has not started implementation. Sending either prompt below authorizes the work described in the task comment.
- *Blocked:* **Not ready for dispatch:** `<the unresolved decision, named>` is open, so this issue carries `<blocked labels>`. Creating this issue and comment has not started implementation, and the prompts below must not be sent until a human settles that decision and the block is lifted.

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

The Execution section is not only prose: it is the machine-readable pointer to the live revision. Its link must be the comment permalink carrying `#issuecomment-<id>`, it must name exactly one revision, and [task-comment.md](task-comment.md#which-revision-is-current) reads it to decide which candidate a repeat run may edit. A hand-built or shortened link there leaves the chain unresolvable.

## Acceptance criteria that hold up

A criterion is finished when someone who was not in the conversation can decide pass or fail without asking anyone. The strong column below is drawn from this repository; for another repository the shape transfers and the commands and paths do not.

| Weak | Strong |
|---|---|
| The skill is well documented | The README documents the invocation, both arguments, and their defaults; `SKILL.md` frontmatter sets `argument-hint` and `disable-model-invocation: false` |
| Handles errors properly | A missing or ambiguous target produces a question and zero state-changing remote calls |
| Tests pass | `<repository check>` passes, and the result is recorded in the PR with the command and counts |
| The decrypt path still works | `wx-dlh decrypt` on the recorded fixture reproduces the expected sha256, with `decode_key` passed as a string and the first 128 KiB compared byte for byte |
| Good validation | The recorded scenario matrix covers the listed cases, each with input, observed output, and a pass/fail line |

Numbering is a contract. `AC3` means the same thing in revision 1 and revision 4; a new criterion is appended as `ACn+1`, and a withdrawn one stays in place marked `(withdrawn, revision R)`. Renumbering breaks every comment, PR, and report that cited the old number.

Each criterion states its own evidence — the command and its result, the file path, the resolvable link, or the recorded scenario. Validation then reads as a checklist rather than a judgement call.

An issue whose work touches a secret states how it was verified without publishing the secret: cite a sha256, a byte offset, or a redacted log line. In this repository that means an issue touching capture, decrypt, or download never commits captured media and never quotes `<forbidden evidence>`.

## Honesty markers

Keep these visible rather than smoothing them away:

- **Assumption:** the drafter's inference, written as an assumption and flagged for the owner to confirm or correct.
- **Open decision:** a question whose two plausible answers produce different acceptance criteria. It keeps the issue `decision:needed` and `ai:blocked` until a human settles it, and it forces the *Blocked* status sentence in the Execution section — a body that says "ready for explicit human dispatch" while carrying `ai:blocked` tells the human the opposite of what its labels say. Those label names are this repository's, from [`.github/labels.yml`](../../../../.github/labels.yml); another repository's equivalents go in their place, and a repository with no equivalent gets the open decision named in the body and reported instead.
- **Recommendation:** a suggestion the owner has not adopted. It never appears in Scope.
- **Confirmed decision:** cite the comment or discussion that settled it, so a later reader can check.

An issue that reads as fully settled when it is not is worse than one that shows its open questions. Refinement makes the state of knowledge legible; it does not manufacture certainty.
