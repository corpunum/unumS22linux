import QtQuick

// Big touch tile. No animation: a pressed state only changes the colour.
Rectangle {
  id: tile
  property string label: ""
  property string sub: ""
  property string glyph: ""
  property color accent: "#4fa3ff"
  property bool holdToConfirm: false   // long-press (700 ms) instead of tap
  signal activated()

  radius: 14
  color: tap.pressed ? "#2a3440" : "#1b2129"
  border.color: "#2c3642"
  border.width: 1

  Column {
    anchors.left: parent.left
    anchors.leftMargin: 16
    anchors.verticalCenter: parent.verticalCenter
    spacing: 4
    Text { text: tile.glyph; color: tile.accent; font.pixelSize: 30; font.bold: true }
    Text { text: tile.label; color: "#e8edf2"; font.pixelSize: 20; font.bold: true }
    Text {
      text: tile.sub; color: "#8b98a7"; font.pixelSize: 13
      visible: text.length > 0; width: tile.width - 32; elide: Text.ElideRight
    }
  }

  TapHandler {
    id: tap
    longPressThreshold: 0.7
    onTapped: if (!tile.holdToConfirm) tile.activated()
    onLongPressed: if (tile.holdToConfirm) tile.activated()
  }
}
