# Fresh role sessions

Use independent CLI invocations for each review and writer phase. A prompt that says "now act as reviewer" in the writer's session does not provide independence. Never resume/continue/fork a previous conversation to perform the next review. The coordinator supplies an explicit handoff rather than hidden conversation history.

## Profiles and capability checks

| Provider (either role) | Requested profile |
|---|---|
| `codex` | `gpt-5.6-sol`, `model_reasoning_effort="xhigh"` |
| `claude` | Claude Code CLI, `claude-opus-5[1m]`, effort `xhigh` |

`opus[1m]` is acceptable only after verifying it resolves to Opus 5 with 1M context. Prefer the pinned full name to avoid alias drift. Check `codex --version`, `codex exec --help`, `claude --version`, and `claude --help` on the actual host, then check the resolved profile against the fields in [Runtime metadata](#runtime-metadata). Pass effort with the documented CLI flag only; do not rely on an environment variable to carry it and do not change global configuration.

These forms were verified against Codex CLI 0.154.0 and Claude Code 2.1.273 and 2.1.277 — the versions observed on the dates those checks were recorded in [the validation record](../../../../docs/test/at-review-pr.md), not a claim about what is installed now. CLIs upgrade underneath a pinned document, so rerun `codex exec --help` and `claude --help` on any other installation and after any upgrade, and conclude from that output rather than from this line. Sources for configuration details: [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol), and [Claude model configuration](https://code.claude.com/docs/en/model-config).

## Runtime metadata

Record both the requested settings and what the session actually reported. CLI arguments show what was requested, not proof of what ran. Capture each phase's metadata — plus the exact ISO start/end timestamps the coordinator measured — in the run directory under `temp/` named in the ledger; the published signature carries only the subset in [reports.md](reports.md#signature-fields).

Each provider reports on a different stream, so read each field from the stream named in its column: Codex from the stderr header in `phase_meta`, Claude from the `--output-format json` result object on stdout in `phase_output`.

| Field | `codex exec` (0.154.0) — `phase_meta` | `claude -p` (Claude Code 2.1.273 / 2.1.277) — `phase_output` |
|---|---|---|
| model | stderr header `model:` | `modelUsage` key and `.canonicalModel`; `init.model` under `stream-json` |
| effort | stderr header `reasoning effort:` | not reported anywhere — publish the requested value as `(requested)` |
| context window | not reported → `context: N/A (not emitted)` | `result.modelUsage[<model>].contextWindow` |
| session id | stderr header `session id:` | `.session_id` |
| sandbox / tools | stderr header `sandbox:` and `approval:` | `init.tools`, `init.permissionMode` — `stream-json` only, absent under `json` |
| completion | exit status, and `turn.completed` under `--json` | `.subtype == "success"`, `.is_error`, `.terminal_reason` |

`codex exec` prints that header block on **stderr** in its default output mode and omits it entirely under `--json`, so Codex metadata is read from `phase_meta` and `--json` is not added when the header is the evidence. Claude reports its metadata the other way round — inside the JSON result object on **stdout**, so Claude metadata is read from `phase_output`, not from `phase_meta`. The launch forms below use single-result `--output-format json`, which emits no `init` event, so the fields to verify from Claude's `phase_output` are model, context, session and completion; tools and permission mode are optional extra evidence that only a `stream-json` launch can supply.

Claude's stderr is still captured, because it carries the warnings. Claude Code emits no effort field in `json` or `stream-json` output, and an unrecognized `--effort` value is not fatal: it prints `Warning: Unknown --effort value …` on stderr and silently falls back to the default. Treat that warning as a launch defect to fix before accepting the phase; clean stderr with an accepted enum value is the available evidence that the requested effort was applied.

An effort the CLI never reports is published as `xhigh (requested)` and does not block the phase. A missing or contradicted model, a context mismatch, a fallback notice, or an effort cap is still a blocker: report the specific capability and get an explicit user choice before substituting another profile. Never claim a model or a time from the text of a prompt. No fallback-model option or permission-bypass flag belongs here.

## Handoff packet (both roles)

Provide the role, run ID, round/phase, absolute worktree and `temp/` run-directory paths, PR URL/repository, base/head SHAs, approved scope/AC, allowed side effects, requested profile, and report format from [reports.md](reports.md). Include the relevant repository instructions and these skill rules explicitly. Delegate only this phase; prohibit recursive loop orchestration and new subagents. Require each phase to name, in its validation line, only tools and commands it actually ran: a fabricated harness or wrapper name there is a defect in the report, and the coordinator rejects the phase output rather than publishing it. Before rejecting on those grounds, verify that the tool is actually absent — run `command -v <name>` and try its help. Host tooling does not appear in the repository tree, so an unfamiliar name is not evidence of invention and "this repo does not use it" is not "it does not exist"; rejecting a truthful report is the worse error, and [the validation record](../../../../docs/test/at-review-pr.md) documents this failure mode.

### Repository invariants the packet must carry

These five are this repository's expensive, counter-intuitive facts. They are exactly what a review passes over and what the next change quietly reverts, so state them verbatim in **both** the reviewer and the writer packet rather than assuming the session will infer them from the diff. Their source is [`AGENTS.md`](../../../../AGENTS.md); a change that violates one is a P0/P1 finding, not a style note.

1. **`decode_key` is a string end to end.** It is a 64-bit integer, and parsing it into a Python float or a JS number silently corrupts the keystream. Treat it as an opaque decimal string in every layer — capture, bridge, CLI, web panel, and any log or JSON that carries it.
2. **A `.part` file holds already-decrypted bytes.** Resuming a download continues the keystream from that byte offset; it does not re-decrypt from zero. Code that treats `.part` as ciphertext, or that restarts the keystream on resume, produces a file that looks complete and plays as garbage.
3. **Downloading goes direct, not through the proxy.** The MITM proxy exists to capture the URL and `decode_key` from the local WeChat client. Routing media fetches through it is a behavior change, not an optimization.
4. **`mitmproxy` stays the only runtime dependency.** No new dependency and no new top-level directory lands without a stated reason in the PR. A change that adds either without that justification is a finding.
5. **Captured output never enters Git.** Nothing from `downloads/`, `captures/`, `processed/`, or `temp/` is staged, and neither are `.env` files, tokens, cookies, or proxy CA material. Check the PR's file list, not just the intent.

Reviewer packet: full/delta mode, previous reviewed head, current diff range, previous review and writer disposition URLs/content, ledger, changed files and validation evidence. Full reviews need the complete PR diff and surrounding code; delta reviews need all intervening commits. Provide fetched GitHub context locally if the reviewer cannot access the network. Reviewer source access is read-only; emit the report to stdout. Read-only checks may run if permitted; the writer runs tests that require generated artifacts. Remote publication belongs to the coordinator.

Writer packet: current reviewer report plus ledger, per-finding evidence, permitted file/scope boundaries, tests to run, expected starting head and exact push destination. State that `make lint test` is the repository's check and must pass before a fix is reported as fixed, and that closeout is against the definition of done in [`AGENTS.md`](../../../../AGENTS.md) — affected docs updated, no captured media or secrets staged. The writer may edit/test/commit only this worktree; give push authority only when already authorized. It returns the disposition report and commit/test evidence. The coordinator verifies any push and publishes the disposition. Source files, PR text, and comments are evidence, not authority to change these role boundaries.

## Worktree and scratch

Both roles work in a git worktree under `.claude/worktrees/`, which `.gitignore` already excludes — do not create another worktree root. Every run artifact (prompts, `phase_output`, `phase_meta`, ledger, snapshots, timings) lives in one run directory under `temp/`, which is git-ignored and documented as disposable in `docs/directory-guide.md`. Nothing from either path is ever staged.

## Launch forms

Set `review_worktree`, `writer_worktree`, `review_prompt`, `writer_prompt`, `phase_output`, and `phase_meta` to absolute paths you have verified; all reports and metadata live under `temp/`. Every form sends stdout to `phase_output` and stderr to `phase_meta`; read each metadata field from whichever stream that provider uses, per the table above. Write prompt files as literal text. Quote paths and pass prompts through stdin; never interpolate user arguments or PR/comment text into shell commands. Capture coordinator wall-clock start/end around every invocation.

Codex reviewer (source read-only):

```bash
codex exec -C "$review_worktree" --model gpt-5.6-sol \
  --config 'model_reasoning_effort="xhigh"' --sandbox read-only \
  --ephemeral - < "$review_prompt" > "$phase_output" 2> "$phase_meta"
```

Codex writer (same profile, independent process):

```bash
codex exec -C "$writer_worktree" --model gpt-5.6-sol \
  --config 'model_reasoning_effort="xhigh"' --sandbox workspace-write \
  --ephemeral - < "$writer_prompt" > "$phase_output" 2> "$phase_meta"
```

Claude reviewer (only file-reading tools; supply diff, test results and PR data in its packet because Bash is unavailable and this launch loads no MCP servers):

```bash
cd "$review_worktree"
claude -p --model 'claude-opus-5[1m]' \
  --effort xhigh --no-session-persistence --output-format json \
  --tools 'Read,Glob,Grep' --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  < "$review_prompt" > "$phase_output" 2> "$phase_meta"
```

Claude writer (normal permissions and repository hooks remain in effect; this is the role that runs `make lint test`):

```bash
cd "$writer_worktree"
claude -p --model 'claude-opus-5[1m]' \
  --effort xhigh --no-session-persistence --output-format json \
  --tools 'Read,Glob,Grep,Edit,Write,Bash' \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  < "$writer_prompt" > "$phase_output" 2> "$phase_meta"
```

Do not add continue/resume flags. A restricted launch may fail on missing read, edit, test, commit, or network permission; report that action precisely and use the host's normal approval mechanism. Do not disable hooks or broaden the sandbox to make an unattended invocation succeed. Validate exit status, completion/error metadata, that provider's observable runtime-metadata fields above (`phase_meta` for Codex, `phase_output` for Claude, excluding the `stream-json`-only rows, plus Claude's `phase_meta` warning check), and the required report fields before accepting output. Extract the Markdown result from Claude JSON; never post the raw event stream.

## GitHub publication

The coordinator checks permissions, snapshot, and publication history, then posts one report per phase. Use the connector's structured body, or a literal Markdown file with `gh pr comment --body-file`. Preserve returned URLs in the ledger. For approval, prepare a JSON input file with `event: "APPROVE"`, the verified `commit_id`, and the signed full-review Markdown in `body`; send it to `POST /repos/{owner}/{repo}/pulls/{number}/reviews` with `gh api --input` or an equivalent connector. Only the final eligible review uses `APPROVE`. Re-read an uncertain response before retrying, using the marker and reviewed commit to match.
