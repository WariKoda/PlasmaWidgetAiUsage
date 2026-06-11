pragma ComponentBehavior: Bound

import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import "lib.js" as Lib

PlasmoidItem {
    id: root

    // -- Parsed helper output ------------------------------------------------
    property var report: null          // { generated_at, providers: [...] }
    property string lastError: ""      // non-empty when the last run failed
    property bool loading: false
    property string currentSource: ""  // command of the in-flight helper run

    // -- Panel hover tooltip -------------------------------------------------
    toolTipMainText: i18n("AI Usage")
    toolTipSubText: buildTooltip()

    function buildTooltip() {
        if (root.lastError.length > 0)
            return root.lastError;
        if (!root.report || !root.report.providers)
            return root.loading ? i18n("Loading…") : "";
        var lines = [];
        var list = root.report.providers;
        for (var i = 0; i < list.length; ++i) {
            var p = list[i];
            if (p.available) {
                var parts = [];
                for (var j = 0; j < p.windows.length; ++j) {
                    var w = p.windows[j];
                    var val = (w.used_percent !== null && w.used_percent !== undefined)
                        ? Lib.fmtPct(w.used_percent)
                        : (w.detail ? w.detail : "—");
                    parts.push(w.label + " " + val
                               + " (" + Lib.fmtCountdown(w.resets_at, Date.now(), i18n("now")) + ")");
                }
                var plan = p.plan ? " · " + p.plan : "";
                lines.push(p.label + plan + ":\n   " + parts.join("\n   "));
            } else {
                lines.push(p.label + ": " + (p.error ? p.error : i18n("unavailable")));
            }
        }
        return lines.join("\n");
    }

    // Absolute path to the bundled Python helper. We invoke it through
    // `python3 <path>` so a lost executable bit after packaging is irrelevant.
    readonly property string helperPath:
        Qt.resolvedUrl("../code/ai-usage-json").toString().replace(/^file:\/\//, "")

    // -- Build the helper command from the current configuration -------------
    function enabledProviders() {
        var list = [];
        if (Plasmoid.configuration.showClaude) list.push("claude");
        if (Plasmoid.configuration.showCodex)  list.push("codex");
        if (Plasmoid.configuration.showAntigravity) list.push("antigravity");
        return list;
    }

    function shellQuote(s) {
        return "'" + String(s).replace(/'/g, "'\\''") + "'";
    }

    function buildCommand() {
        var env = [];
        env.push("AI_USAGE_PROVIDERS=" + enabledProviders().join(","));
        if (!Plasmoid.configuration.claudeLocalFallback)
            env.push("AI_USAGE_CLAUDE_LOCAL_FALLBACK=0");
        var c5 = Plasmoid.configuration.claudeCap5h;
        var c7 = Plasmoid.configuration.claudeCap7d;
        if (c5 > 0) env.push("AI_USAGE_CLAUDE_CAP_5H=" + c5);
        if (c7 > 0) env.push("AI_USAGE_CLAUDE_CAP_7D=" + c7);
        // A token *file path*, never the raw token: this string ends up on the
        // helper's command line, which any local user can read via /proc.
        var tokFile = Plasmoid.configuration.claudeTokenFile;
        if (tokFile && tokFile.length > 0) env.push("AI_USAGE_CLAUDE_TOKEN_FILE=" + shellQuote(tokFile));
        return env.join(" ") + " python3 " + shellQuote(helperPath);
    }

    // -- Executable engine: run the helper, parse its JSON --------------------
    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []

        onNewData: function(sourceName, data) {
            disconnectSource(sourceName);
            watchdog.stop();
            root.loading = false;
            var stdout = (data["stdout"] || "");
            var stderr = (data["stderr"] || "");
            try {
                var parsed = JSON.parse(stdout);
                root.report = parsed;
                root.lastError = "";
            } catch (e) {
                root.lastError = "helper error: " + (stderr.trim() || e);
            }
        }

        function exec(cmd) { connectSource(cmd); }
    }

    function refresh() {
        if (root.loading) return;
        root.loading = true;
        root.currentSource = buildCommand();
        watchdog.restart();
        executable.exec(root.currentSource);
    }

    Timer {
        interval: Math.max(15, Plasmoid.configuration.refreshIntervalSec) * 1000
        running: true
        repeat: true
        onTriggered: root.refresh()
    }

    // Recover from a run whose onNewData never fires: the executable engine can
    // silently fail to deliver, leaving `loading` latched true forever -- after
    // which the refresh Timer bails at `if (root.loading) return` and the widget
    // freezes on its last report. 30 s is 3x the helper's 10 s HTTP timeout and
    // well under the default refresh, so it only trips on a genuinely stuck run.
    Timer {
        id: watchdog
        interval: 30000
        repeat: false
        onTriggered: {
            if (root.loading) {
                executable.disconnectSource(root.currentSource);
                root.loading = false;
                root.lastError = i18n("refresh timed out");
            }
        }
    }
    Component.onCompleted: root.refresh()

    // -- Representations -----------------------------------------------------
    // No forced preferredRepresentation: Plasma then picks by form factor --
    // the compact chips in a panel, the full table as a desktop widget.

    compactRepresentation: CompactRepresentation {
        report: root.report
        loading: root.loading
        warnThreshold: Plasmoid.configuration.warnThreshold
        critThreshold: Plasmoid.configuration.critThreshold
        showReset: Plasmoid.configuration.showResetInCompact
        onClicked: root.expanded = !root.expanded
    }

    fullRepresentation: FullRepresentation {
        report: root.report
        loading: root.loading
        lastError: root.lastError
        warnThreshold: Plasmoid.configuration.warnThreshold
        critThreshold: Plasmoid.configuration.critThreshold
        onRefreshRequested: root.refresh()
    }
}
