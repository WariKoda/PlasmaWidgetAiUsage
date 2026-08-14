# Popup and Settings Regressions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep popup actions visible with multiple profiles and make the Plasma settings page initialize cleanly.

**Architecture:** Constrain only the provider list with a Plasma scroll view while keeping heading and refresh controls fixed. Declare the KConfig default-property interface expected by Plasma directly on the configuration root.

**Tech Stack:** Plasma 6 QML, Kirigami, Python `unittest` static contract tests.

## Global Constraints

- Keep provider-specific data handling out of QML.
- Use only QML APIs verified by the repository's Qt 6 `qmllint`.
- Do not change provider output or fabricate fallback data.
- Do not commit, tag, push, or update the pull request without a separate explicit request.

---

### Task 1: Keep popup actions visible

**Files:**
- Modify: `tests/test_qml_profile_configuration.py`
- Modify: `package/contents/ui/FullRepresentation.qml`

**Interfaces:**
- Consumes: `full.providers` and the existing `refreshRequested()` signal.
- Produces: A vertically scrollable provider area with a fixed refresh row.

- [ ] **Step 1: Write the failing layout contract test**

Add a test that locates `PlasmaComponents.ScrollView`, the provider `Repeater`, the separator, and the Refresh button and asserts that the repeater is inside the scroll-view block while the separator and button occur after it.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_qml_profile_configuration.ClaudeProfileConfigurationContractTests.test_full_representation_keeps_refresh_outside_scroll_area -v`

Expected: FAIL because `FullRepresentation.qml` has no `PlasmaComponents.ScrollView`.

- [ ] **Step 3: Implement the bounded provider area**

Wrap the provider repeater in a `PlasmaComponents.ScrollView` with `Layout.fillWidth: true`, `Layout.fillHeight: true`, and an inner `ColumnLayout` whose width follows the scroll viewport and whose height follows its implicit content height. Keep the separator and refresh row after the scroll view.

- [ ] **Step 4: Verify GREEN**

Run the focused test from Step 2 and expect PASS.

### Task 2: Supply Plasma's configuration defaults

**Files:**
- Modify: `tests/test_qml_profile_configuration.py`
- Modify: `package/contents/ui/config/ConfigGeneral.qml`

**Interfaces:**
- Consumes: all entries and defaults from `package/contents/config/main.xml`.
- Produces: typed `cfg_<entry>Default` properties expected by Plasma's configuration loader.

- [ ] **Step 1: Write the failing KConfig default contract test**

Parse all XML entries, map KConfig types to QML types, and assert that `ConfigGeneral.qml` contains a matching `property <type> cfg_<name>Default: <value>` declaration for every entry.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_qml_profile_configuration.ClaudeProfileConfigurationContractTests.test_settings_declares_every_kconfig_default_property -v`

Expected: FAIL for the currently undeclared Default properties.

- [ ] **Step 3: Add exact typed defaults**

Declare Boolean, integer, and string Default properties near the existing `cfg_claudeProfilesJson` property. Values must match `main.xml`, including the profile JSON string and empty token-file path.

- [ ] **Step 4: Verify GREEN and regressions**

Run the focused test and then `python3 -W error::ResourceWarning -m unittest discover -s tests -v`; expect all tests to pass.

- [ ] **Step 5: Run platform checks**

Run `/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml`, parse `main.xml`, and run `git diff --check`. Expect no QML errors, valid XML, and a clean whitespace check.
