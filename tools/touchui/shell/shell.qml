// s22-touch: big-tile touch shell for the Galaxy S22 native-Linux phone.
// Runs as its own Quickshell instance next to the Omarchy shell (software
// Qt Quick rendering, no blur, no animations). Started and supervised by
// s22-touchd on the native root. IPC target "touch" (see README.md):
//   quickshell ipc -p /opt/s22-touch/shell.qml call touch home
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import QtQuick

ShellRoot {
  id: root

  // ------------------------------------------------------------ state
  property bool homeVisible: false
  property string page: ""             // "" = tile grid
  property bool locked: false
  property var cards: []               // [{id, title, body, until}]
  property int cardSeq: 0
  property var confirmReq: null        // {id, question, until}
  property var perfReq: null
  property var st: ({ model: "…", healthy: null, sessions: "…", running: 0, battery: "…", batteryState: "", batteryTemp: "", thermal: "…", thermalMax: 0 })
  readonly property string runDir: "/run/s22-touch"
  // sway (opt-in sway-pixman desktop) does not give an OnDemand layer keyboard
  // focus on a touch tap, so the chat page takes Exclusive focus there.
  readonly property bool onSway: (Quickshell.env("SWAYSOCK") || "") !== ""
  readonly property string api: "http://127.0.0.1:18880"
  readonly property string phoned: "http://127.0.0.1:8095"
  readonly property color bg: "#0f1216"
  readonly property color fg: "#e8edf2"
  readonly property color dim: "#8b98a7"
  readonly property color accent: "#4fa3ff"

  readonly property var apps: ({
    chat: "page", phone: "page", camera: "page", files: "page", settings: "page", switcher: "page",
    terminal: "app"
  })

  // ------------------------------------------------------------ repaint kick
  // Qt's software scene graph never paints the first frame of a layer surface
  // that is mapped after start-up on this stack (the buffer stays black apart
  // from items that change later; reproduced with a 10-line test shell). A
  // one-step change of the window colour dirties the whole window, so every
  // window flips `kick` shortly after it becomes visible.
  property bool kick: false
  property int kicksLeft: 0
  function kickSoon() { kicksLeft = 5; kickT.restart() }
  function k(c) {
    var q = Qt.lighter(c, 1.0)
    return kick ? Qt.rgba(q.r, q.g, q.b < 0.5 ? q.b + 0.005 : q.b - 0.005, q.a) : q
  }
  Timer {
    id: kickT; interval: 700; repeat: true
    onTriggered: { root.kick = !root.kick; root.kicksLeft -= 1; if (root.kicksLeft <= 0) stop() }
  }
  onPageChanged: kickSoon()

  // ------------------------------------------------------------ helpers
  function http(method, url, body, cb, timeoutMs) {
    var x = new XMLHttpRequest()
    x.onreadystatechange = function () {
      if (x.readyState !== XMLHttpRequest.DONE) return
      var j = null
      try { j = JSON.parse(x.responseText) } catch (e) { }
      if (cb) cb(x.status, j, x.responseText)
    }
    x.open(method, url)
    if (body !== null && body !== undefined) x.setRequestHeader("Content-Type", "application/json")
    try { x.timeout = timeoutMs || 10000 } catch (e) { }
    x.send(body !== null && body !== undefined ? JSON.stringify(body) : null)
  }

  function sh(argv) { Quickshell.execDetached(argv) }

  function keyboard(on) {
    sh(["gdbus", "call", "--session", "--dest", "sm.puri.OSK0", "--object-path", "/sm/puri/OSK0",
        "--method", "sm.puri.OSK0.SetVisible", on ? "true" : "false"])
  }

  function showHome(p) {
    if (locked) return
    page = p || ""
    homeVisible = true
    if (page !== "chat") keyboard(false)
    refreshStatus()
  }

  function hideHome() { homeVisible = false; page = "" }

  // Windows: Hyprland IPC under Hyprland; under sway the compositor-neutral
  // wlr-foreign-toplevel list (Quickshell.Wayland ToplevelManager).
  function windows() {
    var out = []
    if (onSway) {
      var wl = ToplevelManager.toplevels.values
      for (var j = 0; j < wl.length; j++)
        out.push({ t: wl[j], cls: wl[j].appId || "?", title: wl[j].title || "", ws: "" })
      return out
    }
    Hyprland.refreshToplevels()
    var tl = Hyprland.toplevels.values
    for (var i = 0; i < tl.length; i++) {
      var o = tl[i].lastIpcObject || {}
      out.push({ t: tl[i], cls: o["class"] || "?", initialClass: o.initialClass, title: tl[i].title || o.title || "",
                 ws: o.workspace ? o.workspace.name : "" })
    }
    return out
  }

  function findToplevel(cls) {
    var w = windows()
    for (var i = 0; i < w.length; i++)
      if (w[i].cls === cls || w[i].initialClass === cls) return w[i].t
    return null
  }

  function focusToplevel(t) {
    if (onSway) { t.activate(); return }
    var a = String(t.address || "")
    if (a.indexOf("0x") !== 0) a = "0x" + a
    Hyprland.dispatch('hl.dsp.focus({ window = "address:' + a + '" })')
  }

  function closeToplevel(t) {
    if (onSway) { t.close(); return }
    focusToplevel(t)
    Hyprland.dispatch("hl.dsp.window.close()")
  }

  function launchOrFocus(cls, argv) {
    var t = findToplevel(cls)
    if (t) focusToplevel(t)
    else sh(argv)
  }

  function openApp(app) {
    if (locked) return "locked"
    if (!(app in apps)) return "unknown app: " + app + " (known: " + Object.keys(apps).join(", ") + ")"
    if (apps[app] === "page") { showHome(app); return "ok" }
    hideHome()
    if (app === "terminal") launchOrFocus("s22.terminal", ["foot", "--app-id=s22.terminal"])
    kbT.restart()   // terminal focus can hide an immediate reveal: show the keyboard after it settles
    return "ok"
  }

  function back() {
    if (locked || confirmReq) return "ignored"
    if (homeVisible && page !== "") { page = ""; return "grid" }
    if (homeVisible) { hideHome(); return "hidden" }
    showHome(""); return "home"
  }

  function addCard(title, body, ttl) {
    var t = Math.max(3, Math.min(120, Number(ttl) || 8))
    cardSeq += 1
    var c = cards.slice(-2)
    c.push({ id: cardSeq, title: String(title).slice(0, 80), body: String(body).slice(0, 400), until: Date.now() + t * 1000 })
    cards = c
    return cardSeq
  }

  function dismissCard(id) { cards = cards.filter(function (c) { return c.id !== id }) }

  function answerConfirm(answer) {
    var r = confirmReq
    if (!r) return
    confirmReq = null
    var payload = JSON.stringify({ id: r.id, answer: answer, ts: Date.now() })
    sh(["sh", "-c", 'mkdir -p "$1" && printf "%s" "$2" > "$1/$3.json.tmp" && mv "$1/$3.json.tmp" "$1/$3.json"',
        "sh", runDir + "/confirm", payload, r.id])
  }

  function netLabel(kv) {   // operstate per interface -> "wifi · ts", "usb", "offline"
    var up = function (n) { var v = kv["net_" + n]; return v === "up" || v === "unknown" }
    var link = up("wlan0") ? "wifi" : (up("ecm0") ? "usb" : (up("rmnet0") ? "cell" : ""))
    var ts = kv["net_tailscale0"] && kv["net_tailscale0"] !== "absent" && kv["net_tailscale0"] !== "down"
    return link ? link + (ts ? " · ts" : "") : (ts ? "ts" : "offline")
  }

  function refreshStatus() {
    http("GET", api + "/api/model/current", null, function (s, j) {
      var m = j && j.model ? String(j.model) : "unreachable"
      root.st = Object.assign({}, root.st, { model: m.replace(/^openai\//, "").replace(/^llama-cpp-local\/+(models\/)?/, "local ") })
    })
    http("GET", api + "/api/health", null, function (s, j) {
      var h = j ? (j.healthy === true ? "healthy" : (j.health && j.health.status) || "degraded") : "down"
      root.st = Object.assign({}, root.st, { healthy: h })
    })
    http("GET", api + "/api/missions", null, function (s, j) {
      var ms = (j && j.missions) || []
      var run = ms.filter(function (m) { return m.status === "running" }).length
      root.st = Object.assign({}, root.st, { running: run })
    })
    http("GET", api + "/api/sessions?limit=20", null, function (s, j) {
      var ss = (j && j.sessions) || []
      var hour = Date.now() - 3600 * 1000
      var recent = ss.filter(function (x) { return Date.parse(x.lastMessageAt || 0) > hour }).length
      root.st = Object.assign({}, root.st, { sessions: recent + " active / " + ss.length })
    })
    sysProc.running = true
  }

  Process {
    id: sysProc
    command: ["sh", "-c", "b=/sys/class/power_supply/battery; echo cap=$(cat $b/capacity); echo state=$(cat $b/status); echo temp=$(cat $b/temp); for z in /sys/class/thermal/thermal_zone*; do echo z_$(cat $z/type)=$(cat $z/temp); done; for n in wlan0 ecm0 rmnet0 tailscale0; do echo net_$n=$(cat /sys/class/net/$n/operstate 2>/dev/null || echo absent); done; cat /run/s22-touch/guardian.env 2>/dev/null"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var kv = {}
        text.split("\n").forEach(function (l) { var i = l.indexOf("="); if (i > 0) kv[l.slice(0, i)] = l.slice(i + 1) })
        var zones = ["z_BIG", "z_MID", "z_LITTLE", "z_G3D"]
        var mx = 0, parts = []
        zones.forEach(function (z) { var v = Number(kv[z]) / 1000; if (v > 0) { mx = Math.max(mx, v); parts.push(z.slice(2) + " " + v.toFixed(0)) } })
        root.st = Object.assign({}, root.st, {
          battery: (kv.cap || "?") + "%", batteryState: kv.state || "", batteryTemp: kv.temp ? (Number(kv.temp) / 10).toFixed(1) + "°C" : "",
          thermal: mx ? mx.toFixed(0) + "°C" : "?", thermalMax: mx, thermalDetail: parts.join(" · "),
          net: root.netLabel(kv),
          guard: Number(kv.g_level || 0) >= 1 ? "⚠ " + (kv.g_tmax || "?") + "°C" + (kv.g_msg ? " " + kv.g_msg : "") : ""
        })
      }
    }
  }

  Timer {
    interval: 10000; repeat: true
    running: root.homeVisible || root.locked
    onTriggered: root.refreshStatus()
  }

  Timer {   // expire cards and confirm requests
    interval: 1000; repeat: true
    running: root.cards.length > 0 || root.confirmReq !== null
    onTriggered: {
      var now = Date.now()
      if (root.cards.some(function (c) { return c.until <= now })) root.cards = root.cards.filter(function (c) { return c.until > now })
      if (root.confirmReq && root.confirmReq.until <= now) root.answerConfirm("timeout")
    }
  }

  Timer { id: kbT; interval: 1500; onTriggered: root.keyboard(true) }

  SystemClock { id: clock; precision: SystemClock.Minutes }

  // ------------------------------------------------------------ IPC
  IpcHandler {
    target: "touch"
    function home(): string {
      if (root.locked) { root.locked = false; return "unlocked" }   // bottom-edge swipe up also unlocks
      root.showHome(""); return "ok"
    }
    function hide(): string { root.hideHome(); return "ok" }
    function back(): string { return root.back() }
    function switcher(): string { if (root.locked) return "locked"; root.showHome("switcher"); return "ok" }
    function open(app: string): string { return root.openApp(app) }
    function card(title: string, body: string, ttl: string): string { return "card " + root.addCard(title, body, ttl) }
    function confirm(id: string, question: string, timeoutS: string): string {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) return "bad id"
      if (root.confirmReq) return "busy"
      var t = Math.max(5, Math.min(600, Number(timeoutS) || 60))
      root.confirmReq = { id: id, question: String(question).slice(0, 500), until: Date.now() + t * 1000 }
      return "asking"
    }
    function confirmResult(id: string): string { return root.confirmReq && root.confirmReq.id === id ? "pending" : "done" }
    function lock(): string { root.locked = true; root.homeVisible = false; root.keyboard(false); root.refreshStatus(); return "locked" }
    function unlock(): string { root.locked = false; return "unlocked" }
    function keyboard(mode: string): string { root.keyboard(mode !== "off" && mode !== "false" && mode !== "0"); return "ok" }
    function state(): string {
      return JSON.stringify({ home: root.homeVisible, page: root.page, locked: root.locked, cards: root.cards.length,
                              confirm: root.confirmReq ? root.confirmReq.id : null, status: root.st })
    }
    function perf(seconds: string): string {
      if (root.perfReq) return "busy"
      root.perfReq = { until: Date.now() + Math.max(2, Math.min(30, Number(seconds) || 5)) * 1000, frames: [] }
      return "measuring"
    }
  }

  // ------------------------------------------------------------ status strip
  // There is no desktop bar any more (Omarchy removed 2026-10-10), so the
  // touch shell keeps a thin strip on top on both compositors: time, guardian
  // warning, network, agent model, battery. Tap = home / back.
  property string clock: ""
  Timer {
    interval: 15000; repeat: true; running: true; triggeredOnStart: true
    onTriggered: root.clock = Qt.formatDateTime(new Date(), "HH:mm")
  }
  Timer {
    interval: 30000; repeat: true; running: !root.homeVisible && !root.locked
    triggeredOnStart: true
    onTriggered: root.refreshStatus()
  }
  PanelWindow {
    id: strip
    visible: !root.locked
    anchors { top: true; left: true; right: true }
    implicitHeight: 28
    exclusiveZone: 28
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "s22-touch-strip"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    color: root.k("#0b0e12")
    onVisibleChanged: if (visible) root.kickSoon()
    Text {
      anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter
      text: root.clock; color: root.fg; font.pixelSize: 15; font.bold: true
    }
    Text {
      anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter
      width: parent.width - 220; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
      text: (root.st.healthy === "healthy" ? "● " : "○ ") + root.st.model; color: root.dim; font.pixelSize: 13
    }
    Text {
      anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
      text: (root.st.guard ? root.st.guard + "   " : "") + (root.st.net || "…") + "   " + root.st.battery
            + (root.st.batteryState === "Charging" ? "+" : "")
      color: root.st.guard ? "#ffb04f" : root.fg; font.pixelSize: 13
    }
    TapHandler { onTapped: root.homeVisible ? root.hideHome() : root.showHome("") }
  }

  // ------------------------------------------------------------ home + pages
  PanelWindow {
    id: home
    visible: root.homeVisible && !root.locked
    anchors { top: true; bottom: true; left: true; right: true }
    exclusionMode: ExclusionMode.Normal       // stays above the keyboard and below the bar
    exclusiveZone: 0
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "s22-touch-home"
    WlrLayershell.keyboardFocus: root.page !== "chat" ? WlrKeyboardFocus.None
                                 : (root.onSway ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand)
    color: root.k(root.bg)
    onVisibleChanged: if (visible) root.kickSoon()

    Loader {
      anchors.fill: parent
      active: home.visible
      sourceComponent: root.page === "" ? gridComp : pageComp
    }
  }

  Component {
    id: gridComp
    Item {
      Column {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

        Row {
          width: parent.width
          Column {
            width: parent.width - 120
            Text { text: Qt.formatDateTime(clock.date, "HH:mm"); color: root.fg; font.pixelSize: 44; font.bold: true }
            Text { text: Qt.formatDateTime(clock.date, "dddd d MMMM"); color: root.dim; font.pixelSize: 15 }
          }
          Btn { text: "Lock"; width: 120; onClicked: lockProc.running = true }
        }

        Grid {
          columns: 2; spacing: 8; width: parent.width
          StatCard {
            width: (parent.width - 8) / 2; height: 74
            title: "Agent model"; value: root.st.model
            detail: root.st.healthy === null ? "" : "OpenUnum " + root.st.healthy
            dot: root.st.healthy === "healthy" ? "#3fb950" : (root.st.healthy === "down" ? "#f85149" : "#d29922")
          }
          StatCard {
            width: (parent.width - 8) / 2; height: 74
            title: "Sessions"; value: root.st.sessions
            detail: root.st.running + " mission(s) running"
          }
          StatCard {
            width: (parent.width - 8) / 2; height: 74
            title: "Battery"; value: root.st.battery
            detail: root.st.batteryState + (root.st.batteryTemp ? " · " + root.st.batteryTemp : "")
          }
          StatCard {
            width: (parent.width - 8) / 2; height: 74
            title: "Thermal"; value: root.st.thermal
            detail: root.st.thermalDetail || ""
            dot: root.st.thermalMax >= 60 ? "#f85149" : (root.st.thermalMax >= 48 ? "#d29922" : "#3fb950")
          }
        }

        Grid {
          id: tiles
          columns: 2; spacing: 10; width: parent.width
          property real th: Math.max(96, Math.min(220, (parent.height - y - 76) / 3 - 10))
          Tile { width: (parent.width - 10) / 2; height: tiles.th; glyph: "◆"; label: "Agent chat"; sub: "OpenUnum, typed"; onActivated: root.showHome("chat") }
          Tile { width: (parent.width - 10) / 2; height: tiles.th; glyph: "◍"; label: "Phone"; sub: "Calls & SMS (read-only)"; accent: "#3fb950"; onActivated: root.showHome("phone") }
          Tile { width: (parent.width - 10) / 2; height: tiles.th; glyph: "◉"; label: "Camera"; sub: "Rear still (experimental)"; accent: "#d29922"; onActivated: root.showHome("camera") }
          Tile { width: (parent.width - 10) / 2; height: tiles.th; glyph: "▤"; label: "Files"; sub: "Browse /root"; accent: "#a371f7"; onActivated: root.showHome("files") }
          Tile { width: (parent.width - 10) / 2; height: tiles.th; glyph: "›_"; label: "Terminal"; sub: "foot shell"; accent: "#8b98a7"; onActivated: root.openApp("terminal") }
          Tile { width: (parent.width - 10) / 2; height: tiles.th; glyph: "⚙"; label: "Settings"; sub: "Wi-Fi, brightness, model"; accent: "#f0883e"; onActivated: root.showHome("settings") }
        }

        Row {
          spacing: 8; width: parent.width
          Btn { text: "Apps"; width: (parent.width - 8) / 2; onClicked: root.showHome("switcher") }
          Btn { text: "Keyboard"; width: (parent.width - 8) / 2; onClicked: root.keyboard(true) }
        }
      }
    }
  }

  Component {
    id: pageComp
    Item {
      Rectangle {
        id: bar
        width: parent.width; height: 60; color: "#151a20"
        Btn { id: backBtn; text: "‹ Back"; width: 110; height: 48; anchors.left: parent.left; anchors.leftMargin: 8; anchors.verticalCenter: parent.verticalCenter; onClicked: root.back() }
        Text {
          anchors.left: backBtn.right; anchors.leftMargin: 14; anchors.verticalCenter: parent.verticalCenter
          text: ({ chat: "Agent chat", phone: "Phone", camera: "Camera", files: "Files", settings: "Settings", switcher: "Open apps" })[root.page] || root.page
          color: root.fg; font.pixelSize: 22; font.bold: true
        }
        Btn { text: "Home"; width: 90; height: 48; anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; onClicked: root.page = "" }
      }
      Loader {
        anchors.top: bar.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 12
        source: ({ chat: "ChatPage.qml", phone: "PhonePage.qml", camera: "CameraPage.qml", files: "FilesPage.qml", settings: "SettingsPage.qml", switcher: "SwitcherPage.qml" })[root.page] || ""
        onLoaded: item.shell = root
      }
    }
  }

  Process { id: lockProc; command: ["s22-ui", "display", "off"] }

  // ------------------------------------------------------------ cards
  PanelWindow {
    visible: root.cards.length > 0 && !root.locked
    anchors { top: true; left: true; right: true }
    margins { top: 34; left: 10; right: 10 }
    implicitHeight: cardCol.implicitHeight
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "s22-touch-cards"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    // opaque on purpose: a transparent window colour cannot carry the repaint kick
    color: root.k("#1f2933")
    onVisibleChanged: if (visible) root.kickSoon()
    Column {
      id: cardCol
      width: parent.width
      spacing: 0
      Repeater {
        model: root.cards
        Rectangle {
          width: cardCol.width; height: cardText.implicitHeight + 24; radius: 0
          color: "transparent"; border.color: root.accent; border.width: 1
          Column {
            id: cardText
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
            spacing: 4
            Text { text: modelData.title; color: root.fg; font.pixelSize: 17; font.bold: true; width: parent.width; elide: Text.ElideRight }
            Text { text: modelData.body; color: "#c9d1d9"; font.pixelSize: 15; width: parent.width; wrapMode: Text.Wrap; maximumLineCount: 6; elide: Text.ElideRight }
            Text { text: "tap to dismiss"; color: root.dim; font.pixelSize: 11 }
          }
          TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: root.dismissCard(modelData.id) }
        }
      }
    }
  }

  // ------------------------------------------------------------ confirm
  PanelWindow {
    visible: root.confirmReq !== null && !root.locked
    anchors { top: true; bottom: true; left: true; right: true }
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "s22-touch-confirm"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    color: root.kick ? "#cc010101" : "#cc000000"
    onVisibleChanged: if (visible) root.kickSoon()
    Rectangle {
      anchors.centerIn: parent
      width: parent.width - 40; height: qcol.implicitHeight + 40; radius: 18
      color: "#1b2129"; border.color: "#d29922"; border.width: 2
      Column {
        id: qcol
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20
        spacing: 18
        Text { text: "The agent asks you to confirm"; color: "#d29922"; font.pixelSize: 15; font.bold: true }
        Text { text: root.confirmReq ? root.confirmReq.question : ""; color: root.fg; font.pixelSize: 20; width: parent.width; wrapMode: Text.Wrap }
        Row {
          spacing: 12; width: parent.width
          Btn { text: "No"; width: (parent.width - 12) / 2; height: 72; tint: "#4a2326"; onClicked: root.answerConfirm("no") }
          Btn { text: "Yes"; width: (parent.width - 12) / 2; height: 72; tint: "#1f4a2c"; onClicked: root.answerConfirm("yes") }
        }
      }
    }
  }

  // ------------------------------------------------------------ lock cover
  // A soft lock (pocket/accidental-touch guard), NOT an authentication lock:
  // ext-session-lock is deliberately not used, because a crashed locker
  // would leave Hyprland on its "lock screen died" screen.
  PanelWindow {
    id: lockWin
    visible: root.locked
    anchors { top: true; bottom: true; left: true; right: true }
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "s22-touch-lock"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    color: root.k("#05070a")
    onVisibleChanged: if (visible) root.kickSoon()
    property real dragY: 0
    Column {
      anchors.horizontalCenter: parent.horizontalCenter
      y: 160 - lockWin.dragY / 3
      spacing: 10
      Text { anchors.horizontalCenter: parent.horizontalCenter; text: Qt.formatDateTime(clock.date, "HH:mm"); color: root.fg; font.pixelSize: 84; font.bold: true }
      Text { anchors.horizontalCenter: parent.horizontalCenter; text: Qt.formatDateTime(clock.date, "dddd d MMMM"); color: root.dim; font.pixelSize: 18 }
      Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Battery " + root.st.battery + "  ·  " + root.st.model; color: root.dim; font.pixelSize: 15 }
      Item { width: 1; height: 30 }
      Repeater {
        model: root.cards
        Rectangle {
          width: lockWin.width - 60; height: 64; radius: 12; color: "#151b22"
          Column { anchors.fill: parent; anchors.margins: 10
            Text { text: modelData.title; color: root.fg; font.pixelSize: 15; font.bold: true; width: parent.width; elide: Text.ElideRight }
            Text { text: modelData.body; color: root.dim; font.pixelSize: 13; width: parent.width; elide: Text.ElideRight }
          }
        }
      }
      Text {
        visible: root.confirmReq !== null
        width: lockWin.width - 60; wrapMode: Text.Wrap
        text: root.confirmReq ? "The agent is waiting for your confirmation:\n" + root.confirmReq.question + "\nUnlock to answer." : ""
        color: "#d29922"; font.pixelSize: 16
      }
    }
    Text {
      anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 60
      text: "Swipe up to unlock"; color: root.dim; font.pixelSize: 18
    }
    MouseArea {      // touch arrives as synthesized mouse events; a plain press/release delta is the most robust swipe
      anchors.fill: parent
      property real y0: 0
      onPressed: (m) => { y0 = m.y }
      onPositionChanged: (m) => { lockWin.dragY = Math.max(0, y0 - m.y) }
      onReleased: (m) => {
        if (y0 - m.y > lockWin.height * 0.22) root.locked = false
        lockWin.dragY = 0
      }
      onCanceled: lockWin.dragY = 0
    }
  }

  // ------------------------------------------------------------ frame-time probe
  // `perf N`: animates a small strip for N seconds with FrameAnimation and
  // writes per-frame times to /run/s22-touch/perf.json. Only exists while measuring.
  PanelWindow {
    id: perfWin
    visible: root.perfReq !== null
    anchors { bottom: true; left: true; right: true }
    implicitHeight: 80
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "s22-touch-perf"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    color: root.k("#202830")
    onVisibleChanged: if (visible) root.kickSoon()
    Rectangle { id: perfBar; width: 60; height: 60; y: 10; color: root.accent }
    Text { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; text: "measuring frame time…"; color: root.fg }
    FrameAnimation {
      running: root.perfReq !== null
      property var times: []
      onRunningChanged: if (running) times = []
      onTriggered: {
        perfBar.x = (perfBar.x + 6) % Math.max(60, perfWin.width - 60)
        if (frameTime > 0) times.push(frameTime * 1000)
        if (root.perfReq && Date.now() >= root.perfReq.until) {
          var t = times.slice(1).sort(function (a, b) { return a - b })
          var n = t.length, sum = t.reduce(function (a, b) { return a + b }, 0)
          var res = { frames: n, mean_ms: n ? sum / n : null, p50_ms: n ? t[Math.floor(n * 0.5)] : null,
                      p95_ms: n ? t[Math.floor(n * 0.95)] : null, max_ms: n ? t[n - 1] : null,
                      fps: n ? 1000 / (sum / n) : null, ts: Date.now() }
          root.sh(["sh", "-c", 'printf "%s" "$1" > /run/s22-touch/perf.json', "sh", JSON.stringify(res)])
          root.perfReq = null
        }
      }
    }
  }
}
