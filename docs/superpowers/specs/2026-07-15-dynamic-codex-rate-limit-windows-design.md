# Dynamic Codex Rate-Limit Windows

## Context

Codex session events expose real rate-limit data under
`payload.rate_limits.primary` and, when present, `.secondary`. The widget
currently assigns fixed meanings to these slots: primary is labelled as a
5-hour window and secondary as a 7-day window.

Current locally recorded server data no longer matches that assumption. The
primary block reports `window_minutes: 10080`, representing a 7-day window,
and no secondary block. Consequently, the widget shows the correct percentage
and reset timestamp under an incorrect 5-hour label.

## Decision

The Python helper will derive each Codex window's normalized key and display
label from the block's real `window_minutes` value instead of assigning a
duration based on whether the block is primary or secondary.

Known integral durations will use concise normalized values:

- whole days: `<n>d` and `<n>-Day`
- whole hours: `<n>h` and `<n>-Hour`
- remaining durations: `<n>m` and `<n>-Minute`

If `window_minutes` is absent, invalid, non-positive, or unsuitable for an
exact duration label, the helper will retain the window when it has a real
`used_percent`, but identify it neutrally by its server slot (`primary` or
`secondary`). It will not infer a duration.

Primary remains ordered before secondary. The first valid window remains the
headline window used by the compact QML representation. No QML schema or UI
component changes are required.

## Components and Data Flow

1. `codex_latest_rate_limits()` continues to read the latest real server
   payload from local Codex rollout files.
2. A pure duration-normalization helper converts a slot name and
   `window_minutes` into a normalized key and label.
3. `provider_codex()` iterates the primary and secondary slots, uses that
   helper, and emits the existing normalized window schema.
4. Existing QML views render the server-derived label, percentage, and reset
   timestamp without provider-specific logic.

## Error Handling and Data Integrity

- A block without a real `used_percent` is omitted, matching existing
  behavior.
- Missing or malformed duration metadata never becomes a fabricated 5-hour or
  7-day value.
- If neither slot yields a usable window, Codex remains unavailable with a
  human-readable error.
- Percentages and reset timestamps continue to come directly from Codex
  session data.

## Testing and Verification

Tests will exercise the pure normalization/provider behavior before production
code is changed:

- the current single primary 10080-minute block becomes `7d` / `7-Day`;
- the historical 300-minute primary plus 10080-minute secondary payload
  becomes `5h` / `5-Hour` followed by `7d` / `7-Day`;
- missing or invalid duration metadata produces a neutral slot label without
  inventing a duration;
- blocks without a percentage do not produce fake usage windows.

Final verification will include the focused Python tests, Python compilation,
the repository's direct helper-output check, and QML linting. Documentation
will describe Codex window durations as server-derived rather than fixed.

## Git Delivery

The implementation and documentation updates will be committed with a short,
imperative subject. After verification, the current `main` branch will be
pushed to its configured `origin` remote as requested.
