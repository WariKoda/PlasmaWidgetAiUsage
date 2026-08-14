import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package"
MAIN_QML = PACKAGE / "contents/ui/main.qml"
CONFIG_QML = PACKAGE / "contents/ui/config/ConfigGeneral.qml"
METADATA = PACKAGE / "metadata.json"
INSTALLER = ROOT / "install.sh"
README = ROOT / "README.md"


class Plasma527CompatibilityContractTests(unittest.TestCase):
    def test_main_uses_plasma5_root_attached_properties_and_data_source(self):
        source = MAIN_QML.read_text(encoding="utf-8")

        self.assertRegex(source, r"(?m)^import QtQuick 2\.15$")
        self.assertIn("import org.kde.plasma.plasmoid 2.0", source)
        self.assertIn("import org.kde.plasma.core 2.0 as PlasmaCore", source)
        self.assertRegex(source, r"(?m)^Item \{$")
        self.assertIn("width: PlasmaCore.Units.gridUnit * 18", source)
        self.assertIn("height: PlasmaCore.Units.gridUnit * 18", source)
        self.assertIn("Plasmoid.toolTipMainText:", source)
        self.assertIn("Plasmoid.compactRepresentation:", source)
        self.assertIn("Plasmoid.fullRepresentation:", source)
        self.assertIn("PlasmaCore.DataSource {", source)
        self.assertNotIn("PlasmoidItem", source)
        self.assertNotIn("P5Support", source)

    def test_configuration_uses_plasma5_root_and_data_sources(self):
        source = CONFIG_QML.read_text(encoding="utf-8")

        self.assertIn("import QtQuick 2.15", source)
        self.assertIn("import QtQuick.Controls 2.15 as Controls", source)
        self.assertIn("import QtQuick.Layouts 1.15", source)
        self.assertIn("import org.kde.plasma.core 2.0 as PlasmaCore", source)
        self.assertRegex(source, r"(?m)^Item \{$")
        self.assertIn("implicitWidth: formLayout.implicitWidth", source)
        self.assertIn("implicitHeight: formLayout.implicitHeight", source)
        self.assertIn("id: formLayout", source)
        self.assertEqual(source.count("PlasmaCore.DataSource {"), 2)
        self.assertNotIn("org.kde.kcmutils", source)
        self.assertNotIn("org.kde.plasma.plasma5support", source)

    def test_all_qml_imports_are_versioned_and_qt5_has_no_bound_pragma(self):
        for path in sorted((PACKAGE / "contents").rglob("*.qml")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("pragma ComponentBehavior", source, str(path))
            for line in source.splitlines():
                stripped = line.strip()
                if not stripped.startswith("import ") or stripped.startswith('import "'):
                    continue
                self.assertRegex(
                    stripped,
                    r"^import [A-Za-z0-9_.]+ [0-9]+\.[0-9]+(?: as \w+)?$",
                    f"unversioned Qt 5 import in {path}: {stripped}",
                )

    def test_metadata_targets_plasma5_declarative_applet(self):
        metadata = json.loads(METADATA.read_text(encoding="utf-8"))

        self.assertNotIn("X-Plasma-API-Minimum-Version", metadata)
        self.assertEqual(metadata["X-Plasma-API"], "declarativeappletscript")
        self.assertEqual(metadata["X-Plasma-MainScript"], "ui/main.qml")

    def test_installer_uses_only_plasma5_tools(self):
        source = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("kpackagetool5", source)
        self.assertIn("kquitapp5 plasmashell", source)
        self.assertIn("kstart5 plasmashell", source)
        self.assertNotRegex(source, r"kpackagetool6|kquitapp6|\bkstart\b")

    def test_readme_states_exact_runtime_floor_and_plasma5_commands(self):
        source = README.read_text(encoding="utf-8")

        self.assertIn("Plasma 5.27", source)
        self.assertIn("Qt 5.15", source)
        self.assertIn("Python 3.10", source)
        self.assertIn("kpackagetool5", source)
        self.assertNotIn("PlasmoidItem", source)


if __name__ == "__main__":
    unittest.main()
