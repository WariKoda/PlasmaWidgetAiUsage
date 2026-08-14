# Claude multi-profile design

## Goal

Represent every configured Claude Code profile separately in the Plasma widget. Profiles are discovered automatically, can be supplemented with manually configured directories, and can be renamed or hidden without changing the underlying Claude configuration.

## Profile discovery and identity

The helper discovers the default `~/.claude` directory and sibling directories matching `~/.claude-*`. A discovered directory is accepted only if it contains at least one Claude profile marker: `.credentials.json`, `settings.json`, or `projects/`.

Users can add profile directories outside the automatic search. A missing, unreadable, or structurally invalid manual directory remains visible as an unavailable profile with an explicit error. It is not silently discarded.

All paths are expanded and canonicalized before comparison. Automatic and manual entries resolving to the same directory produce one profile. Each profile receives a stable internal ID derived from its canonical path. Its default display name is derived from the directory name; users can override it without changing the ID.

The provider output keeps a stable profile-specific `id` and adds a base-provider field identifying all such entries as Claude. QML uses that base-provider field for the Claude icon and color.

## Configuration and user interface

The settings page contains a dynamic Claude profile list. Each row shows:

- editable display name;
- canonical or configured directory path;
- enabled/visible state;
- automatic or manual origin;
- removal control for manual entries only.

A path input adds manual profiles. Automatically discovered profiles can be hidden but not removed from discovery.

Profile overrides are stored as JSON in a KConfig string because KConfig entries have a static schema while the profile count is dynamic. The stored data contains only display names, enabled states, and manually added paths. Credentials and access tokens are never stored in widget configuration or passed directly on a process command line.

Discovery runs on each helper invocation. Newly created profiles therefore appear without reinstalling the widget. Overrides for temporarily absent automatic profiles remain stored and apply again if the profile returns.

Each enabled profile becomes a separate provider entry in the popup and a separate Claude chip in the compact panel representation. The popup displays the full configured profile name.

## Data collection and isolation

Provider collection is parameterized by a profile object instead of using global Claude paths. For each profile it reads:

- credentials from `<profile>/.credentials.json`;
- local fallback history from `<profile>/projects/`;
- Claude settings from `<profile>/settings.json`.

OAuth response caches, polling locks, retry state, and statusline caches are separated by a deterministic hash of the canonical profile path. Integration state is keyed by the real settings-file path so profiles sharing a symlinked settings file share one installation record. No private-profile usage response can be served for a work profile or vice versa.

The statusline collector derives the current profile from `CLAUDE_CONFIG_DIR`; when the variable is unset, it resolves the default `~/.claude` profile. It writes only to that profile's cache. Collector setup, status, and removal accept a profile directory and operate on that profile's settings and integration state.

Before modifying settings, integration management resolves the real settings path. Profiles sharing a symlinked settings file are treated as sharing one installation target, preventing contradictory double management. The installed collector command inherits `CLAUDE_CONFIG_DIR` from the active Claude process; usage caches therefore remain profile-specific even when settings are shared.

## Error handling

Failure is isolated to the affected profile. Required data is never replaced by invented values:

- invalid or unreadable manual paths remain unavailable with a clear reason;
- absent credentials produce an explicit API error;
- malformed settings, credentials, or cache data are reported or ignored only where an older verified data source remains valid;
- API failures may serve only a last known real response marked stale, or the existing explicitly labelled local token estimate;
- a window without a real percentage continues to use `used_percent: null`;
- duplicate paths are deterministically merged.

One profile failure does not suppress other Claude profiles, Codex, or Antigravity.

## Compatibility

Existing installations migrate without manual action. With no multi-profile override, `~/.claude` is discovered and behaves as the current Claude entry. Existing Claude fallback, extra-usage, and cap settings initially apply to every profile. If the new profile-specific cache is absent, existing single-profile cache data may be migrated once to the canonical default profile; it must never be read or migrated for another profile.

Codex and Antigravity behavior and configuration remain unchanged.

## Verification

Automated tests are written before implementation and cover:

- default and `~/.claude-*` discovery;
- rejection of unrelated similarly named directories;
- manual paths, including invalid paths;
- path and symlink deduplication;
- stable IDs, default names, renames, and visibility overrides;
- separate credentials, project history, API caches, locks, and collector caches;
- collector routing with set and unset `CLAUDE_CONFIG_DIR`;
- shared symlinked settings management;
- unavailable-profile behavior without fabricated usage;
- unchanged Codex and Antigravity output.

After implementation, run the focused unit tests followed by the complete Python test suite, `python3 -m py_compile` for both helpers, a normalized JSON smoke test, and Qt 6 `qmllint` for all widget QML files. Real QML errors and non-zero exits fail verification; known unresolved Plasma/Kirigami import or i18n warnings are documented separately.
