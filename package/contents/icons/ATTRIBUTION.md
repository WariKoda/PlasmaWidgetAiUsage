# Provider logos — provenance

These brand marks are bundled for visual identification of each provider. The
trademarks belong to their respective owners; bundling here is for personal use.

| File | Source | Notes |
|------|--------|-------|
| `claude.svg`  | [Simple Icons](https://simpleicons.org/) (`claude`) — CC0 icon set | brand color `#D97757` |
| `codex.svg`   | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:OpenAI_logo_2025_(symbol).svg) — OpenAI symbol | recolored to `#10a37f` for panel visibility |
| `antigravity.svg` | Google Antigravity brand mark (the "spark"), icon glyph only | base path filled with a gradient approximating the brand blobs so Qt's QtSvg (no filter/mask support) still shows it in color; the original blurred-blob layers render in capable engines |

To swap in different artwork, drop a `<provider-id>.svg` here (`claude`,
`codex`, `antigravity`). The widget loads it automatically and falls back to a
colored-initial ring when a file is missing.
