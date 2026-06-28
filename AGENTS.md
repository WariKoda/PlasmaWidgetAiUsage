# Repository Guidelines

## Project Structure & Module Organization

This repository contains a KDE Plasma 6 applet packaged under `package/`.
The Python helper lives at `package/contents/code/ai-usage-json` and is
responsible for all provider data collection, parsing, cache handling, and JSON
normalization. QML UI files live in `package/contents/ui/`, with
`main.qml` wiring the plasmoid to the helper, `CompactRepresentation.qml` for
panel chips, `FullRepresentation.qml` for the popup, and `ui/config/` for the
settings page. Static provider icons are in `package/contents/icons/`, and
Plasma metadata/config definitions are in `package/metadata.json` and
`package/contents/config/`.

## Build, Test, and Development Commands

There is no compile build step; QML is interpreted by Plasma.

- `./install.sh`: install or upgrade the applet for the current user.
- `./install.sh --system`: install system-wide using `sudo`.
- `/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml`: lint QML with the Qt 6 binary.
- `python3 -m py_compile package/contents/code/ai-usage-json`: check helper syntax.
- `AI_USAGE_PROVIDERS=claude,codex,antigravity python3 package/contents/code/ai-usage-json | python3 -m json.tool`: inspect normalized helper output.

After installing, reload Plasma if needed with
`systemctl --user restart plasma-plasmashell.service`.

## Coding Style & Naming Conventions

Keep provider-specific logic out of QML. Add disk reads, API/RPC calls, and
provider parsing only to `ai-usage-json`; the UI should render the normalized
JSON schema. The helper must remain Python 3 stdlib-only, with no pip
dependencies. Follow existing provider naming such as `provider_codex()` and
shared constructors such as `window()` / `provider()`. In QML, use clear
camelCase properties and functions matching the existing style.

## Testing Guidelines

Run the QML linter and Python compile check before submitting changes. QML
unresolved-import or i18n warnings for Plasma/Kirigami modules can be expected;
treat real `error:` lines and non-zero exits as failures. For provider changes,
run the helper directly and confirm unavailable data reports `available: false`
with a human-readable `error`.

## Commit & Pull Request Guidelines

History currently starts with `Initial commit: AI Usage Plasma 6 widget ...`;
use short, imperative commit subjects that summarize the changed behavior.
Pull requests should describe what changed, how it was verified, and any user
visible impact. Link related issues when available. For UI changes, include a
screenshot or note that the widget was installed and checked in Plasma.

## Agent-Specific Instructions

Never fabricate usage numbers. If real data is unavailable, return
`available: false`; if a window lacks a real percentage, use
`used_percent: null` and meaningful detail text rather than `0 %`.
