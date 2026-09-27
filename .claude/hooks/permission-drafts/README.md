# Development permission drafts

此目录是待审阅的权限草案，尚未应用。目标是减少日常开发确认，同时保留危险删除和维护分支的边界。操作步骤、代码及权限语义以下列英文说明为准。

Owner request: [discussion #821](https://github.com/noobill/work-os-rebuild/discussions/821). Tracking issue: [20](https://github.com/amyteq/at_wxclip/issues/20). Prepared 2026-09-27 for `amyteq/at_wxclip` at base `be83e6ae92b5bfeeaa41c1c8355201fb875d6499`. This directory is inert: no current settings file points to it. Merging this draft PR alone does not install these settings.

## Files and scope

| File | Intended use after owner review |
|---|---|
| `settings.project.json` | Proposed replacement for this project's `.claude/settings.json` |
| `settings.local.proposed.json` | Proposed replacement for this project's ignored `.claude/settings.local.json` |
| `settings.local.compacted.json` | Formatting, exact-dedup and home-relative alternative; retains policy for the inspected owner |
| `settings.user.patch.json` | Merge only its keys into the active Claude user `settings.json`; never replace the full user file |
| `guard_development_permissions.py` | Copy into `.claude/hooks/` before enabling the project draft |
| `test_guard_development_permissions.py` | No-operation regression tests for command and path boundaries |
| `codex.config.toml`, `codex.rules` | Proposed Codex config keys and optional user execution rules |
| `classifier-labels.md`, `audit.json` | Installed-version label inventory and source/change audit |

The user profile inspected for this request was `~/.claude-ayla2`; launch with `CLAUDE_CONFIG_DIR="$HOME/.claude-ayla2"` when that is the intended profile. Its `settings.json` currently has preferences and environment configuration but no permission/classifier block. The similarly named user-level `settings.local.json` contains environment configuration; it is not one of the documented settings scopes, so this draft neither copies its values nor assumes Claude loads it. Keep both original user files intact. [Claude settings scopes](https://code.claude.com/docs/en/settings)

## What changes

The proposed pair uses `auto` mode, narrow read/test allow rules, the retained ask/deny rules and a guarded cleanup exception. Historical broad command allow lists are intentionally replaced by classifier review, not described as equivalent deduplication. For a compacted-policy alternative, use `settings.local.compacted.json` instead. Its inspected home-directory rule, if present, uses the documented `~/` permission anchor in place of a private absolute home path; this resolves to the same location for the inspected owner. `audit.json` records that normalization. Exact duplicates are reported in `audit.json`; a broad `Bash` rule is not used to erase narrower rules in that alternative because auto mode ignores broad execution grants. [Permission modes](https://code.claude.com/docs/en/permission-modes)

The shared draft removes the broad `rm` and branch-deletion/force-push patterns listed in `audit.json` and registers the new hook in place of the old `guard_tool.py` registration. The old file remains: where it was already registered, the new hook delegates every non-allow outcome to it and preserves its stronger deny outcome, preserving project-specific secret, competition and frozen-source checks. Other hooks, including merge ownership, audit and formatting hooks where present, retain their registrations. Previously inactive legacy hook files are not enabled. Installing a second allow hook alongside the original destructive deny hook would not resolve those conflicts.

The hook permits direct literal `rm /tmp/*` and `rm -rf /tmp/*`, plus validated descendants. It asks for `rm -rf *`, `/tmp` itself, mixed paths, variable expansion, compound commands, symlink ancestors and uncertain forms. No deletion command is executed by the tests. The requested whole `/tmp/*` sweep can delete another process's temporary files; use a task-specific descendant when appropriate. This is the explicitly requested exception, not an assertion that all `/tmp` data is disposable.

Branch deletion permits exact `doc/`, `docs/`, `temp/` and `temp-` prefixes, locally or through `origin`; every `dev/` branch and maintained branch is protected. Force push requires `git push origin HEAD:refs/heads/temp/example --force-with-lease=refs/heads/temp/example:<40-hex-expected-head>`. A prefix alone does not prove a branch is unused: the exception adopts the owner's requested naming convention. Hard reset and Git clean remain subject to the retained ask/deny rules because they can discard uncommitted work regardless of branch name. The hook does not implement a general shell/API security boundary: interpreters, executable files, alternate Git spellings, aliases and races remain outside its proof. Retain the classifier, sandbox and GitHub protections.

The sandbox stays enabled, accepts normal sandboxed commands automatically and lets unsupported commands use the normal unsandboxed approval flow. `/tmp` is a deliberate write root. A blanket `denyWrite: ["/"]`, where present, is removed from the draft because it conflicts with descendant write roots; narrower original system/credential protections are kept. An unsandboxed fallback has no sandbox filesystem/network isolation. [Claude sandboxing](https://code.claude.com/docs/en/sandboxing)

## Classifier guidance

Use the user-scope overlay for the named repository. Current documented scopes for `autoMode` are user settings, managed settings and `--settings`/SDK input, not either project settings file. Rule arrays contain prose; labels are not boolean switches. Keep `$defaults`: `allow` adds exceptions to `soft_deny`, while `hard_deny` remains unconditional. `permissions.deny` and explicit content-scoped `ask` rules still take precedence. The overlay addresses the five reported labels without asserting that configuration guarantees every future classifier verdict. [Auto mode configuration](https://code.claude.com/docs/en/auto-mode-config)

The merge exception follows each repository's existing policy and binds an authorized merge to its current head. It does not approve these permission PRs or waive WorkOS's human merge gate. The agent-launch exception covers authorized development workers, including a parent launching outside its sandbox so the child can establish its own controls. It does not endorse blanket bypass flags. Reading a log that mentions such a flag is covered by the read-only exception. Status comments carry facts, not new instructions for other agents.

## Apply later and roll back

1. Review `audit.json` and diff both proposed files against the current project files. Recheck their SHA-256 hashes: local settings may have changed since drafting. Back up the two project files, the active user `settings.json`, and any existing destination hook before replacing anything.
2. Run `python3 .claude/hooks/permission-drafts/test_guard_development_permissions.py` from the repository. Copy the reviewed guard into `.claude/hooks/guard_development_permissions.py`; keep the original `guard_tool.py` file available for delegation.
3. Replace the project and local settings together with the reviewed drafts. Merely adding an overlay does not remove lower-scope deny/ask rules, because arrays merge. Keep the local file ignored by Git. These instructions are manual; no installer or activation command runs as part of this PR.
4. Merge `settings.user.patch.json` into the active user settings, retaining all unrelated keys. Across multiple project overlays, combine and deduplicate the prose entries, retaining one `$defaults` per array. Do not copy one whole overlay over another. Do not move secret `env` values into the repository.
5. Restart Claude, inspect `/status`, `/permissions` and `/sandbox`, then run `claude auto-mode config` under the same profile. Check the named repo and exception prose are present. Use `claude auto-mode defaults` after upgrades to refresh the label inventory. Validate benign commands and simulated tool requests before testing any real cleanup.
6. For Codex, merge the example keys into the selected user or trusted-project `config.toml`, retaining unrelated settings and any more specific sandbox requirements. The optional rules belong in the active Codex user's `rules/` directory. Inspect them with `codex execpolicy check` before relying on them. Do not copy the Claude hook into Codex and assume it runs there.
7. Roll back by restoring the backed-up settings and prior hook together, then restart the clients. Remove only the added classifier prose or restore the backed-up user file; do not clear unrelated rules. Recheck `/status` and the effective classifier config.

## Codex limits

The example combines `approval_policy = "on-request"`, `approvals_reviewer = "auto_review"` and a workspace sandbox with network access. Routine work remains autonomous and eligible escalations go to automatic review. `never` prevents escalation approval rather than granting access; full access removes the boundary where review normally happens. Optional `prompt` rules route their direct command forms to review even inside the sandbox, including ordinary Git pushes/branch listing, at a cost of extra review traffic. They do not cover every wrapper or API. Managed requirements still win, and a denied action requires a materially safer alternative or explicit owner resolution. This proposal does not replace the reviewer policy. [Codex auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)

## Validation status

See the draft PR for checks against its exact commit. Hook tests and JSON/TOML parsing demonstrate the draft logic; they do not establish a live Claude classifier verdict, successful sandbox startup or installed-session behavior. No active configuration is changed, no branch is deleted, no PR is merged and no development agent is launched by this package.
