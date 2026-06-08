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
| **Claude** | `GET https://api.anthropic.com/api/oauth/usage` (Bearer token from `~/.claude/.credentials.json`) | ✅ | Matches the utilization shown on claude.ai. Optional local token-estimate fallback when the API/token is unavailable. |
| **Codex** | Newest `~/.codex/sessions/**/rollout-*.jsonl`, last `token_count` event (`payload.rate_limits.primary` / `.secondary`) | ✅ | Purely local — no network. Values come straight from what the Codex server last reported to the CLI. |
| **Antigravity** | Local `agy` language-server `getUserStatus` Connect RPC — per-model `quotaInfo.remainingFraction` + prompt credits | ✅ | The real signed-in quota the IDE shows, read over loopback (no internet). Its server runs only while Antigravity is open; once closed the tile shows the last-known values marked *cached (stale)*, or *unavailable* if it was never reached. |

The Claude OAuth usage endpoint is internal/undocumented; the exact response
shape used here (`five_hour` / `seven_day` with `utilization` + `resets_at`) was
verified live against a real account, not guessed.

## Architecture

```
package/contents/
  code/ai-usage-json        # Python 3 (stdlib only) — normalizes all providers to one JSON
  ui/main.qml               # PlasmoidItem: runs the helper via the executable engine, parses JSON
  ui/CompactRepresentation.qml   # panel chips
  ui/FullRepresentation.qml      # popup table
  ui/lib.js                 # formatting + threshold helpers
  ui/config/ConfigGeneral.qml    # settings page
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
(`~/.config/`), and the credentials it reads (`~/.claude/.credentials.json`,
`~/.codex/sessions/…`). No tokens or usage data are shared between users; every
user needs their own CLI credentials (or a per-user token override) for their
own numbers to show up.

## Configuration

Right-click the widget → *Configure*:

- **Providers** — show/hide Claude, Codex, Antigravity.
- **Refresh interval** — how often the helper runs (default 120 s).
- **Warning / Critical thresholds** — utilization % at which a value turns
  orange / red.
- **Panel** — show or hide the reset countdown in the compact view.
- **Claude** — enable the local token-estimate fallback, optional 5h/7d token
  caps, and an access-token override.

## Privacy

Tokens are read from your local CLI credential files and are sent **only** to
the matching official API host (Anthropic for Claude). Codex data is read from
local files and Antigravity data from its loopback language server; nothing is
uploaded anywhere by this widget.

## Status

Early `0.1.0`. Built and verified on Plasma 6.6.5 / Qt 6.10. The Antigravity
provider reads its quota from the local `agy` language server (live only while
Antigravity is running). Once closed it serves the last-known values from a local
cache, marked *cached (stale)* with an age note — never a fabricated fresh number;
if it was never reached, the tile is *unavailable*.

## License

MIT — see [LICENSE](LICENSE).
