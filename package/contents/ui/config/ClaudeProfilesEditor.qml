import QtQuick 2.15
import QtQuick.Controls 2.15 as Controls
import QtQuick.Layouts 1.15
import org.kde.kirigami 2.5 as Kirigami

ColumnLayout {
    id: root

    property var profiles: []
    property string configurationJson: "{\"manual\":[],\"overrides\":{}}"
    property var collectorStatuses: ({})
    property string managementProfilePath: ""
    property string editError: ""

    signal configurationEdited(string configurationJson)
    signal managementRequested(string action, string profilePath)

    function parseConfiguration() {
        try {
            var configuration = JSON.parse(configurationJson);
            if (!configuration || typeof configuration !== "object"
                    || Array.isArray(configuration)
                    || !Array.isArray(configuration.manual)
                    || !configuration.overrides
                    || typeof configuration.overrides !== "object"
                    || Array.isArray(configuration.overrides)) {
                throw new Error("invalid profile configuration");
            }
            return configuration;
        } catch (error) {
            editError = i18n("Could not edit Claude profiles: %1", error);
            return null;
        }
    }

    function publishConfiguration(configuration) {
        editError = "";
        // The page owns cfg_claudeProfilesJson. Emitting instead of assigning
        // configurationJson keeps its binding to that KConfig property intact.
        configurationEdited(JSON.stringify(configuration));
    }

    function updateOverride(profilePath, key, value) {
        var configuration = parseConfiguration();
        if (!configuration)
            return;
        var current = configuration.overrides[profilePath];
        var replacement = {};
        if (current && typeof current === "object" && !Array.isArray(current)) {
            for (var currentKey in current)
                replacement[currentKey] = current[currentKey];
        }
        replacement[key] = value;
        configuration.overrides[profilePath] = replacement;
        publishConfiguration(configuration);
    }

    function addManualProfile(profilePath) {
        var trimmedPath = String(profilePath).trim();
        if (trimmedPath.length === 0) {
            editError = i18n("Enter a Claude profile directory.");
            return false;
        }
        var configuration = parseConfiguration();
        if (!configuration)
            return false;
        configuration.manual.push({"path": trimmedPath});
        publishConfiguration(configuration);
        return true;
    }

    function removeManualProfile(profile) {
        var configuration = parseConfiguration();
        if (!configuration)
            return;
        var manualPaths = Array.isArray(profile.manual_paths)
            ? profile.manual_paths : [];
        if (manualPaths.length === 0) {
            editError = i18n("This profile has no manual entries.");
            return;
        }
        var remaining = [];
        var removedCount = 0;
        for (var index = 0; index < configuration.manual.length; ++index) {
            var entry = configuration.manual[index];
            var configuredPath = entry && typeof entry.path === "string"
                ? entry.path : "";
            if (manualPaths.indexOf(configuredPath) !== -1) {
                removedCount += 1;
            } else {
                remaining.push(entry);
            }
        }
        if (removedCount === 0) {
            editError = i18n("Could not match the manual Claude profile entries.");
            return;
        }
        // Keep overrides separate: the same canonical directory may also be
        // discovered automatically and must retain its label/enabled override.
        configuration.manual = remaining;
        publishConfiguration(configuration);
    }

    Repeater {
        model: root.profiles

        delegate: Controls.Frame {
            id: profileFrame

            required property var modelData
            readonly property var collectorStatus:
                root.collectorStatuses[modelData.canonical_path] || ({})

            Layout.fillWidth: true

            ColumnLayout {
                anchors.fill: parent

                RowLayout {
                    Layout.fillWidth: true

                    Controls.CheckBox {
                        checked: profileFrame.modelData.enabled === true
                        onClicked: root.updateOverride(
                            profileFrame.modelData.canonical_path,
                            "enabled",
                            checked)
                    }

                    Controls.TextField {
                        id: labelField
                        Layout.fillWidth: true
                        text: profileFrame.modelData.label
                        placeholderText: i18n("Profile name")
                        onEditingFinished: {
                            var label = text.trim();
                            if (label.length === 0) {
                                root.editError = i18n("Profile names must not be empty.");
                                text = profileFrame.modelData.label;
                                return;
                            }
                            root.updateOverride(
                                profileFrame.modelData.canonical_path,
                                "label",
                                label);
                        }
                    }

                    Controls.Label {
                        text: profileFrame.modelData.automatic
                            ? (profileFrame.modelData.manual_paths.length > 0
                                ? i18n("Automatic and manual")
                                : i18n("Automatic"))
                            : i18n("Manual")
                    }

                    Controls.Button {
                        text: i18n("Remove manual entries")
                        visible: profileFrame.modelData.manual_paths.length > 0
                        onClicked: root.removeManualProfile(profileFrame.modelData)
                    }
                }

                Controls.Label {
                    Layout.fillWidth: true
                    text: profileFrame.modelData.canonical_path
                    elide: Text.ElideMiddle
                    textFormat: Text.PlainText
                }

                Controls.Label {
                    Layout.fillWidth: true
                    text: profileFrame.modelData.profile_error || ""
                    visible: text.length > 0
                    color: Kirigami.Theme.negativeTextColor
                    wrapMode: Text.Wrap
                }

                Controls.Label {
                    Layout.fillWidth: true
                    text: profileFrame.collectorStatus.message
                        ? profileFrame.collectorStatus.message
                        : (profileFrame.collectorStatus.error ? ""
                            : i18n("Checking usage collector status…"))
                    visible: text.length > 0
                    wrapMode: Text.Wrap
                }

                Controls.Label {
                    Layout.fillWidth: true
                    text: profileFrame.collectorStatus.claudeVersion
                        ? i18n("Claude Code version: %1",
                               profileFrame.collectorStatus.claudeVersion)
                        : ""
                    visible: text.length > 0
                    wrapMode: Text.Wrap
                }

                Controls.Label {
                    Layout.fillWidth: true
                    text: profileFrame.collectorStatus.error || ""
                    visible: text.length > 0
                    color: Kirigami.Theme.negativeTextColor
                    wrapMode: Text.Wrap
                }

                RowLayout {
                    Controls.Button {
                        text: i18n("Set up usage collector")
                        enabled: root.managementProfilePath.length === 0
                            && profileFrame.collectorStatus.canSetup === true
                            && profileFrame.collectorStatus.state !== "configured"
                        onClicked: root.managementRequested(
                            "setup", profileFrame.modelData.canonical_path)
                    }

                    Controls.Button {
                        text: i18n("Remove usage collector")
                        visible: profileFrame.collectorStatus.state === "configured"
                        enabled: root.managementProfilePath.length === 0
                        onClicked: root.managementRequested(
                            "remove", profileFrame.modelData.canonical_path)
                    }

                    Controls.BusyIndicator {
                        running: root.managementProfilePath
                            === profileFrame.modelData.canonical_path
                        visible: running
                        implicitWidth: Kirigami.Units.iconSizes.small
                        implicitHeight: implicitWidth
                    }
                }
            }
        }
    }

    Controls.Label {
        Layout.fillWidth: true
        text: root.editError
        visible: text.length > 0
        color: Kirigami.Theme.negativeTextColor
        wrapMode: Text.Wrap
    }

    RowLayout {
        Layout.fillWidth: true

        Controls.TextField {
            id: manualPathField
            Layout.fillWidth: true
            placeholderText: i18n("Claude profile directory")
            onAccepted: addButton.clicked()
        }

        Controls.Button {
            id: addButton
            text: i18n("Add")
            enabled: manualPathField.text.trim().length > 0
            onClicked: {
                if (root.addManualProfile(manualPathField.text))
                    manualPathField.clear();
            }
        }
    }
}
