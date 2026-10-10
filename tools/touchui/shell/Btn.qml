import QtQuick

// Plain touch button, >= 56 logical px tall (112 physical px on the S22).
Rectangle {
  id: b
  property string text: ""
  property color tint: "#25303c"
  property bool enabledBtn: true
  property bool holdToConfirm: false
  signal clicked()
  implicitHeight: 56
  implicitWidth: Math.max(96, label.implicitWidth + 32)
  radius: 12
  color: !enabledBtn ? "#1a1f25" : (tap.pressed ? Qt.lighter(tint, 1.4) : tint)
  Text {
    id: label
    anchors.centerIn: parent
    text: b.text + (b.holdToConfirm ? "  (hold)" : "")
    color: b.enabledBtn ? "#e8edf2" : "#5c6773"
    font.pixelSize: 17
    font.bold: true
  }
  TapHandler {
    id: tap
    enabled: b.enabledBtn
    // Real fingertips drift and linger: a tap that moved a few px or was held
    // past the long-press threshold used to be dropped (synthetic taps never
    // were). Count any release inside the control; long-press only when needed.
    gesturePolicy: TapHandler.ReleaseWithinBounds
    longPressThreshold: b.holdToConfirm ? 0.7 : 0
    onTapped: if (!b.holdToConfirm) b.clicked()
    onLongPressed: if (b.holdToConfirm) b.clicked()
  }
}
