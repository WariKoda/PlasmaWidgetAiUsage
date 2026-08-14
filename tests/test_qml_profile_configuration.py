import json
import re
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_XML = ROOT / "package/contents/config/main.xml"
CONFIG_GENERAL = ROOT / "package/contents/ui/config/ConfigGeneral.qml"
PROFILE_EDITOR = ROOT / "package/contents/ui/config/ClaudeProfilesEditor.qml"
MAIN_QML = ROOT / "package/contents/ui/main.qml"
PROVIDER_ICON = ROOT / "package/contents/ui/ProviderIcon.qml"
COMPACT_REPRESENTATION = ROOT / "package/contents/ui/CompactRepresentation.qml"
FULL_REPRESENTATION = ROOT / "package/contents/ui/FullRepresentation.qml"


def compact_qml(path):
    if not path.exists():
        raise AssertionError(f"required QML file does not exist: {path}")
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


class ClaudeProfileConfigurationContractTests(unittest.TestCase):
    def test_kconfig_persists_profile_configuration_json(self):
        root = ElementTree.parse(MAIN_XML).getroot()
        namespace = {"kcfg": "http://www.kde.org/standards/kcfg/1.0"}
        entries = {
            entry.attrib["name"]: entry
            for entry in root.findall(".//kcfg:entry", namespace)
        }

        self.assertIn("claudeProfilesJson", entries)
        entry = entries["claudeProfilesJson"]
        self.assertEqual(entry.attrib["type"], "String")
        self.assertEqual(
            entry.findtext("kcfg:default", namespaces=namespace),
            '{"manual":[],"overrides":{}}',
        )

    def test_settings_declares_every_kconfig_default_property(self):
        root = ElementTree.parse(MAIN_XML).getroot()
        namespace = {"kcfg": "http://www.kde.org/standards/kcfg/1.0"}
        qml_source = CONFIG_GENERAL.read_text(encoding="utf-8")
        qml_types = {"Bool": "bool", "Int": "int", "String": "string"}

        for entry in root.findall(".//kcfg:entry", namespace):
            name = entry.attrib["name"]
            kconfig_type = entry.attrib["type"]
            default = entry.findtext("kcfg:default", default="", namespaces=namespace)
            if kconfig_type == "Bool":
                qml_default = default.lower()
            elif kconfig_type == "Int":
                qml_default = default
            else:
                qml_default = json.dumps(default)
            declaration = (
                f"property {qml_types[kconfig_type]} "
                f"cfg_{name}Default: {qml_default}"
            )
            self.assertIn(declaration, qml_source, declaration)

    def test_profile_editor_exposes_the_configuration_contract(self):
        source = compact_qml(PROFILE_EDITOR)

        self.assertRegex(source, r"property var profiles\s*:")
        self.assertRegex(source, r"property string configurationJson\s*:")
        self.assertIn("signal configurationEdited(string configurationJson)", source)
        self.assertIn(
            "signal managementRequested(string action, string profilePath)", source
        )
        self.assertIn("Repeater", source)
        self.assertIn("model: root.profiles", source)

    def test_settings_wires_shell_quoted_discovery_and_profile_management(self):
        source = compact_qml(CONFIG_GENERAL)

        self.assertIn(
            'AI_USAGE_CLAUDE_PROFILES_JSON=" + shellQuote(cfg_claudeProfilesJson)',
            source,
        )
        self.assertIn('" --claude-profiles"', source)
        self.assertRegex(
            source,
            r"function managementCommand\(action, profilePath, requestId\)",
        )
        self.assertIn('" --profile " + shellQuote(profilePath)', source)

    def test_collector_status_is_keyed_by_canonical_profile_path(self):
        settings = compact_qml(CONFIG_GENERAL)
        editor = compact_qml(PROFILE_EDITOR)

        self.assertRegex(settings, r"property var collectorStatuses\s*:")
        self.assertIn("statuses[profilePath]", settings)
        self.assertIn("modelData.canonical_path", editor)
        self.assertIn("root.collectorStatuses[modelData.canonical_path]", editor)

    def test_manual_profile_removal_uses_raw_paths_and_keeps_overrides(self):
        source = compact_qml(PROFILE_EDITOR)

        self.assertIn("profile.manual_paths", source)
        self.assertIn("manualPaths.indexOf(configuredPath)", source)
        self.assertIn("configuration.manual = remaining", source)
        self.assertNotIn("delete configuration.overrides", source)
        self.assertIn("visible: profileFrame.modelData.manual_paths.length > 0", source)
        self.assertIn('text: i18n("Remove manual entries")', source)

    def test_mutating_collector_action_refreshes_every_visible_profile(self):
        source = compact_qml(CONFIG_GENERAL)

        self.assertIn(
            "if (!actionWasStatus) "
            "page.queueProfileStatuses(page.discoveredProfiles);",
            source,
        )
        self.assertNotIn(
            "currentStatuses[profilePath] = collectorStatuses[profilePath] ||",
            source,
        )

    def test_collector_error_does_not_also_show_checking_message(self):
        source = compact_qml(PROFILE_EDITOR)

        self.assertIn(
            ': (profileFrame.collectorStatus.error ? "" '
            ': i18n("Checking usage collector status…"))',
            source,
        )

    def test_normal_refresh_shell_quotes_profile_configuration(self):
        source = compact_qml(MAIN_QML)

        self.assertIn(
            'env.push("AI_USAGE_CLAUDE_PROFILES_JSON=" + shellQuote('
            "Plasmoid.configuration.claudeProfilesJson));",
            source,
        )

    def test_normal_refresh_discards_stale_source_without_stopping_current_run(self):
        source = compact_qml(MAIN_QML)
        stale_guard = source.index("if (sourceName !== root.currentSource)")
        stale_disconnect = source.index("disconnectSource(sourceName)", stale_guard)
        stale_return = source.index("return;", stale_disconnect)
        current_watchdog_stop = source.index("watchdog.stop()", stale_return)

        self.assertLess(stale_guard, stale_disconnect)
        self.assertLess(stale_disconnect, stale_return)
        self.assertLess(stale_return, current_watchdog_stop)
        self.assertIn("interval: root.helperTotalDeadlineMs + root.watchdogMarginMs", source)
        self.assertIn("root.runSerial += 1", source)
        self.assertIn('"AI_USAGE_RUN_ID=" + root.runSerial + " " + root.buildCommand()', source)

    def test_token_file_help_describes_each_profile_credentials(self):
        source = compact_qml(CONFIG_GENERAL)

        self.assertIn(
            'placeholderText: i18n("leave empty to use each profile\'s credentials")',
            source,
        )
        self.assertNotIn("~/.claude/.credentials.json", source)

    def test_multi_profile_icons_use_base_provider_assets(self):
        icon = compact_qml(PROVIDER_ICON)
        compact = compact_qml(COMPACT_REPRESENTATION)
        full = compact_qml(FULL_REPRESENTATION)

        self.assertIn('property string baseProvider: ""', icon)
        self.assertIn(
            "readonly property string assetProvider: baseProvider.length > 0 "
            "? baseProvider : providerId",
            icon,
        )
        self.assertIn('pic.assetProvider + ".svg"', icon)
        self.assertIn("Lib.providerColor(pic.assetProvider)", icon)
        self.assertIn("Lib.providerInitial(pic.assetProvider)", icon)
        self.assertIn(
            "baseProvider: chip.modelData.base_provider "
            "? chip.modelData.base_provider : chip.modelData.id",
            compact,
        )
        self.assertIn(
            "baseProvider: section.modelData.base_provider "
            "? section.modelData.base_provider : section.modelData.id",
            full,
        )

    def test_full_representation_keeps_refresh_outside_scroll_area(self):
        source = compact_qml(FULL_REPRESENTATION)

        self.assertIn("PlasmaComponents.ScrollView", source)
        scroll_view = source.index("PlasmaComponents.ScrollView")
        provider_repeater = source.index("Repeater { model: full.providers", scroll_view)
        separator = source.index("Kirigami.Separator", provider_repeater)
        refresh_button = source.index('text: i18n("Refresh")', separator)

        self.assertLess(scroll_view, provider_repeater)
        self.assertLess(provider_repeater, separator)
        self.assertLess(separator, refresh_button)
        self.assertIn("Layout.fillHeight: true", source[scroll_view:provider_repeater])


if __name__ == "__main__":
    unittest.main()
