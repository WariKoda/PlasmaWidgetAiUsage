import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.plasma.plasma5support as P5Support

KCM.SimpleKCM {
    id: page

    property bool managementBusy: false
    property string managementSource: ""
    property string managementAction: ""
    property int managementRequestId: 0
    property string collectorState: ""
    property string collectorMessage: ""
    property string claudeVersion: ""
    property bool collectorCanSetup: false
    property string managementError: ""
    property string pendingActionError: ""

    readonly property string helperPath:
        decodeURIComponent(Qt.resolvedUrl("../../code/ai-usage-json")
            .toString().replace(/^file:\/\//, ""))

    function shellQuote(value) {
        return "'" + String(value).replace(/'/g, "'\\''") + "'";
    }

    function managementCommand(action, requestId) {
        return "AI_USAGE_MANAGEMENT_REQUEST_ID=" + requestId
            + " python3 " + shellQuote(helperPath)
            + " --claude-integration " + shellQuote(action);
    }

    function runManagement(action) {
        if (managementBusy)
            return;
        managementBusy = true;
        managementAction = action;
        managementRequestId += 1;
        managementSource = managementCommand(action, managementRequestId);
        managementWatchdog.restart();
        managementExecutable.connectSource(managementSource);
    }

    function clearManagementStatus() {
        collectorState = "";
        collectorMessage = "";
        claudeVersion = "";
        collectorCanSetup = false;
    }

    function invalidateManagementStatus(errorMessage) {
        clearManagementStatus();
        managementError = pendingActionError.length > 0
            ? pendingActionError + "\n" + errorMessage : errorMessage;
        pendingActionError = "";
    }

    P5Support.DataSource {
        id: managementExecutable
        engine: "executable"
        connectedSources: []

        onNewData: function(sourceName, data) {
            if (sourceName !== page.managementSource) {
                disconnectSource(sourceName);
                return;
            }

            disconnectSource(sourceName);
            managementWatchdog.stop();
            var stdout = String(data["stdout"] || "");
            var stderr = String(data["stderr"] || "").trim();
            var actionWasStatus = page.managementAction === "status";
            page.managementBusy = false;
            page.managementSource = "";
            page.managementAction = "";

            if (stderr.length > 0) {
                if (actionWasStatus)
                    page.invalidateManagementStatus(stderr);
                else
                    page.pendingActionError = stderr;
            } else {
                try {
                    var result = JSON.parse(stdout);
                    if (!result || typeof result !== "object"
                            || typeof result.state !== "string"
                            || typeof result.message !== "string") {
                        throw new Error("invalid management response");
                    }
                    page.collectorState = result.state;
                    page.collectorMessage = result.message;
                    page.claudeVersion = result.claude_version
                        ? String(result.claude_version) : "";
                    page.collectorCanSetup = result.can_setup === true;
                    if (actionWasStatus) {
                        page.managementError = page.pendingActionError;
                        page.pendingActionError = "";
                    } else if (result.ok === false) {
                        page.pendingActionError = result.message;
                    } else {
                        page.pendingActionError = "";
                        page.managementError = "";
                    }
                } catch (error) {
                    var parseError = i18n("Could not read usage collector status: %1", error);
                    if (actionWasStatus)
                        page.invalidateManagementStatus(parseError);
                    else
                        page.pendingActionError = parseError;
                }
            }

            if (!actionWasStatus)
                page.runManagement("status");
        }
    }

    Timer {
        id: managementWatchdog
        interval: 30000
        repeat: false
        onTriggered: {
            if (!page.managementBusy || page.managementSource.length === 0)
                return;
            var actionWasStatus = page.managementAction === "status";
            managementExecutable.disconnectSource(page.managementSource);
            page.managementBusy = false;
            page.managementSource = "";
            page.managementAction = "";
            var timeoutError = i18n("Usage collector management timed out.");
            if (actionWasStatus) {
                page.invalidateManagementStatus(timeoutError);
            } else {
                page.clearManagementStatus();
                page.pendingActionError = timeoutError;
                page.runManagement("status");
            }
        }
    }

    // cfg_<name> aliases are auto-bound to the matching main.xml entries.
    property alias cfg_showClaude: showClaude.checked
    property alias cfg_showCodex: showCodex.checked
    property alias cfg_showAntigravity: showAntigravity.checked

    property alias cfg_refreshIntervalSec: refreshSpin.value
    property alias cfg_warnThreshold: warnSpin.value
    property alias cfg_critThreshold: critSpin.value
    property alias cfg_showResetInCompact: showReset.checked

    property alias cfg_claudeLocalFallback: localFallback.checked
    property alias cfg_claudeExtraUsage: extraUsage.checked
    property alias cfg_claudeCap5h: cap5h.value
    property alias cfg_claudeCap7d: cap7d.value
    property alias cfg_claudeTokenFile: tokenFileField.text

    Kirigami.FormLayout {
        anchors.fill: parent

        Controls.CheckBox {
            id: showClaude
            Kirigami.FormData.label: i18n("Providers:")
            text: i18n("Claude")
        }
        Controls.CheckBox {
            id: showCodex
            text: i18n("Codex")
        }
        Controls.CheckBox {
            id: showAntigravity
            text: i18n("Antigravity")
        }

        Item { Kirigami.FormData.isSection: true }

        Controls.SpinBox {
            id: refreshSpin
            Kirigami.FormData.label: i18n("Refresh interval (s):")
            from: 15
            to: 3600
            stepSize: 15
        }

        Controls.SpinBox {
            id: warnSpin
            Kirigami.FormData.label: i18n("Warning threshold (%):")
            from: 1
            to: 100
        }
        Controls.SpinBox {
            id: critSpin
            Kirigami.FormData.label: i18n("Critical threshold (%):")
            from: 1
            to: 100
        }

        Controls.CheckBox {
            id: showReset
            Kirigami.FormData.label: i18n("Panel:")
            text: i18n("Show reset countdown in panel")
        }

        Item { Kirigami.FormData.isSection: true }

        Controls.CheckBox {
            id: localFallback
            Kirigami.FormData.label: i18n("Claude:")
            text: i18n("Local token estimate when the API is unavailable")
        }
        Controls.CheckBox {
            id: extraUsage
            text: i18n("Show extra usage")
        }
        Controls.Label {
            Kirigami.FormData.label: i18n("Usage collector:")
            text: page.collectorMessage.length > 0
                ? page.collectorMessage
                : i18n("Checking usage collector status…")
            wrapMode: Text.Wrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
        }
        Controls.Label {
            text: i18n("Claude Code version: %1", page.claudeVersion)
            visible: page.claudeVersion.length > 0
            wrapMode: Text.Wrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
        }
        Controls.Label {
            text: i18n("Claude Code must be installed and signed in first.")
            visible: page.collectorState === "not-configured"
                && !page.collectorCanSetup
            wrapMode: Text.Wrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
        }
        Controls.Label {
            text: page.managementError
            visible: page.managementError.length > 0
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.Wrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
        }
        RowLayout {
            Controls.Button {
                text: i18n("Set up usage collector")
                enabled: !page.managementBusy && page.collectorCanSetup
                    && page.collectorState !== "configured"
                onClicked: page.runManagement("setup")
            }
            Controls.Button {
                text: i18n("Remove usage collector")
                visible: page.collectorState === "configured"
                enabled: !page.managementBusy
                onClicked: page.runManagement("remove")
            }
            Controls.BusyIndicator {
                running: page.managementBusy
                visible: running
                implicitWidth: Kirigami.Units.iconSizes.small
                implicitHeight: implicitWidth
            }
        }
        Controls.SpinBox {
            id: cap5h
            Kirigami.FormData.label: i18n("5-hour token cap (0 = off):")
            from: 0
            to: 1000000000
            stepSize: 100000
            editable: true
        }
        Controls.SpinBox {
            id: cap7d
            Kirigami.FormData.label: i18n("7-day token cap (0 = off):")
            from: 0
            to: 1000000000
            stepSize: 100000
            editable: true
        }
        Controls.TextField {
            id: tokenFileField
            Kirigami.FormData.label: i18n("Access token file:")
            placeholderText: i18n("leave empty to use ~/.claude/.credentials.json")
            Layout.preferredWidth: Kirigami.Units.gridUnit * 16
        }
    }

    Component.onCompleted: page.runManagement("status")
}
