pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import "lib.js" as Lib

Item {
    id: compact

    property var report: null
    property bool loading: false
    property int warnThreshold: 75
    property int critThreshold: 90
    property bool showReset: true

    signal clicked()

    property double nowMs: Date.now()
    Timer {
        interval: 30000; running: true; repeat: true
        onTriggered: compact.nowMs = Date.now()
    }

    readonly property var providers: (report && report.providers) ? report.providers : []

    function levelColor(level) {
        if (level === "crit") return Kirigami.Theme.negativeTextColor;
        if (level === "warn") return Kirigami.Theme.neutralTextColor;
        if (level === "ok")   return Kirigami.Theme.positiveTextColor;
        return Kirigami.Theme.disabledTextColor;
    }

    // A panel sizes an applet through the Layout attached properties, not
    // implicitWidth. Drive both from the row's content width so the panel
    // allocates enough horizontal space and the text never bleeds into the
    // neighbouring applets.
    readonly property real contentWidth: row.implicitWidth + Kirigami.Units.smallSpacing * 2

    Layout.minimumWidth: contentWidth
    Layout.preferredWidth: contentWidth
    implicitWidth: contentWidth
    implicitHeight: Kirigami.Units.iconSizes.small

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Kirigami.Units.largeSpacing

        Repeater {
            model: compact.providers

            delegate: RowLayout {
                id: chip
                required property var modelData
                spacing: Kirigami.Units.smallSpacing

                readonly property var w5h: Lib.primaryWindow(modelData)
                readonly property var pct: (modelData.available && w5h) ? w5h.used_percent : null
                readonly property string level: modelData.available
                    ? Lib.levelFor(pct, compact.warnThreshold, compact.critThreshold)
                    : "muted"
                // Served from a stale on-disk cache (Claude api-cached, or the
                // last-known Antigravity status while agy is closed). Dimmed so an
                // old value does not read as a fresh one.
                readonly property bool stale: String(modelData.source).indexOf("-cached") !== -1

                // Provider logo (bundled SVG) or colored-initial fallback.
                ProviderIcon {
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: Kirigami.Units.iconSizes.small * 0.9
                    implicitHeight: implicitWidth
                    providerId: chip.modelData.id
                    dimmed: !chip.modelData.available
                }

                PlasmaComponents.Label {
                    Layout.alignment: Qt.AlignVCenter
                    text: Lib.fmtPct(chip.pct)
                    color: compact.levelColor(chip.level)
                    font.bold: chip.level === "crit"
                    opacity: chip.stale ? 0.55 : 1.0
                }

                PlasmaComponents.Label {
                    Layout.alignment: Qt.AlignVCenter
                    visible: compact.showReset && chip.w5h && chip.modelData.available
                    text: chip.w5h ? Lib.fmtCountdown(chip.w5h.resets_at, compact.nowMs, i18n("now")) : ""
                    opacity: 0.7
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                }
            }
        }

        // Fallback text when there is nothing to show yet.
        PlasmaComponents.Label {
            visible: compact.providers.length === 0
            text: compact.loading ? "…" : "AI"
            opacity: 0.6
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: compact.clicked()
    }
}
