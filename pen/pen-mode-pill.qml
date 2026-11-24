// A small always-on-top pill that shows DRAW or NAVIGATE while a drawing app is open; one tap switches.
// It is a layer-shell surface (top centre, above windows) and never takes keyboard focus.
// Loaded by pen-mode-tray.py; if the layer-shell module is missing the tray icon simply runs without it.
import QtQuick
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell

Window {
    id: pill
    width: label.implicitWidth + 58
    height: 44
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    visible: bridge.shown

    LayerShell.Window.scope: "pen-mode"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.activateOnShow: false

    Rectangle {
        anchors.fill: parent
        anchors.margins: 3
        radius: height / 2
        color: bridge.mode === "draw" ? bridge.drawColor : "#6b7280"
        opacity: tap.pressed ? 1.0 : 0.9
        border.color: "#40ffffff"
        border.width: 1

        Rectangle {            // state dot: filled for DRAW, ring for NAVIGATE
            x: 14; width: 14; height: 14; radius: 7
            anchors.verticalCenter: parent.verticalCenter
            color: bridge.mode === "draw" ? "white" : "transparent"
            border.color: "white"; border.width: 2
        }
        Text {
            id: label
            x: 36
            anchors.verticalCenter: parent.verticalCenter
            text: bridge.mode === "draw" ? "DRAW" : "NAVIGATE"
            color: "white"
            font.bold: true
            font.pixelSize: 15
            font.letterSpacing: 1
        }
        TapHandler { id: tap; onTapped: bridge.toggle() }
    }
}
