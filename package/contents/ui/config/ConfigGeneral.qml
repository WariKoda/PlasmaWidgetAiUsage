import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

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
}
