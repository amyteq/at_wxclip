# Agent working agreement

`at_wxclip` (wxclip) captures a WeChat video URL through a local proxy, decodes the real
media address, downloads the file directly, and offers follow-up processing
(screenshots, text extraction, summarization). Read `README.md` and `docs/项目背景.md`
before product-level changes; read `docs/开发计划.md` before picking up work.

This file is the single source of truth for shared agent instructions. Claude Code reads
it through `CLAUDE.md`; Codex reads it directly from the repo root. Some Codex
environments mount `.agents/` and `.codex/` read-only, so nothing unique lives there.

## Priorities

- Keep the tool usable from the CLI first; the web UI is a thin layer over the same code.
- Prefer small, reversible changes and a flat structure over new abstraction layers.
- Land one capability end to end (capture → decode → download) before broadening scope.
- Do not add a dependency or a new top-level folder without saying why in the PR.

## Repository layout

Flat and slim by intent — see `docs/目录说明.md`:

| Path       | Holds                                                        |
|------------|--------------------------------------------------------------|
| `wxclip/`  | Python package: proxy, decoder bridge, downloader, web server |
| `web/`     | Frontend pages and the JavaScript decode logic                |
| `docs/`    | Background, plans, specs, directory notes                     |
| `tests/`   | Pytest suite (mirrors `wxclip/` module names)                 |
| `scripts/` | Developer entry points meant to be run by a human             |
| `tools/`   | One-off or auxiliary utilities, not part of the shipped package |
| `evals/`   | Fixtures and checks for decode/extract/summarize quality      |
| `temp/`    | Scratch space, git-ignored                                    |

## Engineering

- Python 3.11+, managed with `uv`; lint and format with `ruff`; test with `pytest`.
- `make lint test` must pass before a commit is pushed.
- Keep code, identifiers, and commit messages in English; non-technical docs default to
  Simplified Chinese.
- The proxy exists only to observe traffic from the local WeChat app on this machine.
  Downloading goes direct, not through the proxy.
- Never commit secrets, proxy CA material, captured traffic, or downloaded media.
  Treat anything under `downloads/`, `captures/`, and `temp/` as disposable.
- Record protocol findings (URL shapes, key derivation, header quirks) in `docs/` — they
  are the expensive part of this project and they drift when WeChat updates.

## Boundaries

- Personal-use capture of content the user can already play in their own WeChat client.
- Do not build DRM circumvention for paid or restricted content, bulk scraping of other
  people's accounts, or anything that redistributes captured media.
- No credential harvesting from the WeChat client; the proxy reads media requests, not
  account data.

## Definition of done

`make lint test` passes, docs affected by the change are updated, no captured media or
secrets are staged, and a collaborator can reproduce the change from the docs alone.
