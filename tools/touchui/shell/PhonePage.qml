import QtQuick

// Read-only view of s22-phoned: modem/SIM status, SMS inbox, calls.
// Sending SMS / dialing stays with the agent tools (s22-phoned gate + allowlist).
Flickable {
  id: page
  property var shell: null
  property var status: null
  property var sms: []
  property var calls: []
  contentHeight: col.implicitHeight
  clip: true
  boundsBehavior: Flickable.StopAtBounds

  function load() {
    if (!shell) return
    shell.http("GET", shell.phoned + "/status", null, function (s, j) { page.status = j || { ok: false, error: "phoned unreachable" } })
    shell.http("GET", shell.phoned + "/sms/inbox?limit=20", null, function (s, j) { page.sms = (j && j.messages) || [] })
    shell.http("GET", shell.phoned + "/calls", null, function (s, j) { page.calls = (j && j.calls) || [] })
  }
  onShellChanged: load()

  function line(k, v) { return k + ": " + (v === undefined || v === null ? "—" : v) }

  Column {
    id: col
    width: page.width
    spacing: 10
    Rectangle {
      width: parent.width; height: stCol.implicitHeight + 24; radius: 12; color: "#161b21"
      Column {
        id: stCol
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
        spacing: 4
        property var s: page.status || {}
        property var reg: s.registration || {}
        property var sig: s.signal || {}
        Text { text: page.status === null ? "Loading…" : (stCol.s.ok === false ? "s22-phoned: " + (stCol.s.error || "error") : "Modem " + stCol.s.modem_state); color: "#e8edf2"; font.pixelSize: 19; font.bold: true }
        Text { text: page.line("SIM", stCol.s.sim); color: "#c9d1d9"; font.pixelSize: 15 }
        Text { text: page.line("Network", stCol.reg.reg_status ? stCol.reg.reg_status + " (" + stCol.reg.act + ")" : undefined); color: "#c9d1d9"; font.pixelSize: 15 }
        Text { text: page.line("Operator", stCol.s.operator); color: "#c9d1d9"; font.pixelSize: 15 }
        Text { text: page.line("Signal", stCol.sig.lte_rsrp_dbm !== undefined ? stCol.sig.lte_rsrp_dbm + " dBm" : undefined); color: "#c9d1d9"; font.pixelSize: 15 }
        Text { text: page.line("Outbound", stCol.s.tx_enabled ? "enabled (allowlist)" : "off"); color: "#c9d1d9"; font.pixelSize: 15 }
        Text {
          visible: stCol.s.sim === "CARD_NOT_PRESENT"
          text: "No SIM inserted: calls and SMS are read-only until a SIM is in."
          color: "#d29922"; font.pixelSize: 14; width: parent.width; wrapMode: Text.Wrap
        }
      }
    }
    Btn { text: "Refresh"; width: 140; onClicked: page.load() }
    Text { text: "Messages (" + page.sms.length + ")"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Repeater {
      model: page.sms
      Rectangle {
        width: col.width; height: smsCol.implicitHeight + 20; radius: 10; color: "#1b2129"
        Column {
          id: smsCol
          anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
          Text { text: (modelData.from || "?") + "  ·  " + (modelData.ts || modelData.time || ""); color: "#8b98a7"; font.pixelSize: 13 }
          Text { text: modelData.text || ""; color: "#e8edf2"; font.pixelSize: 16; width: parent.width; wrapMode: Text.Wrap }
        }
      }
    }
    Text { visible: page.sms.length === 0; text: "No messages."; color: "#5c6773"; font.pixelSize: 14 }
    Text { text: "Calls (" + page.calls.length + ")"; color: "#8b98a7"; font.pixelSize: 15; font.bold: true }
    Repeater {
      model: page.calls
      Text { text: (modelData.number || "?") + "  " + (modelData.state || "") ; color: "#e8edf2"; font.pixelSize: 16 }
    }
    Text { visible: page.calls.length === 0; text: "No active calls."; color: "#5c6773"; font.pixelSize: 14 }
  }
}
