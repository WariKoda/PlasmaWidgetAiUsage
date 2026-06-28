pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami
import "lib.js" as Lib

PlasmaComponents.Page {
    id: full

    property var report: null
    property bool loading: false
    property string lastError: ""
    property int warnThreshold: 75
    property int critThreshold: 90

    signal refreshRequested()

    Layout.minimumWidth: Kirigami.Units.gridUnit * 16
    Layout.minimumHeight: Kirigami.Units.gridUnit * 14
    Layout.preferredWidth: Kirigami.Units.gridUnit * 18
    Layout.preferredHeight: Kirigami.Units.gridUnit * 18

    readonly property real usedColWidth: Kirigami.Units.gridUnit * 8
    readonly property real resetColWidth: Kirigami.Units.gridUnit * 4

    property double nowMs: Date.now()
    Timer {
        interval: 30000; running: true; repeat: true
        onTriggered: full.nowMs = Date.now()
    }

    readonly property var providers: (report && report.providers) ? report.providers : []

    function levelColor(level) {
        if (level === "crit") return Kirigami.Theme.negativeTextColor;
        if (level === "warn") return Kirigami.Theme.neutralTextColor;
        if (level === "ok")   return Kirigami.Theme.positiveTextColor;
        return Kirigami.Theme.disabledTextColor;
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.largeSpacing

        PlasmaExtras.Heading {
            level: 3
            text: i18n("AI Usage")
            Layout.fillWidth: true
        }

        // -- Per-provider sections -------------------------------------------
        Repeater {
            model: full.providers

            delegate: ColumnLayout {
                id: section
                required property var modelData
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                // Provider header: dot + name + plan + data source
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    ProviderIcon {
                        implicitWidth: Kirigami.Units.iconSizes.smallMedium
                        implicitHeight: implicitWidth
                        providerId: section.modelData.id
                        dimmed: !section.modelData.available
                        Layout.alignment: Qt.AlignVCenter
                    }
                    PlasmaExtras.Heading {
                        level: 5
                        text: section.modelData.label
                    }
                    PlasmaComponents.Label {
                        visible: !!section.modelData.plan
                        text: section.modelData.plan ? "· " + section.modelData.plan : ""
                        opacity: 0.7
                    }
                    Item { Layout.fillWidth: true }
                    PlasmaComponents.Label {
                        visible: !!section.modelData.source
                        text: section.modelData.source ? section.modelData.source : ""
                        opacity: 0.5
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                    }
                }

                // Table header: Window | Used | Reset
                RowLayout {
                    visible: section.modelData.available && section.modelData.windows.length > 0
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.iconSizes.small
                    PlasmaComponents.Label {
                        text: i18n("Window"); opacity: 0.6; font.bold: true
                        Layout.fillWidth: true
                    }
                    PlasmaComponents.Label {
                        text: i18n("Used"); opacity: 0.6; font.bold: true
                        Layout.preferredWidth: full.usedColWidth
                        horizontalAlignment: Text.AlignRight
                    }
                    PlasmaComponents.Label {
                        text: i18n("Reset"); opacity: 0.6; font.bold: true
                        Layout.preferredWidth: full.resetColWidth
                        horizontalAlignment: Text.AlignRight
                    }
                }

                // Table rows
                Repeater {
                    model: section.modelData.available ? section.modelData.windows : []
                    delegate: RowLayout {
                        id: wrow
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.leftMargin: Kirigami.Units.iconSizes.small

                        readonly property bool hasPct: modelData.used_percent !== null
                                                       && modelData.used_percent !== undefined
                        readonly property bool detailOnlyWindow: !modelData.resets_at
                                                               && !!modelData.detail

                        PlasmaComponents.Label {
                            text: wrow.modelData.label
                            Layout.fillWidth: true
                        }
                        ColumnLayout {
                            Layout.preferredWidth: wrow.detailOnlyWindow
                                ? full.usedColWidth + full.resetColWidth + Kirigami.Units.smallSpacing
                                : full.usedColWidth
                            spacing: 0

                            PlasmaComponents.Label {
                                text: wrow.detailOnlyWindow
                                    ? Lib.fmtUsage(wrow.modelData)
                                    : (wrow.hasPct
                                    ? Lib.fmtPct(wrow.modelData.used_percent)
                                    : (wrow.modelData.detail ? wrow.modelData.detail : "—"))
                                color: full.levelColor(Lib.levelFor(wrow.modelData.used_percent,
                                                                    full.warnThreshold, full.critThreshold))
                                font.bold: wrow.hasPct && wrow.modelData.used_percent >= full.critThreshold
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignRight
                                elide: Text.ElideRight
                            }
                            PlasmaComponents.Label {
                                visible: !wrow.detailOnlyWindow && wrow.hasPct && !!wrow.modelData.detail
                                text: wrow.modelData.detail ? wrow.modelData.detail : ""
                                opacity: 0.75
                                font.pointSize: Kirigami.Theme.smallFont.pointSize
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignRight
                                elide: Text.ElideRight
                            }
                        }
                        PlasmaComponents.Label {
                            visible: !wrow.detailOnlyWindow
                            text: Lib.fmtCountdown(wrow.modelData.resets_at, full.nowMs, i18n("now"))
                            opacity: 0.8
                            Layout.preferredWidth: full.resetColWidth
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                }

                // Unavailable reason, or a staleness note on an available but
                // cache-served provider (source == "api-cached" carries its
                // "cached usage (Xs old) …" note in the error field).
                PlasmaComponents.Label {
                    visible: !section.modelData.available || !!section.modelData.error
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.iconSizes.small
                    text: section.modelData.error ? section.modelData.error : i18n("unavailable")
                    wrapMode: Text.WordWrap
                    opacity: 0.6
                    font.italic: true
                }
            }
        }

        Item { Layout.fillHeight: true }

        Kirigami.Separator { Layout.fillWidth: true }

        RowLayout {
            Layout.fillWidth: true
            PlasmaComponents.Label {
                text: full.lastError.length > 0
                    ? full.lastError
                    : (full.loading ? i18n("Refreshing…") : "")
                color: full.lastError.length > 0 ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                opacity: full.lastError.length > 0 ? 1.0 : 0.7
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                font.pointSize: Kirigami.Theme.smallFont.pointSize
            }
            PlasmaComponents.Button {
                text: i18n("Refresh")
                icon.name: "view-refresh"
                enabled: !full.loading
                onClicked: full.refreshRequested()
            }
        }
    }
}
