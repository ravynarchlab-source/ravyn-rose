// Rice lyrics window (loaded by lyricswin.py, which provides `backend` and `theme`).
// Qt shapes Arabic and lays it out right-to-left; the flavour's font for Latin, Noto Kufi Arabic for Arabic.
//
// With synced lyrics the line is shown word by word: the word being sung is the bright one, and it
// lights up on the beat (the same beat clock the deck uses). A new line slides in from below while
// the old one slides out. With `rice react off` or reduced motion the line is simply shown and swapped.
import QtQuick
import QtQuick.Window
import QtQuick.Effects

Window {
    id: win
    title: "rice-lyrics"
    visible: true
    width: 1200; height: 110
    readonly property var th: theme
    property color cBg: th.bg                         // not constants: they fade over when the flavour is switched
    property color cHi: th.hi
    property color cDim: th.dim
    property color cAccent: th.accent
    readonly property int fade: backend.reducedMotion ? 0 : 420
    Behavior on cBg { ColorAnimation { duration: win.fade } }
    Behavior on cHi { ColorAnimation { duration: win.fade } }
    Behavior on cDim { ColorAnimation { duration: win.fade } }
    Behavior on cAccent { ColorAnimation { duration: win.fade } }
    color: Qt.rgba(cBg.r, cBg.g, cBg.b, 0.72)     // translucent panel; the KWin border rule adds the frame

    readonly property var st: backend.state
    readonly property bool playing: st.status === "Playing"
    readonly property bool reduced: backend.reducedMotion
    readonly property bool reactOn: backend.amount > 0 && !reduced
    readonly property bool timed: st.t0 >= 0          // a real lyric line with a start time
    property real kick: 0
    property int beats: 0
    property int word: -1                             // index of the word being sung (-1: none)

    function mix(a, b, t) { return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, 1) }
    function split(s) { return s.split(/\s+/).filter(function (w) { return w.length > 0 }) }
    // which word a position falls on: the line's time is shared out by word length
    function wordAt(pos) {
        var words = cur.words
        if (!win.timed || words.length === 0) return -1
        var end = st.t1 > st.t0 ? Math.min(st.t1, st.t0 + 9) : st.t0 + 4
        var frac = (pos - st.t0) / Math.max(0.4, (end - st.t0) * 0.92)      // singers finish a little before the next line
        if (frac >= 1) return words.length - 1
        var total = 0, i
        for (i = 0; i < words.length; i++) total += words[i].length + 1.5
        var acc = 0
        for (i = 0; i < words.length; i++) {
            acc += words[i].length + 1.5
            if (frac * total < acc) return i
        }
        return words.length - 1
    }

    function step() {
        var r = backend.frame()
        win.kick = r[0] * backend.amount
        var want = wordAt(r[3])
        if (r[1] !== win.beats) {                     // a beat: the highlight moves ON the beat
            win.beats = r[1]
            win.word = want
        } else if (want > win.word + 1 || want < win.word || r[2] < 0.5) {
            win.word = want                           // the beat is too slow for these words (or there is none): keep up
        }
    }
    // While a pulse fades the window follows the display (60 fps); between pulses a plain timer asks
    // for the next beat and nothing is repainted.
    readonly property bool listening: visible && playing && reactOn && timed
    FrameAnimation { running: win.listening && win.kick > 0; onTriggered: win.step() }
    Timer { interval: 12; repeat: true; running: win.listening && win.kick <= 0; onTriggered: win.step() }
    Timer {                                           // without the beat clock: still follow the words, calmly
        interval: 200; repeat: true
        running: win.visible && win.playing && win.timed && !win.reactOn
        onTriggered: { win.kick = 0; win.word = win.wordAt(backend.position()) }
    }

    // ---------------------------------------------------------------- a line, word by word
    component Line: Item {
        id: line
        property string text: ""
        property bool rtl: false
        property bool active: false                   // the line being sung (the one sliding out is not)
        readonly property var words: win.split(win.th.lyrics_upper ? text.toUpperCase() : text)
        readonly property real px: rtl ? height * 0.58 : height * 0.62
        width: parent.width; height: parent.height
        Row {
            id: row
            readonly property real fit: Math.min(1, line.width / Math.max(1, implicitWidth))
            readonly property bool alignLeft: win.th.lyrics_align === "left" && !line.rtl
            anchors.verticalCenter: parent.verticalCenter
            x: alignLeft ? 0 : (line.width - implicitWidth * fit) / 2
            transformOrigin: Item.Left
            scale: fit                                // a long line shrinks to fit instead of being cut
            layoutDirection: line.rtl ? Qt.RightToLeft : Qt.LeftToRight
            spacing: line.px * 0.42
            Repeater {
                model: line.words
                Text {
                    required property int index
                    required property string modelData
                    readonly property bool now: line.active && win.timed && index === win.word
                    readonly property bool sung: !line.active || !win.timed || win.word < 0 || index < win.word
                    text: modelData
                    textFormat: Text.PlainText
                    font.family: line.rtl ? "Noto Kufi Arabic" : win.th.ui_font
                    font.bold: true
                    font.pixelSize: line.px
                    // sung words keep the flavour's highlight colour, words to come are quieter,
                    // the word being sung is the brightest and flashes with the beat
                    color: !win.playing ? win.cDim
                         : now ? win.mix(win.cHi, win.th.light ? win.cAccent : Qt.rgba(1, 1, 1, 1), 0.25 + 0.75 * win.kick)
                         : sung ? win.cHi : win.mix(win.cDim, win.cHi, 0.35)
                    scale: now ? 1 + 0.07 * win.kick : 1
                    Behavior on color { enabled: !win.reduced && !now; ColorAnimation { duration: 180 } }
                }
            }
        }
    }

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
        Item {
            id: stage
            width: parent.width
            height: win.height * 0.58
            clip: true
            Line { id: old; opacity: 0 }
            Line { id: cur; active: true; text: "♪" }
            ParallelAnimation {               // the old line leaves upward, then the next one slides in from below
                id: slide
                NumberAnimation { target: old; property: "y"; from: 0; to: -stage.height * 0.6; duration: 200; easing.type: Easing.OutQuad }
                NumberAnimation { target: old; property: "opacity"; from: 1; to: 0; duration: 160; easing.type: Easing.OutQuad }
                SequentialAnimation {
                    PropertyAction { target: cur; property: "opacity"; value: 0 }
                    PauseAnimation { duration: 70 }
                    ParallelAnimation {
                        NumberAnimation { target: cur; property: "y"; from: stage.height * 0.55; to: 0; duration: 300; easing.type: Easing.OutCubic }
                        NumberAnimation { target: cur; property: "opacity"; from: 0; to: 1; duration: 240; easing.type: Easing.OutQuad }
                    }
                }
            }
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

    function show(text, rtl) {
        if (text === cur.text) return
        slide.stop()
        old.text = cur.text; old.rtl = cur.rtl
        cur.text = text; cur.rtl = rtl
        win.word = win.timed ? win.wordAt(backend.position()) : -1
        if (win.reduced) { old.opacity = 0; cur.opacity = 1; cur.y = 0 }
        else slide.start()
    }
    Connections { target: backend; function onStateChanged() { win.show(win.st.line, win.st.rtl === true) } }
    Component.onCompleted: { cur.text = st.line; cur.rtl = st.rtl === true }
}
