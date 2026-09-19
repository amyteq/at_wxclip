# Review and disposition reports

Use these formats as Markdown comments. Replace placeholders with observed facts; omit no-findings rows rather than inventing findings. Empty tables may be replaced by "No new findings" / "No fixes required" plus the ledger summary. Escape pipes inside table cells. Link exact file lines at the reviewed commit, tests, and prior comments. Include the run/round/phase marker to reconcile uncertain publication.

Do not repeat the PR URL — the comment already lives on the PR. Write each SHA as a short hash linked to its exact full-SHA commit URL, and carry every other reference on descriptive link text; no bare URLs in comment prose. Both signatures are defined in [Signature fields](#signature-fields).

Write every published comment as **soft-wrapped Markdown**: one paragraph, list item or table row per line, with no hard line breaks inside a paragraph. GitHub wraps to the reader's viewport, so a hard-wrapped paragraph renders ragged at every other width. The rule lives in the Engineering section of [`AGENTS.md`](../../../../AGENTS.md), and it binds issue and pull-request bodies, comments, and repository Markdown alike.

Leave a blank line before any `---`. Without it the preceding paragraph parses as a setext H2 and renders as a heading, which is how a signature separator silently turns the line above it bold.

That rule binds the header and record lines of the templates below too, which is why each `Base: …`, `Push: …`, link, `Ledger: …`, `Validation: …`, `Verdict: …`, and `Unresolved P0/P1: …` line stands as its own paragraph with a blank line after it. Two adjacent lines are one hard-wrapped paragraph in source, which is exactly what the rule forbids; GitHub's comment renderer happens to turn that single newline into a `<br>`, but the rule governs the source, and separate paragraphs render on separate lines under every renderer. Do not close the gap by joining them into one long line either — that loses the line break the reader needs.

## Reviewer

```markdown
<!-- at-review-pr: RUN_ID round=R phase=review|closing-review -->
## PR review — round R / LIMIT — full|delta|final-full

Base: [BASE_SHA_SHORT](BASE_COMMIT_URL) · Reviewed head: [HEAD_SHA_SHORT](HEAD_COMMIT_URL) · [Scope / AC](SCOPE_URL)

[Previous review](PREVIOUS_REVIEW_URL) · [Writer disposition](DISPOSITION_URL) · Delta range: [PREVIOUS_HEAD_SHORT..HEAD_SHA_SHORT](COMPARE_URL) (full reviews: complete PR diff)

| ID | Priority | Finding / concrete impact | Evidence at reviewed head | Ledger state / adjudication |
|---|---|---|---|---|
| F001 | P1 | Trigger and resulting defect | file:line + test/reproduction | open |

Ledger: resolved IDs; accepted rebuttals; accepted deferrals with residual severity; pending/reopened IDs and new evidence. Do not restate accepted requests as new findings.

Validation: commands and results actually observed, skipped checks with reasons. Name only tools and commands that actually ran in this phase — a validation line is evidence, so an invented tool, harness or wrapper name in it is a defect in the report itself.

Verdict: continue / approval-eligible / blocked / budget-exhausted.

Unresolved P0/P1: IDs or none. Residual P2/P3: IDs or none.

---
🔎 ACTUAL_AGENT·ACTUAL_MODEL·ACTUAL_EFFORT · read-only head: [HEAD_SHA_SHORT](HEAD_COMMIT_URL) · context: OBSERVED_CONTEXT_OR_NA · STARTED_TIMESTAMP TZ · MEASURED_ELAPSED · VERDICT
```

In round 1 write `previous review: none` and `writer disposition: none` as plain text rather than empty links, and drop the delta range from a full review.

## Writer

```markdown
<!-- at-review-pr: RUN_ID round=R phase=writer -->
## Fix dispositions — round R / LIMIT

Base: [BASE_SHA_SHORT](BASE_COMMIT_URL) · Input head: [REVIEWED_SHA_SHORT](REVIEWED_COMMIT_URL) · Result head: [RESULT_SHA_SHORT](RESULT_COMMIT_URL) · [Review](REVIEW_URL) · [Scope / AC](SCOPE_URL)

Push: verified remote head / no code changes / blocked (reason)

| ID | Disposition | Change or rebuttal/deferral reasoning | Evidence / commit / tests | Residual risk / next action |
|---|---|---|---|---|
| F001 | fixed-pending-review | Concrete fix | commit link + `make lint test` result | reviewer verification |
| F002 | deferred-pending-review | Scope or effort reason | supporting evidence | impact, follow-up proposal |

Cover every open finding handed to this phase. Propose follow-ups without filing new issues unless authorized. State failed or unrun tests; do not imply a rebuttal/deferral is accepted. A phase that changed files quotes the `make lint test` result it actually observed, including a failing one.

---
✏️ ACTUAL_AGENT·ACTUAL_MODEL·ACTUAL_EFFORT · head: [RESULT_SHA_SHORT](RESULT_COMMIT_URL) · context: OBSERVED_CONTEXT_OR_NA · STARTED_TIMESTAMP TZ · MEASURED_ELAPSED
```

## Signature fields

Each signature is a single rendered line: fields joined by ` · `, with the provider triple joined by `·` without spaces. The coordinator fills it from that phase's execution evidence, never from estimates or from the launch command text.

| Field | Meaning | Example |
|---|---|---|
| `ACTUAL_AGENT·ACTUAL_MODEL·ACTUAL_EFFORT` | Provider, model, and reasoning effort for the session | `codex·gpt-5.6-sol·xhigh`, `claude·claude-opus-5·xhigh (requested)` |
| `OBSERVED_CONTEXT_OR_NA` | Context window available to that session as reported — not tokens consumed | `context: 1M`, `context: 200k`, `context: N/A (not emitted)` |
| `STARTED_TIMESTAMP TZ` | Coordinator wall clock when the phase started, `yyyy-MM-dd HH:mm`, zone named | `2026-09-17 08:24 AEST` |
| `MEASURED_ELAPSED` | Measured duration of that phase as `H:mm` | `0:04` (4 minutes), `1:37` (1 hour 37 minutes) |
| `VERDICT` (reviewer only) | Outcome label for this review; always the last field | `CHANGE REQUIRED`, `APPROVAL ELIGIBLE`, `APPROVED`, `BLOCKED`, `BUDGET EXHAUSTED` |

The end timestamp, the requested profile, and the per-phase metadata file are recorded in the `temp/` run directory under [runtime metadata](agents.md#runtime-metadata) rather than published here. Elapsed hours are unbounded and minutes are rounded, so a sub-minute phase shows `0:00`; keep raw ISO timestamps and seconds in that file instead of inflating the published value. Use the phase's own wall time, not total loop time.

`ACTUAL_EFFORT` is the value the CLI reported. When a CLI reports no effort at all, publish the requested value with a trailing ` (requested)` — for example `claude·claude-opus-5·xhigh (requested)` — and note in the metadata file that this CLI has no echo. An unreported effort does **not** block the phase. Agent, model, and phase timing stay verify-or-block: when those cannot be observed, mark them `unverified` and stop rather than substituting a plausible value. `N/A (not emitted)` belongs only to a context field the CLI genuinely never reports. A value that contradicts the request — a different model, a fallback notice, an effort cap, a rejected `--effort` value — is a blocker, not a footnote. Publish only the non-secret summary, never raw transcripts, tokens, or credentials. If a phase failed before model execution, say "not run" rather than signing it.

Captured media, `decode_key` values, and proxy CA material never appear in a published report. A finding that needs one as evidence cites the file path and offset, not the bytes.

The body `Verdict:` line records loop state; the footer `VERDICT` labels the review outcome. Keep them consistent:

| Body `Verdict:` | Footer `VERDICT` |
|---|---|
| `continue` | `CHANGE REQUIRED` |
| `approval-eligible` | `APPROVAL ELIGIBLE`, becoming `APPROVED` only in a review GitHub actually accepted at that head |
| `blocked` | `BLOCKED` |
| `budget-exhausted` | `BUDGET EXHAUSTED` |

`APPROVED` therefore never appears on a plain comment. Self-authored or permission-blocked eligibility stays `APPROVAL ELIGIBLE`, and the terminal coordinator summary states whether GitHub accepted the approval.

The two labels record the same code judgement and differ only in whether GitHub stored it. `APPROVAL ELIGIBLE` means the review concluded the change is approved and GitHub refused to record that verdict — normally because the signed-in account authored the PR. It is **not** a weaker verdict, a provisional pass, or a request for another round: it is the terminal success state, and the loop stops on it exactly as it stops on `APPROVED`. Only the reviewable artifact differs: `APPROVED` is a review object branch protection can count, `APPROVAL ELIGIBLE` is a comment a human reads. Never run further rounds hoping an eligible verdict will turn into `APPROVED`; the restriction is an identity fact, not a quality gap.
