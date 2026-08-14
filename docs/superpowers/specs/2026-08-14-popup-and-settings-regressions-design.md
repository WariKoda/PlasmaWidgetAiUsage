# Popup and Settings Regressions Design

## Goal

Keep the popup controls reachable with any number of visible provider sections and allow Plasma to initialize the configuration page without missing-property errors.

## Popup layout

`FullRepresentation.qml` keeps its heading and bottom refresh row outside the scrolling area. The provider sections live in a `PlasmaComponents.ScrollView` that consumes only the remaining height. Its inner column sizes to the viewport width and to its natural content height, so long provider lists scroll vertically without increasing the popup or pushing the refresh button away.

## Configuration defaults

`ConfigGeneral.qml` declares every `cfg_<entry>Default` property corresponding to an entry in `package/contents/config/main.xml`. The types and values match the XML defaults exactly. The existing editable `cfg_*` aliases and `cfg_claudeProfilesJson` property remain unchanged.

## Verification

Static contract tests first demonstrate both regressions. The popup test checks that the provider repeater is inside the scroll view while the refresh row remains outside it. The configuration test derives all KConfig entries from `main.xml` and checks matching Default properties in QML. After implementation, run the focused tests, the complete Python test suite, XML parsing, `qmllint`, and `git diff --check`.
