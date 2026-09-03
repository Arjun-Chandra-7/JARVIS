const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvis", {
  setMode: (mode) => ipcRenderer.send("mode", mode),
  setZoom: (f) => ipcRenderer.send("set-zoom", f),
  launchPhone: () => ipcRenderer.send("launch-phone"),
  quit: () => ipcRenderer.send("quit"),
  sportsToggle: (state) => ipcRenderer.send("sports_toggle", state),
  onToast: (cb) => ipcRenderer.on("toast", (_e, msg) => cb(msg)),
});
