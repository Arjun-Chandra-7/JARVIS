// The teaching layer's only bridge to the main process. Named channels, checked arguments: the
// page can report what it did and say whether pen mode is on, and nothing else.
const { contextBridge, ipcRenderer } = require("electron");

const EVENT_TYPES = ["ready", "frame", "idle", "error", "rejected", "stale", "quality", "anim"];

contextBridge.exposeInMainWorld("teach", {
  spec: () => ipcRenderer.invoke("teach-spec"),
  onBatch: (cb) => ipcRenderer.on("teach-batch", (_e, batch, recvAt) => cb(batch, Number(recvAt) || 0)),
  onControl: (cb) => ipcRenderer.on("teach-control", (_e, action, arg) => cb(String(action), arg)),
  onGeometry: (cb) => ipcRenderer.on("teach-geometry", (_e, g) => cb(g)),
  report: (evt) => {
    if (evt && EVENT_TYPES.includes(evt.type)) ipcRenderer.send("teach-event", evt);
  },
  pen: (on) => ipcRenderer.send("teach-pen", Boolean(on)),
});
