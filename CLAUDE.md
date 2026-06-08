# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

KDE **Plasma 6 / Qt 6 / KF6** panel widget (`org.warikoda.aiusage`) showing AI coding-assistant usage (Claude / Codex / Gemini) as 5-hour and 7-day windows.

## Architecture (do not break this split)

- **`package/contents/code/ai-usage-json`** — Python 3 helper, **stdlib only** (no pip deps). ALL disk reads, API calls, and per-provider parsing live here. It emits one normalized JSON object (schema documented in its header docstring) and is configured purely via `AI_USAGE_*` environment variables.
- **`package/contents/ui/*.qml`** — the frontend stays *pure*: it runs the helper via the executable engine and renders the JSON. **Never add provider parsing or API logic to QML.** New data work goes into the Python helper.

## Core principle: never fabricate a usage number

A provider with no real data → `available: false` + a human `error` reason (rendered greyed-out). A window with no real percentage → `used_percent: null`, shown as its `detail` text or `—`, **never as `0 %`**. Do not invent or estimate a utilization value to fill a gap. The Claude provider may serve a stale on-disk cache on transient API failure (429/network), but it is explicitly marked `source: "api-cached"` with an age note — staleness is always surfaced, never silent.

## Lint after editing (mirrors CI `.github/workflows/lint.yml`)

- QML: `/usr/lib/qt6/bin/qmllint <file>` — use this exact Qt6 path; `/usr/bin/qmllint` is a broken Qt5 wrapper. Unresolved-import / i18n warnings for Plasma/Kirigami modules are **expected**; only real `error:` lines matter.
- Python helper: `python3 -m py_compile package/contents/code/ai-usage-json`
- Run the helper standalone to inspect output: `AI_USAGE_PROVIDERS=claude python3 package/contents/code/ai-usage-json | python3 -m json.tool`

## Install / reload — user-triggered, do not auto-run

- Per-user: `./install.sh` (or `kpackagetool6 --type Plasma/Applet --install package`).
- System-wide (all users): `./install.sh --system` (= `sudo kpackagetool6 --type Plasma/Applet --global --install package`). Runtime state stays per-user (`~/.cache/plasma-ai-usage/`, `~/.config/`, credentials in `~/.claude/` etc.).
- QML is interpreted — there is no build step. After code changes the **user** reloads Plasma (`systemctl --user restart plasma-plasmashell.service`); do not trigger reloads or installs automatically.

## Git

Feature branches + PRs; `main` stays clean. Branch before committing; commit/push only when asked.
