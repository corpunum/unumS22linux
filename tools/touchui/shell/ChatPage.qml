import QtQuick
import QtQuick.Controls

// Typed chat with the phone's own OpenUnum in session "touch".
Item {
  id: page
  property var shell: null
  property var msgs: []
  property bool pending: false
  property int polls: 0
  readonly property string sid: "touch"

  function load() {
    if (!shell) return
    shell.http("GET", shell.api + "/api/sessions/" + sid + "?limit=30", null, function (s, j) {
      var m = (j && j.messages) || []
      page.msgs = m.filter(function (x) { return x.role === "user" || x.role === "assistant" }).slice(-30).map(function (x) {
        var c = String(x.content || "")
        return { role: x.role, text: c.length > 1500 ? c.slice(0, 1500) + " …" : c }
      })
      list.positionViewAtEnd()
    })
  }

  function send() {
    var t = input.text.trim()
    if (!t || pending) return
    input.text = ""
    page.msgs = page.msgs.concat([{ role: "user", text: t }])
    pending = true
    polls = 0
    shell.http("POST", shell.api + "/api/chat", { sessionId: sid, message: t }, function (s, j) {
      load()
      if (s >= 200 && s < 300 && j && (j.reply || j.content || j.message)) pending = false
    }, 180000)
  }

  onShellChanged: load()

  Timer {   // a long turn answers 202 first: poll until the reply lands (max ~3 min)
    interval: 3000; repeat: true; running: page.pending
    onTriggered: {
      page.polls += 1
      page.load()
      var last = page.msgs.length ? page.msgs[page.msgs.length - 1] : null
      if ((last && last.role === "assistant") || page.polls > 60) page.pending = false
    }
  }

  ListView {
    id: list
    anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: inputRow.top
    anchors.bottomMargin: 8
    clip: true
    spacing: 8
    model: page.msgs
    boundsBehavior: Flickable.StopAtBounds
    delegate: Rectangle {
      width: list.width * 0.88
      x: modelData.role === "user" ? ListView.view.width - width : 0
      height: msgText.implicitHeight + 20
      radius: 12
      color: modelData.role === "user" ? "#1d3a5c" : "#1b2129"
      Text {
        id: msgText
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
        text: modelData.text; wrapMode: Text.Wrap; color: "#e8edf2"; font.pixelSize: 16
      }
    }
    footer: Text {
      visible: page.pending; height: visible ? 40 : 0
      text: "Agent is working…"; color: "#8b98a7"; font.pixelSize: 15
    }
  }

  Row {
    id: inputRow
    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
    spacing: 8
    TextField {
      id: input
      width: parent.width - sendBtn.width - 8; height: 56
      placeholderText: "Message the agent"
      font.pixelSize: 17
      color: "#e8edf2"
      placeholderTextColor: "#5c6773"
      background: Rectangle { radius: 12; color: "#161b21"; border.color: input.activeFocus ? "#4fa3ff" : "#2c3642" }
      inputMethodHints: Qt.ImhNoPredictiveText
      onActiveFocusChanged: if (activeFocus && page.shell) page.shell.keyboard(true)
      onAccepted: page.send()
    }
    Btn { id: sendBtn; text: page.pending ? "…" : "Send"; width: 96; height: 56; tint: "#1f4a7a"; enabledBtn: !page.pending; onClicked: page.send() }
  }
}
