# Dynamic Codex Rate-Limit Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Codex rate-limit windows using the duration reported by each server payload instead of fixed primary/secondary assumptions.

**Architecture:** Keep provider parsing in the stdlib-only Python helper. Add one pure duration-label function, use it while normalizing primary and secondary blocks, and leave the existing normalized JSON/QML contract unchanged.

**Tech Stack:** Python 3 standard library, `unittest`, KDE Plasma 6 QML, Git.

## Global Constraints

- Never fabricate usage numbers or durations.
- Keep provider-specific parsing out of QML.
- Preserve primary-before-secondary ordering and the existing window schema.
- Derive exact whole-day, whole-hour, or whole-minute labels from positive integral `window_minutes`; otherwise use a neutral slot label.
- Do not add Python dependencies.

---

### Task 1: Test and implement Codex duration normalization

**Files:**
- Create: `tests/test_ai_usage_json.py`
- Modify: `package/contents/code/ai-usage-json:422-497`

**Interfaces:**
- Consumes: a Codex slot string and arbitrary `window_minutes` value.
- Produces: `codex_window_identity(slot, window_minutes) -> tuple[str, str]`.

- [ ] **Step 1: Write failing pure-function tests**

Load the extensionless helper with `runpy.run_path(..., run_name="ai_usage_json_test")`. Assert that 10080 becomes `("7d", "7-Day")`, 300 becomes `("5h", "5-Hour")`, 90 becomes `("90m", "90-Minute")`, and missing, boolean, fractional, zero, or negative values return the neutral `("primary", "Primary")` identity.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_ai_usage_json.CodexWindowIdentityTests -v`

Expected: failure because `codex_window_identity` is absent.

- [ ] **Step 3: Implement the minimal pure helper**

Validate that minutes are a positive integer but not a boolean. Prefer exact day divisibility, then exact hour divisibility, then minutes. Return the title-cased slot identity for invalid input.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python3 -m unittest tests.test_ai_usage_json.CodexWindowIdentityTests -v`

Expected: all identity tests pass.

### Task 2: Test and implement provider normalization

**Files:**
- Modify: `tests/test_ai_usage_json.py`
- Modify: `package/contents/code/ai-usage-json:422-497`

**Interfaces:**
- Consumes: `rate_limits.primary` and optional `.secondary` blocks returned by `codex_latest_rate_limits()`.
- Produces: the existing provider dictionary with dynamically keyed/labeled windows.

- [ ] **Step 1: Write failing provider tests**

Patch `codex_latest_rate_limits` with `unittest.mock.patch.dict(provider_codex.__globals__, ...)`. Assert that a current 10080-minute primary payload emits only `7d` / `7-Day`; a historical 300/10080 payload emits `5h` then `7d`; an invalid duration emits neutral `primary` / `Primary`; and a block without `used_percent` yields `available: false` with no windows.

- [ ] **Step 2: Run the focused provider tests and verify RED**

Run: `python3 -m unittest tests.test_ai_usage_json.CodexProviderTests -v`

Expected: duration-dependent expectations fail against the fixed `CODEX_WINDOWS` mapping.

- [ ] **Step 3: Replace the fixed duration mapping**

Iterate `("primary", "secondary")`, pass each block's `window_minutes` to `codex_window_identity`, and construct the existing window objects from real `used_percent` and `resets_at` values. Update the verified payload comment and empty-window error without changing QML.

- [ ] **Step 4: Run all Python tests and verify GREEN**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 3: Update documentation and verify the complete widget

**Files:**
- Modify: `README.md:29`
- Modify: `CLAUDE.md:3`

**Interfaces:**
- Consumes: the implemented server-derived duration behavior.
- Produces: documentation that no longer promises fixed Codex windows.

- [ ] **Step 1: Update documentation**

Describe Codex windows as dynamically derived from `window_minutes`; remove the broad fixed 5-hour/7-day claim from the repository guidance while retaining accurate Claude details.

- [ ] **Step 2: Run fresh full verification**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile package/contents/code/ai-usage-json
AI_USAGE_PROVIDERS=codex python3 package/contents/code/ai-usage-json | python3 -m json.tool
/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml
git diff --check
```

Expected: tests and compilation exit zero; live Codex JSON labels its 10080-minute primary window `7-Day`; QML lint has no `error:` lines and exits according to the repository's accepted Plasma/Kirigami warning policy; the diff check is clean.

- [ ] **Step 3: Commit implementation**

Create a short imperative implementation commit on a feature branch, review the resulting diff, and push that branch to the configured `origin` remote. Do not install the widget or restart Plasma automatically.
