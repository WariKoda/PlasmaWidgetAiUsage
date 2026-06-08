---
name: verify-widget
description: Verify the AI Usage widget after changes — byte-compile and run the Python helper per provider, then qmllint every QML file. Use after editing the helper or any QML, or before opening a PR.
---

Run these checks from the repo root and report results concisely. Do not edit code as part of verification — only report what fails.

## 1. Python helper

```
python3 -m py_compile package/contents/code/ai-usage-json
```

Then run it standalone per provider and confirm the JSON is well-formed and honest (real numbers, or `available:false` / `used_percent:null` — never a fabricated `0 %`):

```
AI_USAGE_PROVIDERS=claude python3 package/contents/code/ai-usage-json | python3 -m json.tool
AI_USAGE_PROVIDERS=codex  python3 package/contents/code/ai-usage-json | python3 -m json.tool
```

(`gemini` always reports `available:false` by design — that is correct, not a failure.)

## 2. QML

Lint every QML file with the **Qt6** binary (the `/usr/bin/qmllint` wrapper is broken Qt5):

```
for f in package/contents/ui/*.qml; do /usr/lib/qt6/bin/qmllint "$f"; done
```

Unresolved-import and i18n warnings for Plasma/Kirigami modules are expected and not failures — flag only lines containing `error:` (same rule the CI uses).

## 3. Report

Summarise: helper compiles ✓/✗, helper output valid+honest ✓/✗, QML `error:` count per file. If everything is clean, say so plainly. Do not restart Plasma or install anything — the user does that.
