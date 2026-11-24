// Rice flavour picker (loaded by picker.py, which provides `backend`).
// One card per flavour, drawn in that flavour's own colours around a small screenshot of it.
// The window takes the colours of the flavour in use and fades to the next one when you switch.
// Cards are sized for fingers: the picker is meant to work in tablet mode too.
import QtQuick
import QtQuick.Window

Window {
    id: win
    title: "rice flavour"
    visible: true
    readonly property var cards: backend.flavours
    readonly property var cur: {
        for (var i = 0; i < cards.length; i++) if (cards[i].name === backend.current) return cards[i]
        return cards[0]
    }
    readonly property int cols: Screen.width >= 1300 ? cards.length : (Screen.width >= 1000 ? 3 : 2)
    readonly property int rowsN: Math.ceil(cards.length / cols)
    readonly property int gap: 18
    readonly property int cardW: Math.min(300, Math.floor((Screen.width - 120 - gap * (cols - 1)) / cols))
    readonly property int cardH: Math.round(cardW * 9 / 16) + 92
    width: cols * cardW + (cols - 1) * gap + 56
    height: rowsN * cardH + (rowsN - 1) * gap + (cols < cards.length ? 172 : 150)
    minimumWidth: width; minimumHeight: height

    property color cBg: cur.bg
    property color cFg: cur.fg
    property color cDim: cur.dim
    property color cAccent: cur.accent
    Behavior on cBg { ColorAnimation { duration: 420; easing.type: Easing.InOutQuad } }
    Behavior on cFg { ColorAnimation { duration: 420 } }
    Behavior on cDim { ColorAnimation { duration: 420 } }
    Behavior on cAccent { ColorAnimation { duration: 420 } }
    color: cBg

    property int focusIndex: {
        for (var i = 0; i < cards.length; i++) if (cards[i].name === backend.current) return i
        return 0
    }
    function choose(i) { focusIndex = i; backend.apply(cards[i].name) }

    Item {
        anchors.fill: parent
        focus: true
        Keys.onPressed: (e) => {
            var n = win.cards.length
            if (e.key >= Qt.Key_1 && e.key < Qt.Key_1 + n) win.choose(e.key - Qt.Key_1)
            else if (e.key === Qt.Key_Right || e.key === Qt.Key_Tab) win.focusIndex = (win.focusIndex + 1) % n
            else if (e.key === Qt.Key_Left || e.key === Qt.Key_Backtab) win.focusIndex = (win.focusIndex + n - 1) % n
            else if (e.key === Qt.Key_Down) win.focusIndex = Math.min(n - 1, win.focusIndex + win.cols)
            else if (e.key === Qt.Key_Up) win.focusIndex = Math.max(0, win.focusIndex - win.cols)
            else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) win.choose(win.focusIndex)
            else if (e.key === Qt.Key_Escape || e.key === Qt.Key_Q) Qt.quit()
            else return
            e.accepted = true
        }

        Text {
            id: heading
            x: 28; y: 22
            text: "flavour"
            font.family: win.cur.font; font.pixelSize: 26; font.bold: true; font.capitalization: Font.AllUppercase
            color: win.cFg
        }
        Text {
            anchors { left: heading.right; leftMargin: 16; baseline: heading.baseline }
            text: backend.riceOn ? "tap one to switch" : "the rice is off: your choice is used the next time it turns on"
            font.family: win.cur.font; font.pixelSize: 14
            color: win.cDim
        }
        Rectangle {                              // close: there is no title bar while the rice is on
            anchors { right: parent.right; top: parent.top; margins: 12 }
            width: 48; height: 48; radius: win.cur.radius
            color: closeArea.pressed ? Qt.rgba(win.cAccent.r, win.cAccent.g, win.cAccent.b, 0.35) : "transparent"
            border.width: 1; border.color: win.cDim
            Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 20; color: win.cFg }
            MouseArea { id: closeArea; anchors.fill: parent; onClicked: Qt.quit() }
        }

        Grid {
            x: 28; y: 78
            columns: win.cols; spacing: win.gap
            Repeater {
                model: win.cards
                Rectangle {
                    id: cardBox
                    required property var modelData
                    required property int index
                    readonly property bool inUse: modelData.name === backend.current
                    readonly property bool focused: index === win.focusIndex
                    width: win.cardW; height: win.cardH
                    radius: modelData.radius
                    color: modelData.window
                    border.width: inUse ? 3 : (focused ? 2 : 1)
                    border.color: inUse ? modelData.accent : (focused ? modelData.dim : modelData.faint)
                    scale: area.pressed ? 0.97 : (inUse ? 1.0 : (area.containsMouse ? 1.0 : 0.975))
                    opacity: inUse || area.containsMouse || focused ? 1.0 : 0.86
                    Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutQuad } }
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                    Behavior on border.color { ColorAnimation { duration: 200 } }

                    Image {
                        id: shot
                        x: 6; y: 6; width: parent.width - 12; height: Math.round(width * 9 / 16)
                        source: cardBox.modelData.preview
                        fillMode: Image.PreserveAspectCrop; smooth: true; mipmap: true; asynchronous: true
                    }
                    Rectangle {                  // no screenshot shipped: the flavour's colours as bands
                        visible: shot.status !== Image.Ready
                        anchors.fill: shot; color: cardBox.modelData.bg
                        Row {
                            anchors.centerIn: parent; spacing: 6
                            Repeater {
                                model: [cardBox.modelData.accent, cardBox.modelData.accent2, cardBox.modelData.fg, cardBox.modelData.dim]
                                Rectangle { width: 26; height: 60; color: modelData }
                            }
                        }
                    }
                    Text {
                        x: 14; y: shot.y + shot.height + 10
                        text: (cardBox.index + 1) + "  " + cardBox.modelData.title
                        font.family: cardBox.modelData.font; font.pixelSize: 20; font.bold: true
                        color: cardBox.modelData.hi
                    }
                    Text {
                        anchors { right: parent.right; rightMargin: 14 }
                        y: shot.y + shot.height + 15
                        visible: cardBox.inUse
                        text: backend.busy ? "switching" : "in use"
                        font.family: cardBox.modelData.font; font.pixelSize: 13
                        color: cardBox.modelData.accent
                    }
                    Text {
                        x: 14; y: shot.y + shot.height + 42; width: parent.width - 28
                        text: cardBox.modelData.blurb
                        font.family: cardBox.modelData.font; font.pixelSize: 13
                        color: cardBox.modelData.dim
                        wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    MouseArea {
                        id: area
                        anchors.fill: parent; hoverEnabled: true
                        onClicked: win.choose(cardBox.index)
                    }
                }
            }
        }

        Text {
            anchors { left: parent.left; leftMargin: 28; right: parent.right; rightMargin: 28; bottom: parent.bottom; bottomMargin: 16 }
            wrapMode: Text.WordWrap; maximumLineCount: 2
            text: "1-" + win.cards.length + " switch    arrows + enter    esc close    Meta+Alt+T cycles without the picker"
            font.family: win.cur.font; font.pixelSize: 13
            color: win.cDim
        }
    }
}
