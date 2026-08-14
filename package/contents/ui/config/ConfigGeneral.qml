import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.plasma.plasma5support as P5Support

KCM.SimpleKCM {
    id: page

    // cfg_<name> properties are auto-bound to matching main.xml entries.
    property string cfg_claudeProfilesJson: "{\"manual\":[],\"overrides\":{}}"

    property var discoveredProfiles: []
    property string discoveryError: ""
    property bool discoveryBusy: false
    property string discoverySource: ""
    property int discoveryRequestId: 0
    property bool componentReady: false

    property bool managementBusy: false
    property string managementSource: ""
    property string managementAction: ""
    property string managementProfilePath: ""
    property int managementRequestId: 0
    property var managementQueue: []
    property var collectorStatuses: ({})
    property var pendingActionErrors: ({})

    readonly property string helperPath:
        decodeURIComponent(Qt.resolvedUrl("../../code/ai-usage-json")
            .toString().replace(/^file:\/\//, ""))

    function shellQuote(value) {
        return "'" + String(value).replace(/'/g, "'\\''") + "'";
    }

    function copyObject(source) {
        var copy = {};
        if (!source || typeof source !== "object")
            return copy;
        for (var key in source)
            copy[key] = source[key];
        return copy;
    }

    function discoveryCommand() {
        return "AI_USAGE_CLAUDE_PROFILES_JSON=" + shellQuote(cfg_claudeProfilesJson)
            + " AI_USAGE_DISCOVERY_REQUEST_ID=" + discoveryRequestId
            + " python3 " + shellQuote(helperPath) + " --claude-profiles";
    }

    function runDiscovery() {
        discoveryRequestId += 1;
        if (discoverySource.length > 0)
            discoveryExecutable.disconnectSource(discoverySource);
        discoverySource = discoveryCommand();
        discoveryBusy = true;
        discoveryError = "";
        discoveryWatchdog.restart();
        discoveryExecutable.connectSource(discoverySource);
    }

    function managementCommand(action, profilePath, requestId) {
        return "AI_USAGE_MANAGEMENT_REQUEST_ID=" + requestId
            + " python3 " + shellQuote(helperPath)
            + " --claude-integration " + shellQuote(action)
            + " --profile " + shellQuote(profilePath);
    }

    function setCollectorStatus(profilePath, state, message, version,
                                canSetup, errorMessage) {
        var statuses = copyObject(collectorStatuses);
        statuses[profilePath] = {
            "state": state,
            "message": message,
            "claudeVersion": version,
            "canSetup": canSetup,
            "error": errorMessage
        };
        collectorStatuses = statuses;
    }

    function setPendingActionError(profilePath, errorMessage) {
        var errors = copyObject(pendingActionErrors);
        if (errorMessage.length > 0)
            errors[profilePath] = errorMessage;
        else
            delete errors[profilePath];
        pendingActionErrors = errors;
    }

    function pendingActionError(profilePath) {
        return pendingActionErrors[profilePath]
            ? String(pendingActionErrors[profilePath]) : "";
    }

    function combineErrors(first, second) {
        if (first.length === 0)
            return second;
        if (second.length === 0)
            return first;
        return first + "\n" + second;
    }

    function queueProfileStatuses(profiles) {
        var queue = [];
        var currentStatuses = {};
        for (var index = 0; index < profiles.length; ++index) {
            var profilePath = String(profiles[index].canonical_path || "");
            if (profilePath.length === 0)
                continue;
            currentStatuses[profilePath] = {
                "state": "",
                "message": "",
                "claudeVersion": "",
                "canSetup": false,
                "error": ""
            };
            queue.push({"action": "status", "profilePath": profilePath});
        }
        collectorStatuses = currentStatuses;
        managementQueue = queue;
        runNextManagementRequest();
    }

    function startManagement(action, profilePath) {
        if (managementBusy || String(profilePath).length === 0)
            return;
        managementBusy = true;
        managementAction = action;
        managementProfilePath = String(profilePath);
        managementRequestId += 1;
        managementSource = managementCommand(
            action, managementProfilePath, managementRequestId);
        managementWatchdog.restart();
        managementExecutable.connectSource(managementSource);
    }

    function runManagement(action, profilePath) {
        if (managementBusy)
            return;
        startManagement(action, profilePath);
    }

    function runNextManagementRequest() {
        if (managementBusy || managementQueue.length === 0)
            return;
        var queue = managementQueue.slice();
        var request = queue.shift();
        managementQueue = queue;
        startManagement(request.action, request.profilePath);
    }

    function clearManagementLifecycle() {
        managementBusy = false;
        managementSource = "";
        managementAction = "";
        managementProfilePath = "";
    }

    function validManagementResult(result) {
        return result && typeof result === "object"
            && typeof result.state === "string"
            && typeof result.message === "string";
    }

    P5Support.DataSource {
        id: discoveryExecutable
        engine: "executable"
        connectedSources: []

        onNewData: function(sourceName, data) {
            if (sourceName !== page.discoverySource) {
                disconnectSource(sourceName);
                return;
            }

            disconnectSource(sourceName);
            discoveryWatchdog.stop();
            page.discoveryBusy = false;
            page.discoverySource = "";
            var stdout = String(data["stdout"] || "");
            var stderr = String(data["stderr"] || "").trim();
            if (stderr.length > 0) {
                page.discoveredProfiles = [];
                page.discoveryError = stderr;
                page.queueProfileStatuses([]);
                return;
            }

            try {
                var result = JSON.parse(stdout);
                if (!result || typeof result !== "object"
                        || !Array.isArray(result.profiles)) {
                    throw new Error("invalid profile discovery response");
                }
                if (result.ok === false) {
                    throw new Error(result.error
                        ? String(result.error)
                        : "profile discovery failed");
                }
                page.discoveredProfiles = result.profiles;
                page.discoveryError = "";
                page.queueProfileStatuses(result.profiles);
            } catch (error) {
                page.discoveredProfiles = [];
                page.discoveryError = i18n(
                    "Could not discover Claude profiles: %1", error);
                page.queueProfileStatuses([]);
            }
        }
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
            var action = page.managementAction;
            var profilePath = page.managementProfilePath;
            var actionWasStatus = action === "status";
            var stdout = String(data["stdout"] || "");
            var stderr = String(data["stderr"] || "").trim();
            var result = null;
            var responseError = stderr;

            if (responseError.length === 0) {
                try {
                    result = JSON.parse(stdout);
                    if (!page.validManagementResult(result))
                        throw new Error("invalid management response");
                } catch (error) {
                    responseError = i18n(
                        "Could not read usage collector status: %1", error);
                }
            }

            page.clearManagementLifecycle();

            if (responseError.length > 0) {
                if (actionWasStatus) {
                    var earlierError = page.pendingActionError(profilePath);
                    page.setPendingActionError(profilePath, "");
                    page.setCollectorStatus(
                        profilePath, "", "", "", false,
                        page.combineErrors(earlierError, responseError));
                } else {
                    page.setPendingActionError(profilePath, responseError);
                    page.setCollectorStatus(
                        profilePath, "", "", "", false, responseError);
                }
            } else if (actionWasStatus) {
                var pendingError = page.pendingActionError(profilePath);
                page.setPendingActionError(profilePath, "");
                page.setCollectorStatus(
                    profilePath,
                    result.state,
                    result.message,
                    result.claude_version ? String(result.claude_version) : "",
                    result.can_setup === true,
                    pendingError);
            } else {
                var actionError = result.ok === false ? result.message : "";
                page.setPendingActionError(profilePath, actionError);
                page.setCollectorStatus(
                    profilePath,
                    result.state,
                    result.message,
                    result.claude_version ? String(result.claude_version) : "",
                    result.can_setup === true,
                    actionError);
            }

            if (!actionWasStatus)
                page.queueProfileStatuses(page.discoveredProfiles);
            else
                page.runNextManagementRequest();
        }
    }

    Timer {
        id: discoveryWatchdog
        interval: 30000
        repeat: false
        onTriggered: {
            if (!page.discoveryBusy || page.discoverySource.length === 0)
                return;
            discoveryExecutable.disconnectSource(page.discoverySource);
            page.discoveryBusy = false;
            page.discoverySource = "";
            page.discoveredProfiles = [];
            page.discoveryError = i18n("Claude profile discovery timed out.");
            page.queueProfileStatuses([]);
        }
    }

    Timer {
        id: managementWatchdog
        interval: 30000
        repeat: false
        onTriggered: {
            if (!page.managementBusy || page.managementSource.length === 0)
                return;
            var action = page.managementAction;
            var profilePath = page.managementProfilePath;
            var actionWasStatus = action === "status";
            managementExecutable.disconnectSource(page.managementSource);
            page.clearManagementLifecycle();
            var timeoutError = i18n("Usage collector management timed out.");
            if (actionWasStatus) {
                var earlierError = page.pendingActionError(profilePath);
                page.setPendingActionError(profilePath, "");
                page.setCollectorStatus(
                    profilePath, "", "", "", false,
                    page.combineErrors(earlierError, timeoutError));
            } else {
                page.setPendingActionError(profilePath, timeoutError);
                page.setCollectorStatus(
                    profilePath, "", "", "", false, timeoutError);
                page.queueProfileStatuses(page.discoveredProfiles);
            }
            if (actionWasStatus)
                page.runNextManagementRequest();
        }
    }

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

    onCfg_claudeProfilesJsonChanged: {
        if (componentReady)
            runDiscovery();
    }

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
            Kirigami.FormData.label: i18n("Profiles:")
            text: page.discoveryBusy
                ? i18n("Discovering Claude profiles…") : page.discoveryError
            visible: text.length > 0
            color: page.discoveryError.length > 0
                ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            wrapMode: Text.Wrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 30
        }

        ClaudeProfilesEditor {
            id: profileEditor
            Layout.fillWidth: true
            Layout.maximumWidth: Kirigami.Units.gridUnit * 30
            profiles: page.discoveredProfiles
            configurationJson: page.cfg_claudeProfilesJson
            collectorStatuses: page.collectorStatuses
            managementProfilePath: page.managementProfilePath

            onConfigurationEdited: function(configurationJson) {
                if (page.cfg_claudeProfilesJson !== configurationJson)
                    page.cfg_claudeProfilesJson = configurationJson;
                else
                    page.runDiscovery();
            }
            onManagementRequested: function(action, profilePath) {
                page.runManagement(action, profilePath);
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
            placeholderText: i18n("leave empty to use each profile's credentials")
            Layout.preferredWidth: Kirigami.Units.gridUnit * 16
        }
    }

    Component.onCompleted: {
        componentReady = true;
        runDiscovery();
    }
}
