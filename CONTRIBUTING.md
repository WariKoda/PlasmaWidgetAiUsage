# Contributing

Thanks for your interest in improving the AI Usage widget. It's small and the
rules are few, but two of them are load-bearing.

## Architecture: keep the split

- **`package/contents/code/ai-usage-json`** — a Python 3 helper, **stdlib only**
  (no pip dependencies). ALL disk reads, API/RPC calls, and per-provider parsing
  live here. It emits one normalized JSON object (schema in its header docstring).
- **`package/contents/ui/*.qml`** — the frontend stays *pure*: it runs the helper
  and renders the JSON. **Don't add provider parsing or network logic to QML.**
  New data work goes into the Python helper.

## Never fabricate a usage number

A provider with no real data must report `available: false` plus a human-readable
`error` reason. A window with no real percentage uses `used_percent: null` (shown
as its `detail` text or `—`), **never a made-up `0 %`**. Don't estimate or guess a
utilization value to fill a gap. Staleness (e.g. a served cache) must be surfaced,
never silent.

## Before you open a PR

Run the same checks CI runs (`.github/workflows/lint.yml`):

```bash
# QML — use the Qt6 binary; /usr/bin/qmllint is often a broken Qt5 wrapper.
/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml
# Unresolved-import / i18n warnings for Plasma/Kirigami modules are expected;
# only real `error:` lines (non-zero exit) matter.

# Python helper
python3 -m py_compile package/contents/code/ai-usage-json

# Inspect the helper output directly
AI_USAGE_PROVIDERS=claude,codex,antigravity python3 package/contents/code/ai-usage-json | python3 -m json.tool
```

QML is interpreted — there is no build step. To try changes in a live panel,
install (`./install.sh`, or `./install.sh --system` for all users) and reload
Plasma (`systemctl --user restart plasma-plasmashell.service`).

## Pull requests

Work on a feature branch and keep `main` clean. Describe what changed and how you
verified it. Adding a new provider? Follow the existing `provider_*` functions in
the helper and the normalized `window()` / `provider()` shape.
