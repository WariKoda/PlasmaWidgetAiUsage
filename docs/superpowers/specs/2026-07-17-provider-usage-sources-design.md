# Provider Usage Sources Design

## Goal

Replace Claude's undocumented OAuth usage polling as the primary source, adopt
Codex's structured app-server rate-limit API, and prevent indefinitely stale
Antigravity values from appearing available. The widget must continue to emit
only real provider-reported percentages.

## Constraints

- The Python helper remains Python 3 standard-library-only.
- Provider parsing, disk access, subprocesses, and RPC calls stay out of QML.
- Setup may modify Claude configuration only after an explicit action on the
  widget's settings page.
- Enabling the Claude provider must never silently modify
  `~/.claude/settings.json`.
- Existing Claude statusline output must be preserved.
- Missing or expired data produces `available: false`; it must never become a
  fabricated `0 %`.
- Widget installation and Plasma reload remain user-triggered.

## Source Strategy

### Claude

Claude Code 2.1.80 and newer exposes subscription rate-limit data to custom
statusline commands under:

- `rate_limits.five_hour.used_percentage`
- `rate_limits.five_hour.resets_at`
- `rate_limits.seven_day.used_percentage`
- `rate_limits.seven_day.resets_at`

A bundled collector reads the statusline JSON from stdin and atomically writes
only normalized rate-limit fields plus a collection timestamp to a private
cache file. It then invokes the user's previous statusline command with the
unchanged JSON input and passes through that command's stdout, stderr, and exit
status. If there was no previous statusline command, the collector emits no
display text and exits successfully after writing the cache.

The Claude provider reads fresh collector data first. The existing OAuth usage
endpoint becomes a compatibility fallback with conservative polling and server
backoff. OAuth fallback data receives the same maximum-age checks as collector
data and cannot remain available indefinitely.

Collector data remains usable for 15 minutes after capture. OAuth polling is
limited to one attempt per 15 minutes, and a last successful OAuth response may
remain usable for at most 60 minutes. A window whose `resets_at` lies in the
past is removed even when the enclosing record is otherwise young.

Collector data is expected only after Claude Code has processed its first API
response in a session. Absence therefore reports an honest setup or freshness
state rather than a zero value.

### Codex

For Codex versions that provide it, the helper starts the local structured
app-server protocol and requests `account/rateLimits/read`. It maps the returned
rate-limit windows to the widget's normalized schema and shuts the subprocess
down within a bounded timeout.

The existing scan of `~/.codex/sessions/**/rollout-*.jsonl` remains a fallback
for older Codex versions or temporary app-server failures. Results keep their
source distinguishable (`app-server` versus `local-session`) so failures and
fallback behavior remain observable.

The session fallback must retain the source event timestamp. It is usable for
at most 15 minutes, and individual windows are removed after their reset time.

Interactive `/status` and `/usage` output is not parsed because it is a
human-facing TUI surface rather than a stable machine interface.

### Antigravity

The current local `GetUserStatus` Connect RPC remains primary because no
documented personal Antigravity quota API was found. Google Cloud's Quotas API
describes project/service quotas and is not a substitute for a consumer
Antigravity account's model allowance.

The last successful Antigravity response may be shown as cached for 10 minutes.
After that period the provider becomes unavailable with
an error stating that Antigravity is not running and how old the last reading
is. Old percentages are not returned in `windows` once expired.

## Claude Setup Experience

Collector setup controls appear only on the widget's settings page in the
Claude section. The regular panel and popup representations remain display-only.

The settings page shows:

- detected Claude Code version and whether it supports statusline rate limits;
- collector state: not configured, configured, stale, or error;
- an explicit **Set up usage collector** action;
- when managed by the widget, an explicit **Remove integration** action;
- actionable setup errors without exposing tokens or the full credentials file.

The setup action invokes the helper in a dedicated management mode. Before
writing, it validates the current JSON configuration and the existing
`statusLine` object. It stores the exact previous statusline configuration in a
widget-owned private state file, installs a wrapper command, and performs the
settings update atomically. Re-running setup is idempotent.

Removal is offered only when the installed integration matches the widget's
managed wrapper. It restores the saved configuration atomically. If the user
changed the statusline after setup, removal fails safely with an explanation
instead of overwriting the newer configuration.

If Claude Code or its settings directory is absent, the settings page explains
that Claude Code must first be installed and signed in. It does not create a
pretend Claude installation.

## Data Freshness and Errors

Each cached provider record carries `fetched_at`. The helper validates that
timestamps are numeric, not implausibly in the future, and inside the source's
maximum age before returning windows as available.

- Fresh data: provider is available with its concrete source.
- Grace-period data: provider may be available as explicitly cached with an age
  message.
- Expired data: provider is unavailable and returns no percentage windows.
- HTTP 429: described as usage-query throttling with the retry time when known;
  it is not described as the user's plan allowance being exhausted.
- Malformed collector, RPC, or app-server data: rejected with a human-readable
  error; no partial numeric guesses are constructed.

The 15-minute Claude collector age, 15-minute OAuth polling interval,
60-minute OAuth maximum age, 15-minute Codex session maximum age, and 10-minute
Antigravity grace period are named constants in the helper and covered by
boundary tests.

## Components

- `package/contents/code/ai-usage-json`: provider selection, freshness policy,
  Codex app-server client, and setup/status/remove management commands.
- `package/contents/code/claude-usage-collector`: minimal stdlib-only statusline
  wrapper and atomic cache writer.
- `package/contents/ui/config/ConfigGeneral.qml`: Claude setup status and
  explicit management actions only.
- `package/contents/ui/main.qml`: executes helper management actions requested
  by settings UI and refreshes status; it does not parse provider payloads.
- `tests/test_ai_usage_json.py`: pure provider, freshness, app-server mapping,
  and setup-state tests.
- `tests/test_claude_usage_collector.py`: collector input validation, atomic
  cache behavior, and previous-command passthrough tests.
- `README.md`: supported sources, setup flow, freshness behavior, and privacy.

## Security and Privacy

Cache and integration-state directories use mode `0700`; files use `0600`.
Atomic replacement prevents partial JSON from being consumed. The collector
stores no prompts, transcript paths, access tokens, account email, or unrelated
statusline fields. Commands are executed without interpolating statusline JSON
into a shell command. Setup output redacts secrets and does not print the full
Claude settings document.

## Verification

Development follows test-first cycles for each behavior. Completion requires:

- `python3 -m unittest discover -s tests -v`
- `python3 -m py_compile package/contents/code/ai-usage-json`
- Python compilation of the collector
- `/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml`
- direct helper runs showing unavailable providers as `available: false` with
  human-readable errors when their real sources cannot be reached

No installation, Plasma restart, commit, or push is performed automatically.
