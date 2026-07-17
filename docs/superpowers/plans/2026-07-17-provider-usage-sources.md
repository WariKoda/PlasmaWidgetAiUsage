# Provider Usage Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read Claude usage from its official statusline payload, read Codex limits through the app-server protocol, and expire stale provider data honestly.

**Architecture:** A standalone Claude statusline collector writes a minimal private cache and preserves an existing statusline command. The existing helper owns setup management, provider precedence, freshness, Codex JSON-RPC, and Antigravity expiry; QML only invokes management commands and renders their JSON results.

**Tech Stack:** Python 3 standard library, QML/Qt 6, `unittest`, newline-delimited JSON-RPC over subprocess stdio.

## Global Constraints

- Keep provider-specific parsing and I/O out of QML.
- Keep all Python code standard-library-only.
- Never emit a fabricated percentage; expired real data is unavailable.
- Never alter Claude settings without the explicit settings-page action.
- Preserve the exact pre-existing Claude statusline behavior.
- Do not install the widget, restart Plasma, commit, or push automatically.

---

## File Map

- Create `package/contents/code/claude-usage-collector`: one-shot statusline stdin processor, cache writer, and previous-command passthrough.
- Modify `package/contents/code/ai-usage-json`: management CLI, freshness helpers, Claude source precedence, Codex app-server client, and Antigravity expiry.
- Modify `package/contents/ui/config/ConfigGeneral.qml`: setup status and explicit setup/remove controls.
- Modify `README.md`: current source table, setup instructions, freshness, and privacy.
- Modify `tests/test_ai_usage_json.py`: setup state, provider freshness, Codex app-server mapping, and Antigravity expiry.
- Create `tests/test_claude_usage_collector.py`: collector validation, permissions, and passthrough tests.

### Task 1: Claude statusline collector

**Files:**
- Create: `package/contents/code/claude-usage-collector`
- Create: `tests/test_claude_usage_collector.py`

**Interfaces:**
- Consumes stdin: one Claude statusline JSON object.
- Reads optional env `AI_USAGE_PREVIOUS_STATUSLINE` containing the previous shell command.
- Writes `~/.cache/plasma-ai-usage/claude-statusline.json` as `{fetched_at, rate_limits}`.
- Preserves the previous command's stdout, stderr, and exit status.

- [ ] **Step 1: Write failing collector tests**

Add subprocess tests which set an isolated `HOME` and assert:

```python
payload = {
    "rate_limits": {
        "five_hour": {"used_percentage": 23, "resets_at": 1784272799},
        "seven_day": {"used_percentage": 16, "resets_at": 1784710799},
    }
}
result = run_collector(payload)
self.assertEqual(result.returncode, 0)
self.assertEqual(read_cache()["rate_limits"], payload["rate_limits"])
self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
```

Cover malformed JSON, missing `rate_limits`, removal of unrelated fields, and a previous command such as `python3 -c 'import sys; print(sys.stdin.read())'` receiving the unchanged payload.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_claude_usage_collector -v`

Expected: FAIL because `package/contents/code/claude-usage-collector` does not exist.

- [ ] **Step 3: Implement the collector**

Implement these focused functions:

```python
def normalized_rate_limits(payload):
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None
    result = {}
    for name in ("five_hour", "seven_day"):
        block = rate_limits.get(name)
        if isinstance(block, dict) and isinstance(block.get("used_percentage"), (int, float)):
            result[name] = {
                "used_percentage": block["used_percentage"],
                "resets_at": block.get("resets_at"),
            }
    return result or None
```

Read stdin once, write atomically using directory mode `0700` and file mode `0600`, then run the previous command using `subprocess.run(command, shell=True, input=raw, text=True)`. The JSON is passed through stdin, never interpolated into the command.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_claude_usage_collector -v`

Expected: all collector tests PASS.

### Task 2: Claude setup management and provider freshness

**Files:**
- Modify: `package/contents/code/ai-usage-json`
- Modify: `tests/test_ai_usage_json.py`

**Interfaces:**
- CLI: `ai-usage-json --claude-integration status|setup|remove`.
- Status JSON: `{state, message, claude_version, can_setup}`.
- Managed state: `~/.cache/plasma-ai-usage/claude-integration.json` with the exact prior `statusLine` value and managed command.
- Freshness constants: collector 900 seconds, OAuth poll 900 seconds, OAuth maximum age 3600 seconds.

- [ ] **Step 1: Write failing setup and freshness tests**

Add tests using temporary homes and patched globals for:

```python
self.assertEqual(integration_status()["state"], "not-configured")
self.assertTrue(setup_integration()["ok"])
self.assertEqual(json.loads(settings.read_text())["statusLine"]["command"], managed_command)
self.assertEqual(remove_integration()["ok"], True)
self.assertEqual(json.loads(settings.read_text())["statusLine"], previous_statusline)
```

Also assert setup is idempotent, remove refuses a user-modified command, missing Claude returns `can_setup: false`, collector data older than 900 seconds is unavailable, expired windows are omitted, and OAuth data older than 3600 seconds is unavailable.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_ai_usage_json -v`

Expected: FAIL because management and freshness functions are missing.

- [ ] **Step 3: Implement management and freshness**

Add atomic JSON writing with a caller-supplied destination directory, version
detection via `claude --version`, and these functions with the stated return
contracts:

```python
def claude_integration_status():
    """Return the public state/message/version/can_setup management object."""

def claude_integration_setup():
    """Install or confirm the managed wrapper and return an ok/state object."""

def claude_integration_remove():
    """Restore saved settings or return a conflict error without writing."""

def cache_age_is_valid(fetched_at, maximum_age, now):
    """Return true only for numeric timestamps from now-maximum_age through now."""

def unexpired_windows(windows, now):
    """Return windows with no reset or with resets_at strictly greater than now."""
```

The managed statusline command invokes the bundled collector with a shell-quoted absolute path and sets `AI_USAGE_PREVIOUS_STATUSLINE` to the saved previous command. Setup writes state first, then settings; on settings failure it removes the incomplete state. Removal compares the currently installed command byte-for-byte before restoring.

Update `provider_claude()` precedence to fresh collector, eligible OAuth cache/API fallback, optional local estimate, then unavailable. A 429 message must say `usage query rate-limited` and include retry time when present.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_ai_usage_json -v`

Expected: all Claude management and provider tests PASS.

### Task 3: Codex app-server primary source

**Files:**
- Modify: `package/contents/code/ai-usage-json`
- Modify: `tests/test_ai_usage_json.py`

**Interfaces:**
- `codex_app_server_rate_limits(timeout=4)` returns a `RateLimitSnapshot` dict or `(None, error)`.
- JSON-RPC requests: `initialize` followed by `account/rateLimits/read`.
- Normalizes camelCase `planType`, `usedPercent`, `windowDurationMins`, and `resetsAt`.
- Keeps `codex_latest_rate_limits()` as a 900-second fallback carrying its event timestamp.

- [ ] **Step 1: Write failing Codex tests**

Use a temporary executable fake server which reads two JSON lines and emits:

```json
{"id":1,"result":{"userAgent":"fake"}}
{"id":2,"result":{"rateLimits":{"planType":"plus","primary":{"usedPercent":17,"windowDurationMins":300,"resetsAt":1784272799}}}}
```

Assert source `app-server`, plan `plus`, key `5h`, and percentage `17.0`. Add tests for timeout/error fallback, malformed response rejection, session event older than 900 seconds, and expired session windows.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_ai_usage_json.CodexProviderTests -v`

Expected: FAIL because the provider does not call the app server.

- [ ] **Step 3: Implement JSON-RPC and mapping**

Start `codex app-server --stdio` with pipes. Send exactly:

```python
{"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "plasma-ai-usage", "version": "0.1.4"}}}
{"id": 2, "method": "account/rateLimits/read", "params": None}
```

Read newline-delimited responses until response id `2`, enforce the total timeout, terminate the process in `finally`, and map the response to the existing normalized windows. Prefer `rateLimitsByLimitId["codex"]` when present, otherwise `rateLimits`.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_ai_usage_json.CodexProviderTests -v`

Expected: all Codex tests PASS.

### Task 4: Antigravity expiry

**Files:**
- Modify: `package/contents/code/ai-usage-json`
- Modify: `tests/test_ai_usage_json.py`

**Interfaces:**
- `ANTIGRAVITY_CACHE_MAX_AGE = 600`.
- Cache older than 600 seconds returns unavailable with no windows.

- [ ] **Step 1: Write failing boundary tests**

Patch `time.time`, `antigravity_user_status`, and `read_cache`. Assert age 599 returns `local-cached`, while age 601 returns `available: false`, `windows: []`, and an error containing both `Antigravity not running` and the age.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_ai_usage_json.AntigravityProviderTests -v`

Expected: FAIL because cached data never expires.

- [ ] **Step 3: Implement the age boundary**

Calculate cache age once, reject negative/future or over-age timestamps, and return no cached windows after expiry.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_ai_usage_json.AntigravityProviderTests -v`

Expected: all Antigravity tests PASS.

### Task 5: Settings-page controls, docs, and complete verification

**Files:**
- Modify: `package/contents/ui/config/ConfigGeneral.qml`
- Modify: `README.md`
- Modify: `package/metadata.json`

**Interfaces:**
- Settings page invokes only the helper management CLI and parses its management JSON.
- Buttons exist only in `ConfigGeneral.qml`, never in popup or compact representations.

- [ ] **Step 1: Add settings-page behavior**

Import `org.kde.plasma.plasma5support as P5Support`, add a management `DataSource`, and add a Claude status label plus setup/remove buttons. On component completion and after either action, run the status command. Disable buttons while a management command is active and surface stderr or malformed JSON as an error label.

Use these exact visible strings:

```qml
text: i18n("Set up usage collector")
text: i18n("Remove usage collector")
text: i18n("Claude Code must be installed and signed in first.")
```

- [ ] **Step 2: Update documentation and version**

Change the provider table to Claude statusline collector, Codex app-server with session fallback, and bounded Antigravity RPC cache. Document the explicit settings-page setup and cache ages. Bump metadata from `0.1.3` to `0.1.4`, and update the metadata version test accordingly.

- [ ] **Step 3: Run all verification**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile package/contents/code/ai-usage-json package/contents/code/claude-usage-collector
/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml
AI_USAGE_PROVIDERS=claude,codex,antigravity python3 package/contents/code/ai-usage-json | python3 -m json.tool
git diff --check
```

Expected: unit tests and compilation succeed; QML has no `error:` lines or non-zero exit; helper emits valid normalized JSON and unavailable sources have human-readable errors; diff check is clean.

- [ ] **Step 4: Review scope**

Confirm no popup setup button was added, no provider emits an expired percentage, no token or statusline input is logged, and no install/restart/commit/push occurred.
