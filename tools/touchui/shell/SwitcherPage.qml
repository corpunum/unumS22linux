import QtQuick
import Quickshell.Hyprland

// App switcher: every Hyprland window as a big row. Tap = focus, hold "Close" = close.
Item {
  id: page
  property var shell: null
  property var wins: []

  function load() {
    Hyprland.refreshToplevels()
    var tl = Hyprland.toplevels.values, out = []
    for (var i = 0; i < tl.length; i++) {
      var o = tl[i].lastIpcObject || {}
      out.push({ t: tl[i], cls: o["class"] || "?", title: tl[i].title || o.title || "", ws: o.workspace ? o.workspace.name : "" })
    }
    wins = out
  }
  onShellChanged: load()
  Timer { interval: 600; running: true; onTriggered: page.load() }   // lastIpcObject fills in after refresh

  ListView {
    anchors.fill: parent
    spacing: 10; clip: true
    model: page.wins
    delegate: Rectangle {
      width: ListView.view.width; height: 96; radius: 14
      color: tapW.pressed ? "#2a3440" : "#1b2129"; border.color: "#2c3642"
      Column {
        anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter
        width: parent.width - 150
        Text { text: modelData.cls; color: "#e8edf2"; font.pixelSize: 19; font.bold: true; width: parent.width; elide: Text.ElideRight }
        Text { text: modelData.title + (modelData.ws ? "  ·  ws " + modelData.ws : ""); color: "#8b98a7"; font.pixelSize: 14; width: parent.width; elide: Text.ElideRight }
      }
      TapHandler { id: tapW; onTapped: { page.shell.focusToplevel(modelData.t); page.shell.hideHome() } }
      Btn {
        anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
        text: "Close"; width: 120; holdToConfirm: true; tint: "#4a2326"
        onClicked: {
          page.shell.focusToplevel(modelData.t)
          Hyprland.dispatch("hl.dsp.window.close()")
          page.load()
        }
      }
    }
  }
  Text { anchors.centerIn: parent; visible: page.wins.length === 0; text: "No open apps"; color: "#5c6773"; font.pixelSize: 17 }
}
