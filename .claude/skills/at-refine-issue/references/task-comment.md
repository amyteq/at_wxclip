# Standalone task comment

One comment, one complete work order. The comment is what a launch prompt points at, so it is the only thing the executing session is guaranteed to read. Everything that session needs to decide what to build, when it is done, and when to stop must be inside it or behind a link it is told to open.

The test: **hand the permalink to a fresh session with no other context, and it can do the work.** If it would have to scroll the thread, reconstruct instructions from earlier comments, or ask what "the approach we agreed" was, the comment is incomplete.

Write soft-wrapped Markdown: one paragraph, list item, or table row per physical line. Leave a blank line before every `---`. That rule is this repository's, and it lives in the Engineering section of [`AGENTS.md`](../../../../AGENTS.md); for a target resolved to another repository, follow that repository's Markdown convention where it states one and say in the report which one you applied.

In this repository the comment is written in English, including its acceptance criteria. The AC list here and the AC list in the issue body are one list published twice and must be byte-identical — that part is universal, because a list published in two languages cannot be compared at all. The language choice itself, and what to do for a target in another repository, are in [issue-body.md](issue-body.md#language).

## Marker

The first line is always the marker. It is how a repeat run finds this comment instead of posting a duplicate, and how a reader tells a generated task comment from ordinary discussion.

```text
<!-- at-refine-issue:task repo=<owner>/<repo> issue=<n> revision=<r> -->
```

`revision` starts at 1 and increments only when a *replacement* comment is published. Editing a comment in place to add detail keeps its revision; superseding it with a new comment raises it. A revision number orders the chain; it does not select the live link in it — see [Which revision is current](#which-revision-is-current).

Find existing markers before writing anything, qualifying the prefix with this repository and issue:

```bash
gh api repos/<owner>/<repo>/issues/<n>/comments --paginate \
  --jq '.[] | select(.body | startswith("<!-- at-refine-issue:task repo=<owner>/<repo> issue=<n> ")) | {id, html_url, updated_at, user: .user.login}'
```

A hit is a candidate, not a match. Before editing or adopting one, require all three of: its first line is exactly the marker for this repository and issue; its `user.login` is the account the run writes as (`gh api user --jq .login`); and its body still carries the required sections of the template below, so it is a task comment rather than a quotation of one. A marker comment written by anyone else is left untouched and reported: it is evidence, not this run's work, and it never becomes the comment a launch prompt points at.

## Which revision is current

Those three checks answer "is this candidate one of this run's task comments". They cannot answer "is it the live one", because a superseded revision keeps its marker, keeps its author, and keeps every required section — see [Revising a dispatched task](#revising-a-dispatched-task). After one legitimate replacement, every revision in the chain passes all three. So currency is decided by a separate rule, and this is it, stated identically in the [skill](../SKILL.md#which-revision-is-current):

**The issue's Execution section names the current revision, and nothing else does.** Not the highest `revision=` number — a publish that succeeded while the issue update failed leaves a high number on a comment the issue never pointed at. Not the newest `updated_at` — appending the dispatch record to a superseded comment touches it later than the live one.

| Candidates passing adoption | Distinct comment ids in the issue's Execution section | Outcome |
|---|---|---|
| Any count | Two or more | Stop. Report every candidate with its `revision`, `updated_at`, and permalink, report every returned id, and ask which id is current. Never infer currency from candidate count |
| The candidate the id names is among them | Exactly one | That comment is current. Every other candidate is superseded: never edit it, never repoint the issue at it, never name it in a launch prompt |
| No candidate matches the id | Exactly one | Stop and report the id and the adoption check it failed — wrong author, missing sections, or deleted comment. Never substitute another candidate for it |
| Exactly one | None — no Execution section, or no id in it | That candidate is current, and this run repairs the Execution section to link it. This is the "published, issue update failed" recovery and the only case where a candidate becomes current without the issue already saying so |
| None | None — no Execution section, or no id in it | Stop. There is no candidate to select or link; report that no current revision can be identified and ask how to recover |
| Two or more | None — no Execution section, or no id in it | Stop. Report every candidate with its `revision`, `updated_at`, and permalink, and ask which is current. A chain whose head cannot be named from the issue is exactly what this rule refuses to guess through |

Any combination not listed above, or any run that cannot determine either count, stops and asks which revision is current. It does not infer currency, mutate a comment or issue, or dispatch work without a selected current revision.

Read the pointer from the issue rather than inferring it; the Execution section is the last section of [issue-body.md](issue-body.md)'s template, so everything from its heading onward is in scope:

```bash
gh issue view <n> --repo <owner>/<repo> --json body --jq .body \
  | awk '/^## Execution/{seen = 1} seen' | grep -oE 'issuecomment-[0-9]+' | sort -u
```

Two cross-checks make the answer falsifiable instead of advisory, and both run before any write:

- The comment the issue names must not carry a `Superseded by [revision …]` line. One that does contradicts the issue about which revision is live; stop, report both, and change nothing.
- Every other candidate must carry that line. A missing one is repaired by appending it, which preserves the byte prefix and is therefore legal even on a dispatched comment, and the repair is named in the report.

## Repository-bound slots

The template below is written for `amyteq/at_wxclip` and reads as a finished comment here, but the third column is this repository's *instance*, not the requirement. The middle column is the requirement.

| Slot | The general requirement it stands for | `amyteq/at_wxclip`'s instance |
|---|---|---|
| `<agent agreement>` | The file carrying the repository's shared agent conventions and its definition of done | `AGENTS.md` |
| `<plan of record>` | The document the milestone this work belongs to is tracked against | `docs/development-plan.md` |
| `<placement guide>` | Where a new artifact belongs in the tree | `docs/directory-guide.md` |
| `<repository invariants>` | The counter-intuitive facts a change can silently break, spelled out one by one rather than cited | `decode_key` stays an opaque string end to end, a `.part` file holds already-decrypted bytes and resume continues the keystream from that offset, downloading goes direct rather than through the proxy, runtime dependencies stay at zero, and nothing from `downloads/`, `captures/`, `processed/`, or `temp/` is ever staged |
| `<worktree root>` | Where a run's dedicated branch or worktree lives | `.claude/worktrees/` |
| `<scratch directory>` | The git-ignored place for a run's working files | `temp/` |
| `<repository check>` | The one command that must pass before work is reported done | `make lint test` |
| `<forbidden evidence>` | The values that may never be quoted as evidence | captured media, a `decode_key` value, or proxy CA material |
| `<reviewer notation>` | How the repository writes the reviewer, distinct from an account GitHub can notify | the agent name without an `@`: `by claude`, `by codex` |

Fill every slot before publishing; an unsubstituted `<slot>` in a published body is a defect, exactly like an unsubstituted permalink. When the resolved repository is not `amyteq/at_wxclip`, fill them from *that* repository's own instructions and never from the third column — importing wxclip's paths, invariants, or check command into another repository's issue publishes instructions its executing session cannot follow. Where the resolved repository has no equivalent for a slot, say so in the comment, report the gap, and leave the requirement in the middle column visible rather than inventing a filling for it.

## Template

````markdown
<!-- at-refine-issue:task repo=<owner>/<repo> issue=<n> revision=<r> -->
## Task — <short imperative title>

**Repository:** `<owner>/<repo>` · **Tracking issue:** [#<n>](<issue url>) · **Owner:** @<handle> · **Reviewer:** <reviewer notation> · **Revision:** <r> (<date>)

One sentence on what this comment is and whether it has been dispatched. A prepared-but-undispatched task says so explicitly: publishing this comment does not start the work. That sentence, plus the `--- dispatched <YYYY-MM-DD> ---` record a later run appends once dispatch happens, is what tells a repeat run which branch it is on; with neither present the run must assume dispatched.

### Goal

What must be true when the task is done. Name the deliverable and where it lands.

### Read first

- `<agent agreement>` — the shared agent agreement, including the definition of done and the boundaries this task may not cross.
- `<plan of record>` for the milestone this belongs to, and `<placement guide>` for where a new artifact goes.
- Each linked issue, discussion, or decision, with one clause saying what it settles and what about it is *not* a requirement here.
- The repository invariants this task could break, named individually rather than by reference: `<repository invariants>`.

### Scope and deliverables

1. Each artifact to produce, with its path where known.
2. Documentation or integration the artifact needs to be usable.
3. Validation evidence and, where authorized, the PR.

### Implementation instructions

1. **Establish the run.** Confirm that this comment's marker names the repository and issue you are working in and that the issue's Execution section still points to this revision; if either disagrees, stop and ask. Then work in a dedicated branch or a worktree under `<worktree root>`, keep scratch in `<scratch directory>`, record the dispatch and its allowed actions, and preserve unrelated work.
2. … ordered steps, each with an observable end state …
3. **Validate and deliver.** Run the scenario matrix and `<repository check>`, inspect the final diff, confirm nothing captured or secret is staged, and deliver per the authorization below.

### Acceptance criteria

- [ ] **AC1 — Short label:** The observable outcome and how it is verified.
- [ ] **AC2 — Short label:** …

These are the same criteria as the issue body, byte for byte. If the two ever disagree, stop and reconcile before continuing.

### Validation and evidence

| Scenario | Required observable result |
|---|---|
| … | … |

Which checks to run, what counts as passing, and which evidence must appear in the closeout. Say explicitly when validation must be offline or mocked, and what live actions are forbidden. Evidence never includes `<forbidden evidence>`: cite a sha256, a path, or a byte offset instead.

### Authorization and boundaries

What the recorded human request authorizes, verbatim where it matters, and what it does not. Name the actions that are allowed without asking again — branch push, PR creation, progress comments — and the actions that stay human-gated.

Out of scope: an explicit list, so the executing session does not widen the task.

### Stop conditions and closeout

- **Success:** the acceptance criteria are satisfied, the checks are recorded, and <the completion artifact> exists.
- **Blocked:** a required decision, access, capability, or dependency is unavailable; authorization is insufficient; or conflicting external edits cannot be reconciled. Finish the independent work, then report the precise missing item and the next action.
- **Cancellation or resource limit:** stop within the limit and preserve progress with a concrete continuation point.

Closeout must include <the artifact links>, changed files, an AC1–ACn status/evidence table, the `<repository check>` result, validation results and limitations, and any remaining blocker with its next action. Do not mark unverified criteria complete.
````

## Launch prompts

Both forms go in the issue's Execution section and in the final response, always with the real permalink substituted:

```text
/goal Execute from <real permalink>. <deliverable and where it lands>. Continue until the task's acceptance criteria are satisfied and <completion artifact> exists, or a stated stop condition applies.
```

```text
Execute from <real permalink>. <the same sentence, without the slash command>.
```

What to say about `/goal`, because overstating it is a recurring failure: it is a harness entry point that keeps a session working until a stop condition holds. It hands the prompt to the session as written. It does not fetch the comment, verify who wrote it, or grant authority. The authorization lives in the comment and in the human's act of sending the prompt. The plain-language form carries the identical task for any session or harness that does not offer the command, and is not a lesser fallback.

A published prompt containing `<comment-url>`, `<real permalink>`, or any other unsubstituted placeholder is a defect. Verify by fetching the URL back before reporting.

## Publishing

```bash
gh issue comment <n> --repo <owner>/<repo> --body-file task-comment.md          # prints the real permalink
gh api repos/<owner>/<repo>/issues/comments/<id> --jq '.html_url, .updated_at'  # read-back
gh api repos/<owner>/<repo>/issues/comments/<id> -X PATCH -F body=@task-comment.md   # edit in place
gh issue edit <n> --repo <owner>/<repo> --body-file issue-body.md               # issue body, after the permalink exists
```

Snapshot before every edit — `url`, `comment_id`, `author`, `updated_at`, `sha256`, and the verbatim body — into a run directory under `temp/` in this repository's checkout, which `.gitignore` excludes and `docs/directory-guide.md` describes as disposable. That path is where this skill's own scratch goes regardless of which repository the target issue lives in. Those files are working state, not the record: the durable record is the published comment itself, and nothing this skill writes locally is ever staged.

## Revising a dispatched task

Once a human has sent a launch prompt, the comment is a contract a session may already be acting on. The rule for changing it is decided by what the change does to the existing bytes: append and the dispatch continues, rewrite and it stops.

Whether a dispatch has happened is decided fail-closed by the skill's *Has this task been dispatched?* rule: a run treats the comment as dispatched unless the human says otherwise and nothing on the issue reads as a run in flight. The first write after a run learns of a dispatch is the record itself, so the next run reads it instead of inferring it:

```text
--- dispatched <YYYY-MM-DD> ---

Dispatched by @<handle> from this issue's Execution section.
```

An append leaves every earlier byte untouched and adds a block at the end:

```text
--- appended <YYYY-MM-DD> ---

<the additional instruction, and what it changes about the acceptance criteria>
```

The heading is signposting for humans; the enforceable part is the byte prefix, which any party can check:

```bash
[ "$(head -c "$(stat -c%s old.md)" new.md | sha256sum | cut -d' ' -f1)" \
= "$(sha256sum old.md | cut -d' ' -f1)" ] && echo APPENDED || echo REWRITTEN
```

Anything that fails that test — reordered sections, a corrected earlier sentence, a trimmed scope list — is a rewrite. Rewriting is allowed; it simply costs a stop and an explicit redispatch, which is the right price for changing a task a run has already acted on. Publish the replacement as a new comment at revision `r+1` rather than mutating the old one, then make both directions navigable:

- The new comment's first line is still the marker, carrying `revision=<r+1>` — a replacement that drops it is unfindable by the query above. Its first line of body text is `Supersedes [revision <r>](<old permalink>).`
- The old comment gains one appended line: `Superseded by [revision <r+1>](<new permalink>). This revision is no longer current.` That line is what the cross-check in [Which revision is current](#which-revision-is-current) reads, so a replacement that skips it leaves two indistinguishable live-looking comments behind.
- The issue's Execution section names exactly one current revision and links it, by the comment permalink carrying `#issuecomment-<id>`.
- Before reporting, re-run the Execution-section query above and confirm it returns exactly the new comment's id. A replacement is not finished until the pointer moves.
- Tell the human, in the report, that the previous dispatch has ended and the new revision needs an explicit redispatch.

## Recovery

| Situation | Action |
|---|---|
| Comment published, issue body update failed | The permalink is already valid. Re-fetch it by marker, reuse it, repair only the issue body. Never post a second comment |
| Publication timed out, outcome unknown | Query by marker before retrying. Adopt a candidate only if it passes all three adoption checks above — marker, author, and required sections — and then resolve currency by [Which revision is current](#which-revision-is-current); if one exists and fails, stop and report; if none exists, publish once |
| A human edited the comment since the snapshot | Reconcile their text into the new body, or stop and report the conflict. Stale text never overwrites a human edit |
| An undispatched draft comment already exists | Reuse it in place, keeping its revision — an in-place edit never raises `revision`. A draft is not a contract, so a full rewrite is fine; it just stays the same comment and the same permalink. "Undispatched" must be established by the fail-closed rule, never assumed |
| Several revisions exist after a legitimate replacement | Expected, not an error. The issue's Execution link picks the current one and the rest stay superseded; append any missing `Superseded by` line and report the repair |
| Two task comments exist by accident | The same rule decides: keep the one the issue's Execution section links, mark the other superseded, and say so in the report. Do not delete it. If the issue links neither, do not pick one — stop and ask |
