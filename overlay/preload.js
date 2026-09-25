// The only bridge between the page and the main process. Everything here is a named, argument-
// checked message — the renderer never gets `ipcRenderer`, `require`, or a way to invoke an
// arbitrary channel, so a bug (or injected text) in the page cannot reach the shell.
const { contextBridge, ipcRenderer } = require("electron");

const FORMS = ["pill", "conversation", "workspace", "ironman"];
const SHORTCUTS = ["toggle", "workspace", "hide"];

contextBridge.exposeInMainWorld("jarvis", {
  // --- window shape -------------------------------------------------------
  setForm: (name) => {
    if (FORMS.includes(name)) ipcRenderer.send("form", name);
  },
  // Ask the pill to make room for an answer, in pixels of extra height. Clamped here as well as
  // in the main process: the renderer is the least trusted side of this boundary and a number it
  // sends should not be able to become a window of any size it likes.
  growPill: (extra) => {
    const px = Number(extra);
    if (Number.isFinite(px) && px >= 0 && px <= 160) ipcRenderer.send("grow-pill", Math.round(px));
  },
  // Let the desktop underneath have the pointer, except where there is something to press.
  setClickThrough: (through) => ipcRenderer.send("click-through", Boolean(through)),
  hide: () => ipcRenderer.send("hide"),
  show: () => ipcRenderer.send("show"),
  focusWindow: () => ipcRenderer.send("focus-window"),
  quit: () => ipcRenderer.send("quit"),

  // --- settings -----------------------------------------------------------
  getState: () => ipcRenderer.invoke("get-state"),
  setShortcut: (which, accelerator) => {
    if (!SHORTCUTS.includes(which)) {
      return Promise.resolve({ ok: false, error: "unknown shortcut" });
    }
    if (typeof accelerator !== "string" || accelerator.length > 64) {
      return Promise.resolve({ ok: false, error: "invalid accelerator" });
    }
    return ipcRenderer.invoke("set-shortcut", which, accelerator);
  },

  // --- actions ------------------------------------------------------------
  launchPhone: () => ipcRenderer.send("launch-phone"),
  openExternal: (url) => {
    if (typeof url === "string" && /^https?:\/\//.test(url)) {
      ipcRenderer.send("open-external", url);
    }
  },
  // Open a picture Jarvis generated. A separate channel rather than letting openExternal take
  // file:// URLs: that one is for the web, and widening it to the filesystem would let any link
  // in any reply open any file on this machine. The main process checks the path again and only
  // accepts one inside the folder Jarvis writes pictures to.
  openPicture: (path) => {
    if (typeof path === "string" && path.includes("/Pictures/Jarvis/") && !path.includes("..")) {
      ipcRenderer.send("open-picture", path);
    }
  },

  // --- main -> renderer ---------------------------------------------------
  onToast: (cb) => ipcRenderer.on("toast", (_e, msg) => cb(String(msg))),
  onForm: (cb) => ipcRenderer.on("form", (_e, name) => cb(String(name))),
  onVisibility: (cb) => ipcRenderer.on("visibility", (_e, vis) => cb(!!vis)),
});
