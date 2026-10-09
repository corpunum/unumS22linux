import QtQuick
import Qt.labs.folderlistmodel
import Quickshell.Io

// Minimal finger-sized file browser (read-only): folders, image preview, small text preview.
Item {
  id: page
  property var shell: null
  property string dir: "/root"
  property string preview: ""
  property string previewKind: ""

  function isImage(n) { return /\.(png|jpe?g|webp|gif|bmp)$/i.test(n) }
  function isText(n) { return /\.(txt|md|json|jsonl|log|conf|toml|ini|sh|py|qml|js|mjs|lua|csv)$/i.test(n) }
  function human(b) { return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : b > 1024 ? (b / 1024).toFixed(0) + " KB" : b + " B" }

  FolderListModel {
    id: fm
    folder: "file://" + page.dir
    showDirsFirst: true
    showHidden: false
    sortField: FolderListModel.Name
  }
  FileView { id: tv; blockLoading: true }

  Column {
    anchors.fill: parent
    spacing: 8
    Row {
      spacing: 8; width: parent.width
      Btn { text: "Up"; width: 80; enabledBtn: page.dir !== "/"; onClicked: { page.preview = ""; page.dir = page.dir.replace(/\/[^\/]+\/?$/, "") || "/" } }
      Text { text: page.dir; color: "#c9d1d9"; font.pixelSize: 15; width: parent.width - 96; elide: Text.ElideLeft; anchors.verticalCenter: parent.verticalCenter }
    }
    Rectangle {
      visible: page.preview !== ""
      width: parent.width; height: visible ? parent.height * 0.45 : 0; radius: 10; color: "#05070a"; clip: true
      Image { anchors.fill: parent; visible: page.previewKind === "image"; fillMode: Image.PreserveAspectFit; asynchronous: true; source: page.previewKind === "image" ? "file://" + page.preview : "" }
      Flickable {
        anchors.fill: parent; anchors.margins: 8; visible: page.previewKind === "text"; contentHeight: pt.implicitHeight; clip: true
        Text { id: pt; width: parent.width; wrapMode: Text.WrapAnywhere; color: "#c9d1d9"; font.family: "monospace"; font.pixelSize: 12 }
      }
      TapHandler { onDoubleTapped: page.preview = "" }
    }
    ListView {
      width: parent.width; height: parent.height - y
      clip: true; model: fm; boundsBehavior: Flickable.StopAtBounds
      delegate: Rectangle {
        width: ListView.view.width; height: 58; color: tapF.pressed ? "#25303c" : (index % 2 ? "#14191f" : "#171d24")
        Text { anchors.left: parent.left; anchors.leftMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 120; elide: Text.ElideRight
               text: (fileIsDir ? "▸ " : "  ") + fileName; color: fileIsDir ? "#4fa3ff" : "#e8edf2"; font.pixelSize: 17 }
        Text { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
               text: fileIsDir ? "" : page.human(fileSize); color: "#8b98a7"; font.pixelSize: 13 }
        TapHandler {
          id: tapF
          onTapped: {
            if (fileIsDir) { page.preview = ""; page.dir = filePath; return }
            if (page.isImage(fileName)) { page.previewKind = "image"; page.preview = filePath }
            else if (page.isText(fileName) && fileSize < 65536) {
              page.previewKind = "text"; page.preview = filePath
              tv.path = filePath; tv.reload(); pt.text = tv.text()
            }
          }
        }
      }
    }
  }
}
