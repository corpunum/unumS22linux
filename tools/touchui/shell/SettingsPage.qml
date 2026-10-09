import QtQuick
import QtQuick.Controls
import Quickshell.Io

// Wi-Fi (status), brightness, volume (pinned at 0, read-only), model switch.
Flickable {
  id: page
  property var shell: null
  property var host: ({})
  property string wifi: "…"
  property int bright: 128
  property int brightMax: 510
  property string model: ""
  property string modelMsg: ""
  contentHeight: col.implicitHeight
  clip: true
  boundsBehavior: Flickable.StopAtBounds

  function load() {
    statusProc.running = true
    wifiProc.running = true
    brProc.running = true
    if (shell) shell.http("GET", shell.api + "/api/model/current", null, function (s, j) { page.model = j ? (j.provider + " · " + j.model) : "unreachable" })
  }
  onShellChanged: load()
  Timer { interval: 15000; repeat: true; running: true; onTriggered: page.load() }

  Process {
    id: statusProc; command: ["s22-ui", "status"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: { try { page.host = JSON.parse(text) } catch (e) { page.host = { ok: false } } } }
  }
  Process {
    id: wifiProc
    command: ["sh", "-c", "echo state=$(cat /sys/class/net/wlan0/operstate 2>/dev/null); ip -4 -o addr show wlan0 2>/dev/null | awk '{print \"ip=\"$4}'; ip -4 -o addr show tailscale0 2>/dev/null | awk '{print \"ts=\"$4}'"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: page.wifi = text.trim().split("\n").join("   ") }
  }
  Process {
    id: brProc
    command: ["sh", "-c", "cat /sys/class/backlight/panel/brightness /sys/class/backlight/panel/max_brightness"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: { var v = text.trim().split("\n"); page.brightMax = Number(v[1]) || 510; page.bright = Number(v[0]) || 0 } }
  }
  Process { id: brSet; property int v: 128; command: ["sh", "-c", "echo $1 > /sys/class/backlight/panel/brightness", "sh", String(v)] }
  Process {
    id: modelProc; property string choice: "luna"
    command: ["s22-ui", "model", choice]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: { var j = {}; try { j = JSON.parse(text) } catch (e) { }
      page.modelMsg = j.ok ? "Switched; s22-keepalive now pins this choice." : "Switch failed: " + (j.error || text.slice(0, 120)); page.load() } }
  }

  Column {
    id: col
    width: page.width
    spacing: 14

    Text { text: "Wi-Fi"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Rectangle {
      width: parent.width; height: 64; radius: 12; color: "#161b21"
      Text { anchors.fill: parent; anchors.margins: 12; text: page.wifi; color: "#e8edf2"; font.pixelSize: 15; wrapMode: Text.Wrap; verticalAlignment: Text.AlignVCenter }
    }
    Text { text: "Managed by the S22 wifi-autostart service (status only here)."; color: "#5c6773"; font.pixelSize: 12; width: parent.width; wrapMode: Text.Wrap }

    Text { text: "Brightness  " + Math.round(100 * page.bright / page.brightMax) + "%"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Slider {
      id: slider
      width: parent.width; height: 56
      from: 8; to: page.brightMax; stepSize: 1
      value: page.bright
      onMoved: { brSet.v = Math.round(value); brSet.running = true; page.bright = Math.round(value) }
    }

    Text { text: "Volume"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Rectangle {
      width: parent.width; height: volCol.implicitHeight + 24; radius: 12; color: "#161b21"
      Column {
        id: volCol
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
        spacing: 4
        Text {
          text: page.host.ok === undefined ? "Checking…" : (page.host.muted ? "Muted (pinned at 0)" : "NOT MUTED — s22-keepalive will re-mute")
          color: page.host.muted ? "#3fb950" : "#f85149"; font.pixelSize: 18; font.bold: true
        }
        Text {
          text: "level " + page.host.volume_level + " · amps " + JSON.stringify(page.host.amps) + " · keepalive " + (page.host.keepalive ? "on" : "off")
          color: "#8b98a7"; font.pixelSize: 13
        }
        Text { text: "Volume is pinned at 0 by the owner's rule; there is no control here."; color: "#5c6773"; font.pixelSize: 12; width: parent.width; wrapMode: Text.Wrap }
      }
    }

    Text { text: "Agent model"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Text { text: page.model; color: "#e8edf2"; font.pixelSize: 15; width: parent.width; wrapMode: Text.WrapAnywhere }
    Row {
      spacing: 10; width: parent.width
      Btn { text: "Luna (cloud)"; width: (parent.width - 10) / 2; height: 64; holdToConfirm: true; tint: page.model.indexOf("luna") >= 0 ? "#1f4a7a" : "#25303c"
            onClicked: { modelProc.choice = "luna"; page.modelMsg = "Switching…"; modelProc.running = true } }
      Btn { text: "Local 2B (GPU)"; width: (parent.width - 10) / 2; height: 64; holdToConfirm: true; tint: page.model.indexOf("llama") >= 0 ? "#1f4a7a" : "#25303c"
            onClicked: { modelProc.choice = "local"; page.modelMsg = "Switching…"; modelProc.running = true } }
    }
    Text { text: page.modelMsg; visible: text.length > 0; color: "#d29922"; font.pixelSize: 13; width: parent.width; wrapMode: Text.Wrap }

    Text { text: "Screen"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Row {
      spacing: 10; width: parent.width
      Btn { text: "Lock + screen off"; width: (parent.width - 10) / 2; onClicked: lockOff.running = true }
      Btn { text: "Keyboard"; width: (parent.width - 10) / 2; onClicked: if (page.shell) page.shell.keyboard(true) }
    }
    Process { id: lockOff; command: ["s22-ui", "display", "off"] }
  }
}
