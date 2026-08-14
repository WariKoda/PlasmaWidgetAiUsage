# AI Usage — KDE Plasma 6 panel widget

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Plasma 6](https://img.shields.io/badge/Plasma-6-1d99f3.svg)
![Qt 6](https://img.shields.io/badge/Qt-6-41cd52.svg)

A configurable Plasma 6 panel widget that shows your AI coding assistant usage
at a glance: the rolling limit windows with reset countdowns, for
**Claude Code**, **OpenAI Codex** and **Google Antigravity**.

- **Compact panel view** — one chip per provider: a colored ring, the headline
  window's utilization and the reset countdown.
- **Popup detail view** — a `Window | Used | Reset` table per provider, the
  detected plan, the data source, and a *Refresh* button.

> _Add a `screenshots/preview.png` (panel + popup) after adding the widget to
> your panel, then embed it here._

## Provider support — and an honest data-source table

Different CLIs expose usage data very differently. This widget never invents a
number: when real data is not available a provider is shown greyed-out with the
reason, and a window with no real percentage shows a token estimate or `—`,
never a fabricated `0 %`.

| Provider | Primary source | Real % ? | Notes |
|----------|----------------|:--------:|-------|
| **Claude** | Claude Code statusline usage collector | ✅ | Claude Code 2.1.80+ supplies its reported 5-hour and 7-day rate-limit windows. The internal OAuth endpoint is a compatibility fallback. The widget discovers `~/.claude` and valid `~/.claude-*` profiles automatically. |
| **Codex** | Structured local app-server `account/rateLimits/read` | ✅ | Falls back to the newest local session rate-limit event when the app-server is unavailable. The displayed source distinguishes `app-server` from `local-session`. |
| **Antigravity** | Local `agy` language-server `GetUserStatus` Connect RPC with bounded cache | ✅ | Reads the signed-in quota over loopback while Antigravity is running. A successful response may be cached for only 10 minutes. |

Expired records and windows are rejected. The widget reports a human-readable
unavailable state instead of retaining an old percentage or substituting zero.

## Architecture

```
package/contents/
  code/ai-usage-json        # Python 3 (stdlib only) — normalizes all providers to one JSON
  code/claude-usage-collector # Claude statusline wrapper and private cache writer
  ui/main.qml               # PlasmoidItem: runs the helper via the executable engine, parses JSON
  ui/CompactRepresentation.qml   # panel chips
  ui/FullRepresentation.qml      # popup table
  ui/lib.js                 # formatting + threshold helpers
  ui/config/ConfigGeneral.qml    # settings page
  ui/config/ClaudeProfilesEditor.qml # dynamic Claude profile rows and row actions
  config/{config.qml,main.xml}   # config registration + keys
```

The QML frontend stays pure (no per-provider parsing logic). All disk/API work
lives in the single Python helper, which you can run and test standalone:

```bash
python3 package/contents/code/ai-usage-json | jq
```

The helper is configured entirely through environment variables, which the
widget sets from its settings:

| Env var | Meaning |
|---------|---------|
| `AI_USAGE_PROVIDERS` | comma list, e.g. `claude,codex,antigravity` |
| `AI_USAGE_CLAUDE_LOCAL_FALLBACK` | `0` to disable the offline token estimate |
| `AI_USAGE_CLAUDE_CAP_5H` / `AI_USAGE_CLAUDE_CAP_7D` | token caps to turn the local estimate into a % (0 = show raw tokens) |
| `AI_USAGE_CLAUDE_TOKEN` | override the access token instead of reading the credentials file |
| `AI_USAGE_CLAUDE_PROFILES_JSON` | internal widget/helper interface for manually added profile paths and profile preferences; it never contains tokens |

## Install

Requires Plasma 6 / Qt 6 / KF6 and `python3`.

```bash
# install (or --upgrade to update an existing install)
kpackagetool6 --type Plasma/Applet --install package
# then: right-click your panel → Add Widgets → "AI Usage"
```

A new plasmoid is only picked up after the shell rescans; if it does not appear,
run `kquitapp6 plasmashell && kstart plasmashell` (this restarts your panel).

`./install.sh` wraps the install/upgrade choice.

### System-wide (all users)

To make the widget available to every user on the machine, install it globally
with `-g/--global` (writes to `/usr/share/plasma/plasmoids/`, needs root):

```bash
sudo kpackagetool6 --type Plasma/Applet --global --install package
# or: ./install.sh --system
```

Only the widget *code* is shared. Each user still gets their own runtime state —
the response cache (`~/.cache/plasma-ai-usage/`), the widget settings
(`~/.config/`), and their own profile credentials and local history (for
example `~/.claude-*/.credentials.json` and `~/.codex/sessions/…`). No tokens
or usage data are shared between users; every user needs their own CLI
credentials (or a per-user token override) for their own numbers to show up.

## Configuration

Right-click the widget → *Configure*:

- **Providers** — show/hide Claude, Codex, Antigravity.
- **Refresh interval** — how often the helper runs (default 120 s).
- **Warning / Critical thresholds** — utilization % at which a value turns
  orange / red.
- **Panel** — show or hide the reset countdown in the compact view.
- **Claude profiles** — the widget automatically discovers `~/.claude` plus
  valid `~/.claude-*` directories. Add arbitrary profile directories manually,
  then use the individual rows in `ClaudeProfilesEditor.qml` to rename or hide
  profiles, remove manual entries, and set up or remove each profile's usage
  collector.
- **Claude** — enable the local token-estimate fallback, optional 5h/7d token
  caps, and an access-token override. With Claude Code 2.1.80 or newer installed
  and signed in, click **Set up usage collector** in this section. Setup occurs
  only after this explicit action and preserves an existing statusline command.
  **Remove usage collector** safely restores the saved configuration when the
  integration is still managed by this widget.

Collector controls exist only on the settings page; the panel and popup remain
display-only. Claude Code supplies collector data after it has processed its
first API response in a session. Its `CLAUDE_CONFIG_DIR` environment variable
selects the profile whose statusline data is written and read.

Each Claude profile keeps its credentials, local history, OAuth cache, and
collector cache separate from all other profiles. The profile JSON passed from
the widget to its helper stores only paths and rename/visibility preferences;
it never contains access tokens.

## Freshness

- Claude statusline data is valid for 15 minutes. OAuth fallback attempts are
  limited to once per 15 minutes and a successful response expires after 60
  minutes.
- Codex local-session fallback data is valid for 15 minutes.
- Antigravity's last successful RPC response is valid for 10 minutes.

Every window is also removed after its own reset time. Once a source or window
expires, its percentage is not displayed.

## Privacy

The collector writes only normalized rate-limit percentages, reset timestamps,
and a collection timestamp to a private local cache. It does not retain prompts,
transcript paths, access tokens, account email, or unrelated statusline input.
An existing statusline command receives its original input unchanged.

The optional Claude OAuth fallback reads the local Claude credential and sends
it only to Anthropic's API. Codex communicates with the local app-server or
reads local session events; Antigravity communicates with its loopback language
server. The widget does not upload provider data elsewhere. Setup status and
errors are deliberately limited and never expose raw settings or credentials.

## Status

Version `0.2.0`. Built and verified on Plasma 6.6.5 / Qt 6.10.

## Maintenance branches

`main` is the Plasma 6 development line. `plasma-5.27` is a separate,
long-lived compatibility branch. Shared fixes may flow in one direction only:
`main` → `plasma-5.27`. The branch must never be merged back into `main`. A
required repository check enforces this direction for pull requests.

## License

MIT — see [LICENSE](LICENSE).
