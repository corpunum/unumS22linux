import QtQuick
import Quickshell.Io

// Rear-camera still via s22-touchd -> s22-camera (raw Bayer, no ISP).
// Experimental: frames were black on 2026-10-08 (exposure path unproven).
Item {
  id: page
  property var shell: null
  property var res: ({})
  property int stamp: 0

  FileView { id: resFile; path: "/run/s22-touch/camera.json"; blockLoading: true }
  function readRes() {
    resFile.reload()
    try { page.res = JSON.parse(resFile.text()) } catch (e) { page.res = {} }
    page.stamp = Date.now()
  }
  onShellChanged: readRes()
  Timer { interval: 2000; repeat: true; running: page.res.state === "running"; onTriggered: page.readRes() }

  Process { id: capProc; command: ["s22-ui", "camera"]; onExited: page.readRes() }

  Column {
    anchors.fill: parent
    spacing: 12
    Rectangle {
      width: parent.width; height: width * 0.75; radius: 12; color: "#05070a"; clip: true
      Image {
        anchors.fill: parent; fillMode: Image.PreserveAspectFit; cache: false; asynchronous: true
        source: page.res.path ? "file://" + page.res.path + "?" + page.stamp : ""
      }
      Text { anchors.centerIn: parent; visible: !page.res.path; text: "No capture yet"; color: "#5c6773"; font.pixelSize: 17 }
    }
    Text {
      width: parent.width; wrapMode: Text.Wrap; color: "#c9d1d9"; font.pixelSize: 15
      text: page.res.state === "running" ? "Capturing… (the sensor firmware fallback can take about 60 s)"
          : page.res.state === "done" ? "Last capture: " + (page.res.rc === 0 ? "ok" : "failed (rc " + page.res.rc + ")")
          : page.res.state === "failed" ? "Capture failed: " + page.res.error : "Raw still from the rear camera, developed on the phone."
    }
    Btn {
      text: "Capture still"; holdToConfirm: true; width: parent.width; height: 64; tint: "#4a3a12"
      enabledBtn: page.res.state !== "running"
      onClicked: { page.res = { state: "running" }; capProc.running = true }
    }
    Text {
      width: parent.width; wrapMode: Text.Wrap; color: "#8b98a7"; font.pixelSize: 13
      text: "Experimental. Earlier frames were black (the exposure path is unproven). Point the rear camera at a lit scene."
    }
  }
}
