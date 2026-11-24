// Rice lyrics window (run by `rice on` with: qml6 lyrics.qml -- <theme.json> <now.json>).
// Qt shapes Arabic and lays it out right-to-left; the flavour's font for Latin, Noto Kufi Arabic for Arabic.
import QtQuick
import QtQuick.Window
import QtQuick.Effects

Window {
    id: win
    title: "rice-lyrics"
    visible: true
    width: 1200; height: 110
    // colours and shapes of the flavour in use; the values here only apply until theme.json is read
    property var th: ({ bg: "#14070b", accent: "#e0607e", hi: "#ffb3c6", dim: "#9c6b78", ui_font: "Monocraft",
                        lyrics_align: "center", lyrics_upper: false, radius: 0 })
    readonly property color cBg: th.bg
    color: Qt.rgba(cBg.r, cBg.g, cBg.b, 0.72)     // translucent panel; the KWin border rule adds the frame
    property string statePath: Qt.application.arguments[Qt.application.arguments.length - 1]
    property string themePath: Qt.application.arguments[Qt.application.arguments.length - 2]
    Component.onCompleted: {
        var x = new XMLHttpRequest()
        x.open("GET", "file://" + themePath, false)
        try { x.send(); win.th = JSON.parse(x.responseText) } catch (e) {}
    }
    property var st: ({ line: "♪", sub: "", art: "", rtl: false, status: "", seq: -1 })
    readonly property bool playing: st.status === "Playing"

    function load() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE || !x.responseText) return
            try { var o = JSON.parse(x.responseText); if (o.seq !== win.st.seq) win.st = o } catch (e) {}
        }
        x.open("GET", "file://" + statePath)
        x.send()
    }
    Timer { interval: 150; running: true; repeat: true; onTriggered: win.load() }

    Rectangle {                               // album art of the player that is playing
        id: artBox
        visible: art.status === Image.Ready
        anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
        width: visible ? parent.height - 20 : 0; height: width
        color: "transparent"; border.color: win.th.accent; border.width: 1; radius: win.th.radius > 8 ? 8 : win.th.radius
        Image { id: art; anchors.fill: parent; anchors.margins: 1; source: win.st.art || ""
                visible: !win.th.mono
                fillMode: Image.PreserveAspectCrop; asynchronous: true; cache: false; sourceSize.width: 256 }
        MultiEffect { anchors.fill: art; source: art; saturation: -1.0; visible: win.th.mono === true }   // MONO: cover in grey
    }

    Column {
        anchors { left: artBox.right; right: parent.right; leftMargin: 14; rightMargin: 14
                  verticalCenter: parent.verticalCenter }
        spacing: 2
        Text {
            id: lineText
            width: parent.width
            height: win.height * 0.58
            text: win.th.lyrics_upper ? win.st.line.toUpperCase() : win.st.line
            color: win.playing ? win.th.hi : win.th.dim
            font.family: win.st.rtl ? "Noto Kufi Arabic" : win.th.ui_font
            font.bold: true
            font.pixelSize: win.st.rtl ? win.height * 0.34 : win.height * 0.36
            fontSizeMode: Text.Fit; minimumPixelSize: 12
            horizontalAlignment: (win.th.lyrics_align === "left" && !win.st.rtl) ? Text.AlignLeft : Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            textFormat: Text.PlainText
            elide: Text.ElideRight; maximumLineCount: 1
        }
        Text {
            width: parent.width
            text: win.st.sub
            visible: text.length > 0
            color: win.th.dim
            font.family: /[؀-ۿ]/.test(text) ? "Noto Kufi Arabic" : win.th.ui_font
            font.pixelSize: Math.max(11, win.height * 0.13)
            horizontalAlignment: (win.th.lyrics_align === "left") ? Text.AlignLeft : Text.AlignHCenter
            textFormat: Text.PlainText
            elide: Text.ElideRight
        }
    }
}
