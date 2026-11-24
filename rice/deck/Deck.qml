// Rice ASCII DECK window (loaded by deck.py, which provides `backend`).
// Qt Quick, not a terminal, for two reasons: Qt shapes Arabic and lays it out right-to-left (terminals
// cannot), and the character grid is drawn by a GPU shader at exact pixel positions.
// Everything that moves is advanced by ONE function (`step`). While music plays and `rice react` is
// not off, it runs once per display frame (60 fps on a 60 Hz screen) and takes bass, beats and the
// spectrum from the shared analyser. Otherwise it runs 15 times a second, and when nothing plays and
// the picture has gone still it changes nothing at all, so the window is not repainted.
import QtQuick
import QtQuick.Window

Window {
    id: win
    title: "rice deck"
    visible: true
    width: 760; height: 720
    color: Qt.rgba(cPanel.r, cPanel.g, cPanel.b, 0.94)      // near-opaque panel colour; the KWin rule adds the frame
    // Test instances (RICE_DECK_TEST=1) never take focus and let real clicks, scrolls and touches pass
    // through, so a window opened for testing cannot catch what the person at the laptop is doing.
    flags: backend.testWindow ? (Qt.Window | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput) : Qt.Window

    readonly property real dpr: Screen.devicePixelRatio
    // colours and shapes of the flavour in use (theme.json, handed over by deck.py)
    readonly property color cPanel: theme.bg
    readonly property color cRose: theme.accent
    readonly property color cBlush: theme.hi
    readonly property color cDim: theme.dim
    readonly property color cFaint: theme.faint
    readonly property string uiFont: theme.ui_font
    readonly property bool playing: backend.status === "Playing"

    // character grid: Monocraft cells are 6 x 9 device pixels (x zoom)
    readonly property int cw: 6 * backend.zoom
    readonly property int ch: 9 * backend.zoom
    readonly property int cols: Math.max(8, Math.floor(artArea.width * dpr / cw))
    readonly property int rows: Math.max(4, Math.floor(artArea.height * dpr / ch))
    onColsChanged: gridTimer.restart()
    onRowsChanged: gridTimer.restart()
    Timer { id: gridTimer; interval: 150; onTriggered: backend.setGrid(win.cols, win.rows) }

    // animation state, advanced only by `tick`
    property real time: 0
    property real frame: 0
    property real progress: 1                // track-change animation, 1 = settled
    // from the shared analyser, fresh every frame: [amount, bass, mids, highs, loud, kick, glow, ring,
    // glitch, seed, bpm, sound, 32 bars, alive] (deck.py frame())
    property var rx: [0, 0, 0, 0, 0, 0, 0, -1, 0, 0, 0, 0]
    property bool hearing: false             // the analyser is delivering: what is audible decides, not the player's word
    readonly property bool reduced: backend.reducedMotion
    readonly property bool reactOn: backend.amount > 0 && !reduced
    property real quiet: 0                   // seconds without sound
    // With the analyser running, silence puts the art to sleep and sound wakes it at once (a player
    // reports "Paused" or "Playing" up to a second late). Without it, the player's state decides.
    readonly property bool asleep: (reactOn && hearing) ? quiet > 0.7 : !playing
    property real still: asleep ? 1 : 0      // 0 alive .. 1 the art has faded to a static dither
    property real wake: 0                    // one pulse when the music comes back (used when react is off)
    property real volT: 1                    // 0..1 while the ripple of a volume change crosses the deck
    property real volDir: 1
    readonly property real volRip: volT >= 1 ? -1 : (function () {
        var e = 0.5 - 0.5 * Math.cos(Math.PI * volT)                   // ease in and out
        return volDir > 0 ? -0.2 + 1.4 * e : 1.2 - 1.4 * e })()
    readonly property int bpm: Math.round(rx[10])
    property real pos: 0                     // playback position, interpolated between polls
    property bool hasNew: false
    property bool hasOld: false
    property real osdLeft: 0                 // seconds the volume OSD stays
    property real bannerLeft: 0
    property real hintLeft: 0
    property string bannerTitle: ""
    property string bannerArtist: ""
    property string hintText: ""
    readonly property bool animating: progress < 1 || volT < 1 || wake > 0 || (still > 0 && still < 1)
    readonly property bool busy: playing || animating || osdLeft > 0 || bannerLeft > 0 || hintLeft > 0
    // every display frame: only when something has to move smoothly
    readonly property bool fast: visible && !reduced && (animating || (reactOn && !asleep) || (reactOn && still < 1))

    function isArabic(s) { return /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/.test(s) }
    function fam(s) { return isArabic(s) ? "Noto Kufi Arabic" : win.uiFont }
    function snap(v) { return Math.round(v * dpr) / dpr }
    function clock(t) {
        t = Math.max(0, Math.floor(t))
        var s = t % 60
        return Math.floor(t / 60) + ":" + (s < 10 ? "0" + s : s)
    }
    function showBanner(t, a) { bannerTitle = t; bannerArtist = a; bannerLeft = 4.0 }
    function showHint(t) { hintText = t; hintLeft = 1.4 }

    function step(dt) {
        // nothing plays and the fade is over, or the desktop asks for no motion: hold the picture
        var frozen = win.reduced || (win.still >= 1 && !win.animating)
        if (!frozen) {
            win.time = (win.time + dt) % 3600
            win.frame = Math.floor(win.time * 15) % 4096
        }
        if (win.progress < 1) win.progress = win.reduced ? 1 : Math.min(1, win.progress + dt / 0.8)
        if (win.osdLeft > 0) win.osdLeft = Math.max(0, win.osdLeft - dt)
        if (win.bannerLeft > 0) win.bannerLeft = Math.max(0, win.bannerLeft - dt)
        if (win.hintLeft > 0) win.hintLeft = Math.max(0, win.hintLeft - dt)
        if (win.playing) win.pos = Math.min(backend.length > 0 ? backend.length : 1e9, win.pos + dt)
        if (win.volT < 1) win.volT = Math.min(1, win.volT + dt / 0.6)
        if (win.wake > 0) win.wake = Math.max(0, win.wake - dt / 0.35)
        var r = backend.frame()
        win.hearing = r[44] > 0.5
        win.quiet = r[11] > 0.5 ? 0 : win.quiet + dt
        // the fade to the still picture takes 1.6 s; coming back takes a tenth of a second
        var goal = win.asleep ? 1 : 0
        if (win.reduced) win.still = goal
        else if (win.still < goal) win.still = Math.min(1, win.still + dt / 1.6)
        else if (win.still > goal) win.still = Math.max(0, win.still - dt / 0.10)
        if (!frozen || r[11] > 0.5) win.rx = r
    }
    FrameAnimation {                                        // once per display frame
        running: win.fast
        onTriggered: win.step(Math.min(0.1, frameTime))
    }
    Timer {                                                 // the slow clock for everything else
        id: tick
        interval: win.busy || win.reactOn ? 66 : 250
        running: win.visible && !win.fast
        repeat: true
        property real last: 0
        onRunningChanged: last = 0
        onTriggered: {
            var now = Date.now()
            var dt = last > 0 ? Math.min(0.5, (now - last) / 1000) : 0.066
            last = now
            win.step(dt)
        }
    }
    onPlayingChanged: if (playing && !reactOn && !reduced) wake = 1      // back alive: one pulse

    Connections {
        target: backend
        function onStateChanged() { win.pos = backend.position }
        function onArtReady(url, isNew) {
            var lowUrl = url.replace(/\.png$/, "-low.png")
            if (isNew) {                     // new track: the old art falls apart, the new one is written line by line
                imgOld.source = imgNew.source
                lowOld.source = lowNew.source
                win.hasOld = win.hasNew
                imgNew.source = url
                lowNew.source = lowUrl
                win.hasNew = url !== ""
                win.progress = 0
            } else {                         // same track, new size / zoom / fit: just swap
                imgNew.source = url
                lowNew.source = lowUrl
                win.hasNew = url !== ""
                win.hasOld = false
                win.progress = 1
            }
        }
        function onTrackChanged() { win.showBanner(backend.title, backend.artist) }
        function onBannerShown(t, a) { win.showBanner(t, a) }
        function onVolumeShown() { win.osdLeft = 1.9 }           // 1.5 s, then a 0.4 s fade
        function onVolumeRipple(dir) {                           // a wave crosses the deck, the way the volume went
            if (win.reduced || (win.volT < 0.5 && dir === win.volDir)) return     // one is on its way already
            win.volDir = dir
            win.volT = 0
        }
        function onSettingsChanged() { win.showHint(backend.modeName + (backend.zoom === 2 ? " · big" : "") + (backend.fill ? "" : " · fit")) }
    }
    Component.onCompleted: { win.pos = backend.position; gridTimer.restart() }

    // ---------------------------------------------------------------- big block digits for the OSD
    component BigGlyph: Grid {
        property string glyph: "0"
        property real px: 10
        property color ink: win.cBlush
        readonly property var font: ({
            "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
            "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
            "8": "111101111101111", "9": "111101111001111", "%": "101001010100101" })
        columns: 3; spacing: Math.max(1, px * 0.16)
        Repeater {
            model: 15
            Rectangle { width: px; height: px; color: (font[glyph] || "")[index] === "1" ? ink : "transparent" }
        }
    }

    component DeckButton: Rectangle {
        property string label: ""
        signal tapped()
        width: 84; height: 46                // big enough for a finger
        color: ma.pressed ? Qt.rgba(win.cRose.r, win.cRose.g, win.cRose.b, 0.4) : (ma.containsMouse ? Qt.rgba(win.cRose.r, win.cRose.g, win.cRose.b, 0.18) : "transparent")
        radius: theme.radius; border.width: theme.button_border; border.color: win.cRose
        Text {
            anchors.centerIn: parent
            text: theme.brackets[0] + label + theme.brackets[1]
            font.family: win.uiFont; font.pixelSize: 18
            color: win.cBlush
        }
        MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; onClicked: parent.tapped() }
    }

    Item {
        id: root
        objectName: "deckRoot"
        anchors.fill: parent
        anchors.margins: 8 / win.dpr
        focus: true
        Keys.onPressed: (e) => {
            switch (e.key) {
            case Qt.Key_Space: backend.playPause(); break
            case Qt.Key_Left: backend.previous(); break
            case Qt.Key_Right: backend.next(); break
            case Qt.Key_Plus: case Qt.Key_Equal: backend.volumeStep(5); break
            case Qt.Key_Minus: backend.volumeStep(-5); break
            case Qt.Key_M: backend.toggleMute(); break
            case Qt.Key_Tab: case Qt.Key_A: backend.cycleMode(); break
            case Qt.Key_1: backend.setMode(0); break
            case Qt.Key_2: backend.setMode(1); break
            case Qt.Key_3: backend.setMode(2); break
            case Qt.Key_4: backend.setMode(3); break
            case Qt.Key_Z: backend.toggleZoom(); break
            case Qt.Key_F: backend.toggleFill(); break
            default: return
            }
            e.accepted = true
        }

        // ------------------------------------------------------------ the art
        Item {
            id: artArea
            anchors { left: parent.left; right: parent.right; top: parent.top; bottom: panel.top; bottomMargin: 8 / win.dpr }
            clip: true

            Image { id: imgNew; visible: false; smooth: false; mipmap: false; cache: false; asynchronous: false }
            Image { id: imgOld; visible: false; smooth: false; mipmap: false; cache: false; asynchronous: false }
            Image { id: atlasImg; visible: false; smooth: false; mipmap: false; cache: false; asynchronous: false; source: backend.atlasUrl }
            // one texel per cell; smooth, so reading between cells gives a blur
            Image { id: lowNew; visible: false; smooth: true; mipmap: false; cache: false; asynchronous: false }
            Image { id: lowOld; visible: false; smooth: true; mipmap: false; cache: false; asynchronous: false }

            ShaderEffect {
                id: fx
                width: win.cols * win.cw / win.dpr           // exactly cols x rows cells in device pixels
                height: win.rows * win.ch / win.dpr
                x: win.snap((artArea.width - width) / 2)
                y: win.snap((artArea.height - height) / 2)
                property variant artNew: imgNew
                property variant artOld: imgOld
                property variant atlas: atlasImg
                property variant lowNew: lowNew
                property variant lowOld: lowOld
                property vector2d grid: Qt.vector2d(win.cols, win.rows)
                property real nGlyphs: backend.nGlyphs
                property real mode: backend.mode
                property real time: win.time
                property real frame: win.frame
                property real progress: win.progress
                property real fx: backend.fx
                // a wake pulse still shows when react is off, at the "subtle" strength
                property real amt: win.reduced ? 0 : Math.max(win.rx[0], win.wake > 0 ? 0.45 : 0)
                property real bass: win.rx[1]
                property real loud: win.rx[4]
                property real kick: Math.max(win.rx[5], win.wake)
                property real glow: Math.max(win.rx[6], win.wake)
                property real ring: win.rx[7]
                property real glitch: win.rx[8]
                property real seed: win.rx[9]
                property real volRip: win.volRip
                property real volDir: win.volDir
                property real still: win.still * win.still * (3 - 2 * win.still)       // eased
                property real hasNew: win.hasNew ? 1 : 0
                property real hasOld: win.hasOld ? 1 : 0
                property real shimmer: win.reduced ? 0 : 1
                property color c0: theme.shader[0]
                property color c1: theme.shader[1]
                property color c2: theme.shader[2]
                property color c3: theme.shader[3]
                property real mono: theme.mono ? 1 : 0
                property real invert: theme.light ? 1 : 0
                property real scan: theme.scanlines
                fragmentShader: "ascii.frag.qsb"
            }

            // tap / click / swipe / drag / wheel on the art
            MouseArea {
                anchors.fill: parent
                property real sx: 0
                property real sy: 0
                property int axis: 0             // 0 undecided, 1 horizontal (prev/next), 2 vertical (volume)
                property int vol0: 0
                property real wheelAcc: 0
                property real wheelAt: 0
                onPressed: (m) => { sx = m.x; sy = m.y; axis = 0; vol0 = backend.volume; root.forceActiveFocus() }
                onPositionChanged: (m) => {
                    var dx = m.x - sx, dy = m.y - sy
                    if (axis === 0 && (Math.abs(dx) > 16 || Math.abs(dy) > 16))
                        axis = Math.abs(dx) > Math.abs(dy) ? 1 : 2
                    if (axis === 2) {
                        var v = Math.max(0, Math.min(100, vol0 + Math.round(-dy / 3)))   // drag up = louder
                        if (v !== backend.volume) backend.setVolume(v)
                    }
                }
                onReleased: (m) => {
                    var dx = m.x - sx
                    if (axis === 1 && Math.abs(dx) > 70) { if (dx < 0) backend.next(); else backend.previous() }
                    axis = 0
                }
                onWheel: (w) => {
                    // one notch of a mouse wheel = 2 %. Touchpads send a flood of small deltas: they are
                    // added up, but at most one 2 % step is taken every 150 ms and the rest is dropped,
                    // so a quick two-finger scroll over the panel cannot run the volume up to 100 %.
                    wheelAcc += w.angleDelta.y
                    var now = Date.now()
                    if (Math.abs(wheelAcc) >= 120 && now - wheelAt >= 150) {
                        backend.volumeStep(wheelAcc > 0 ? 2 : -2)
                        wheelAt = now
                        wheelAcc = 0
                    } else if (Math.abs(wheelAcc) > 240) {
                        wheelAcc = wheelAcc > 0 ? 240 : -240
                    }
                }
            }

            // mode / zoom hint, top left
            Rectangle {
                visible: opacity > 0
                opacity: Math.min(1, win.hintLeft / 0.4)
                x: 8; y: 8; width: hint.implicitWidth + 16; height: hint.implicitHeight + 8
                color: Qt.rgba(win.cPanel.r, win.cPanel.g, win.cPanel.b, 0.85); border.color: win.cRose; border.width: 1
                Text { id: hint; anchors.centerIn: parent; text: win.hintText; font.family: win.uiFont; font.pixelSize: 14; color: win.cBlush }
            }

            // title / artist banner on track change. Arabic is shaped and right-to-left because this is Qt text.
            Rectangle {
                visible: opacity > 0
                opacity: Math.min(1, win.bannerLeft / 0.5)
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 14 }
                height: bannerCol.implicitHeight + 20
                color: Qt.rgba(win.cPanel.r, win.cPanel.g, win.cPanel.b, 0.88); border.color: win.cRose; border.width: 1
                Column {
                    id: bannerCol
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
                    spacing: 4
                    Text {
                        width: parent.width
                        text: win.bannerTitle
                        font.family: win.fam(text); font.bold: true
                        font.pixelSize: win.isArabic(text) ? 26 : 24
                        color: win.cBlush
                        horizontalAlignment: Text.AlignHCenter
                        textFormat: Text.PlainText; elide: Text.ElideRight; maximumLineCount: 1
                    }
                    Text {
                        width: parent.width
                        visible: text.length > 0
                        text: win.bannerArtist
                        font.family: win.fam(text); font.pixelSize: win.isArabic(text) ? 17 : 15
                        color: win.cDim
                        horizontalAlignment: Text.AlignHCenter
                        textFormat: Text.PlainText; elide: Text.ElideRight; maximumLineCount: 1
                    }
                }
            }

            // volume OSD: big block digits + segmented bar
            Rectangle {
                visible: opacity > 0
                opacity: Math.min(1, win.osdLeft / 0.4)
                anchors.centerIn: parent
                width: osdCol.implicitWidth + 44; height: osdCol.implicitHeight + 36
                color: Qt.rgba(win.cPanel.r, win.cPanel.g, win.cPanel.b, 0.9); border.color: win.cRose; border.width: 2
                Column {
                    id: osdCol
                    anchors.centerIn: parent
                    spacing: 16
                    Row {
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: 12
                        Repeater {
                            model: String(backend.volume).split("")
                            BigGlyph { glyph: modelData; px: 13; ink: backend.muted ? win.cFaint : win.cBlush }
                        }
                        BigGlyph { glyph: "%"; px: 13; ink: backend.muted ? win.cFaint : win.cRose }
                    }
                    Row {
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: 4
                        Repeater {
                            model: 20
                            Rectangle {
                                width: 11; height: 20
                                color: (index < Math.round(backend.volume / 5) && !backend.muted) ? (index >= 16 ? win.cBlush : win.cRose) : "transparent"
                                border.color: win.cFaint; border.width: 1
                            }
                        }
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: backend.muted ? "MUTED" : "VOLUME"
                        font.family: win.uiFont; font.pixelSize: 14
                        color: backend.muted ? win.cRose : win.cDim
                    }
                }
            }
        }

        // ------------------------------------------------------------ controls
        Column {
            id: panel
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            spacing: 2

            Item {                               // now playing (always there; the banner is the loud version)
                width: parent.width; height: nowText.implicitHeight
                Text {
                    id: nowText
                    width: parent.width
                    text: backend.title ? (backend.artist ? backend.title + "  —  " + backend.artist : backend.title)
                                        : (backend.player ? "♪ " + backend.player + " ready" : "♪ no music player running")
                    font.family: win.fam(text); font.pixelSize: 13
                    color: win.playing ? win.cBlush : win.cDim
                    horizontalAlignment: Text.AlignHCenter
                    textFormat: Text.PlainText; elide: Text.ElideRight; maximumLineCount: 1
                }
                Text {                           // the tempo the analyser found
                    visible: win.bpm > 0 && win.reactOn && !win.asleep
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    text: win.bpm + " bpm"
                    font.family: win.uiFont; font.pixelSize: 11
                    color: win.cDim
                }
            }

            Item {
                width: parent.width; height: 46
                DeckButton {                     // art mode (same as Tab / A)
                    anchors.left: parent.left
                    width: Math.min(150, parent.width * 0.26)
                    label: backend.modeName
                    onTapped: backend.cycleMode()
                }
                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    DeckButton { label: "⏮"; onTapped: backend.previous() }
                    DeckButton { label: "⏯"; onTapped: backend.playPause() }
                    DeckButton { label: "⏭"; onTapped: backend.next() }
                }
                DeckButton {                     // volume read-out; tap = mute
                    anchors.right: parent.right
                    width: Math.min(150, parent.width * 0.26)
                    label: backend.muted ? "mute" : backend.volume + "%"
                    onTapped: backend.toggleMute()
                }
            }

            Item {                               // progress bar: click or tap to seek
                id: seekRow
                width: parent.width; height: 34
                // some players briefly report the old track's position with the new track's length
                readonly property int whole: Math.floor(backend.length > 0 ? Math.min(win.pos, backend.length) : win.pos)
                readonly property real frac: backend.length > 0 ? Math.max(0, Math.min(1, whole / backend.length)) : 0
                Text { id: tL; anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                       text: win.clock(seekRow.whole); font.family: win.uiFont; font.pixelSize: 13; color: win.cDim }
                Text { id: tR; anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                       text: backend.length > 0 ? win.clock(backend.length) : "-:--"; font.family: win.uiFont; font.pixelSize: 13; color: win.cDim }
                Item {
                    id: bar
                    anchors { left: tL.right; right: tR.left; leftMargin: 10; rightMargin: 10; top: parent.top; bottom: parent.bottom }
                    // drawn with rectangles, not glyphs: long runs of "=" and "-" did not render reliably
                    // with native text at a fractional display scale
                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 2
                                color: backend.canSeek ? win.cFaint : Qt.rgba(win.cFaint.r, win.cFaint.g, win.cFaint.b, 0.45) }
                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: Math.round(parent.width * seekRow.frac); height: 6
                                color: win.cRose }
                    Rectangle { readonly property bool dot: theme.knob === "dot"       // the handle: a bar or a round dot
                                anchors.verticalCenter: parent.verticalCenter; x: Math.round(parent.width * seekRow.frac) - width / 2
                                width: dot ? 14 : 8; height: dot ? 14 : 16; radius: dot ? 7 : 0; color: win.cBlush }
                    MouseArea {
                        anchors.fill: parent
                        enabled: backend.canSeek
                        onPressed: (m) => { root.forceActiveFocus(); backend.seek(m.x / width) }
                    }
                }
            }

            ShaderEffect {                       // spectrum strip (what the cava terminal used to show)
                width: parent.width
                height: Math.max(40, Math.min(84, win.height * 0.10))
                readonly property var v: win.rx
                function q(i) { i += 12; return v.length > i + 3 ? Qt.vector4d(v[i], v[i + 1], v[i + 2], v[i + 3]) : Qt.vector4d(0, 0, 0, 0) }
                property vector4d b0: q(0)
                property vector4d b1: q(4)
                property vector4d b2: q(8)
                property vector4d b3: q(12)
                property vector4d b4: q(16)
                property vector4d b5: q(20)
                property vector4d b6: q(24)
                property vector4d b7: q(28)
                property vector2d cells: Qt.vector2d(32, 7)
                property real beat: win.rx[5] * win.rx[0]
                property real volRip: win.volRip
                property real fade: 1 - 0.75 * win.still
                property color c1: theme.shader[1]
                property color c2: theme.shader[2]
                property color c3: theme.shader[3]
                property real style: theme.bars
                property vector2d size: Qt.vector2d(width * win.dpr, height * win.dpr)
                fragmentShader: "spectrum.frag.qsb"
            }
        }
    }
}
