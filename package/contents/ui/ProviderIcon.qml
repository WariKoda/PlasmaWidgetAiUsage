import QtQuick
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import "lib.js" as Lib

// Shows the provider's bundled logo from contents/icons/<id>.svg when present,
// otherwise falls back to a colored ring with the provider's initial. Drop an
// official logo file there to use it -- no code change needed.
Item {
    id: pic

    property string providerId: ""
    property bool dimmed: false

    Image {
        id: logo
        anchors.fill: parent
        source: pic.providerId.length > 0
            ? Qt.resolvedUrl("../icons/" + pic.providerId + ".svg")
            : ""
        sourceSize.width: width
        sourceSize.height: height
        fillMode: Image.PreserveAspectFit
        smooth: true
        asynchronous: true
        visible: status === Image.Ready
        opacity: pic.dimmed ? 0.5 : 1.0
    }

    // Fallback when no logo file is bundled (status Error/Null).
    Rectangle {
        anchors.fill: parent
        visible: logo.status !== Image.Ready
        radius: width / 2
        color: "transparent"
        border.width: Math.max(1, Math.round(width * 0.1))
        border.color: pic.dimmed
            ? Kirigami.Theme.disabledTextColor
            : Lib.providerColor(pic.providerId)

        PlasmaComponents.Label {
            anchors.centerIn: parent
            text: Lib.providerInitial(pic.providerId)
            font.bold: true
            font.pixelSize: Math.round(parent.height * 0.55)
            color: parent.border.color
        }
    }
}
