const { contextBridge, ipcRenderer } = require("electron");

// Only one process can hold /dev/video0. Default owner is the backend presence service;
// JARVIS_OVERLAY_CAMERA=1 hands it to the overlay instead (in-browser gestures).
contextBridge.exposeInMainWorld("jarvis", {
  overlayCamera: process.env.JARVIS_OVERLAY_CAMERA === "1",
  setMode: (mode) => ipcRenderer.send("mode", mode),
  setZoom: (f) => ipcRenderer.send("set-zoom", f),
  launchPhone: () => ipcRenderer.send("launch-phone"),
  quit: () => ipcRenderer.send("quit"),
  sportsToggle: (state) => ipcRenderer.send("sports_toggle", state),
  onToast: (cb) => ipcRenderer.on("toast", (_e, msg) => cb(msg)),
});
