import QtQuick

// Small read-only status card on the home screen.
Rectangle {
  id: c
  property string title: ""
  property string value: ""
  property string detail: ""
  property color dot: "transparent"
  radius: 12
  color: "#161b21"
  border.color: "#252e38"
  Column {
    anchors.fill: parent
    anchors.margins: 10
    spacing: 2
    Row {
      spacing: 6
      Rectangle { width: 10; height: 10; radius: 5; color: c.dot; visible: c.dot != "transparent"; anchors.verticalCenter: parent.verticalCenter }
      Text { text: c.title; color: "#8b98a7"; font.pixelSize: 12 }
    }
    Text { text: c.value; color: "#e8edf2"; font.pixelSize: 17; font.bold: true; width: parent.width; elide: Text.ElideRight }
    Text { text: c.detail; color: "#8b98a7"; font.pixelSize: 12; width: parent.width; elide: Text.ElideRight; visible: text.length > 0 }
  }
}
