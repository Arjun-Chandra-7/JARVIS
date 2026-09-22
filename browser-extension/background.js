// Jarvis browser control — the half that lives inside the browser.
//
// The debug port is not the only door into the Chrome DevTools Protocol; it is just the one that
// costs a restart. `chrome.debugger` is the same protocol, granted to an extension by permission
// rather than by a command-line flag, and an extension attaches to the tabs you already have
// open rather than to a fresh profile with none of them.
//
// This worker dials OUT to a loopback relay rather than listening for anything. An extension
// cannot accept connections, and it should not want to: dialling out means the only thing that
// can reach it is something already running as you on this machine.
//
//     Jarvis  ──ws──>  relay  ──ws──>  here  ──chrome.debugger──>  your open tab
//
// Load it once, unpacked, from opera://extensions (or chrome://extensions) with developer mode
// on. It survives browser restarts; it does not survive being removed.

const RELAY = "ws://127.0.0.1:9334/extension";

// Reconnect with a widening gap so a relay that is simply not running does not become a busy
// loop for as long as the browser is open.
const BACKOFF_MIN_MS = 1000;
const BACKOFF_MAX_MS = 30000;

// MV3 suspends an idle service worker. Websocket traffic resets that timer, so the relay's
// periodic tab poll doubles as the keepalive — but an alarm is the documented way to be sure,
// and 20 s is under the 30 s idle window.
const KEEPALIVE_ALARM = "jarvis-keepalive";

let socket = null;
let backoff = BACKOFF_MIN_MS;
const attached = new Set();

function connect() {
  try {
    socket = new WebSocket(RELAY);
  } catch (err) {
    schedule();
    return;
  }

  socket.onopen = () => {
    backoff = BACKOFF_MIN_MS;
    sendTabs();
  };

  socket.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    switch (msg.type) {
      case "list-tabs":
        await sendTabs();
        break;
      case "attach":
        await attach(msg);
        break;
      case "detach":
        await detach(msg);
        break;
      case "cdp":
        await forward(msg);
        break;
      // "close-tab" is the name the relay and its tests already use.
      case "close-tab":
        await closeTab(msg);
        break;
      default:
        break;
    }
  };

  socket.onclose = () => {
    socket = null;
    // Leaving a tab attached after the relay has gone would keep the browser's "being debugged"
    // banner up with nothing behind it.
    for (const tabId of [...attached]) detachTab(tabId);
    schedule();
  };

  socket.onerror = () => {
    try { socket && socket.close(); } catch { /* already closing */ }
  };
}

function schedule() {
  setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
}

function send(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

async function sendTabs() {
  const tabs = await chrome.tabs.query({});
  send({
    type: "tabs",
    tabs: tabs.map((t) => ({ id: t.id, url: t.url || "", title: t.title || "", active: t.active })),
  });
}

async function attach(msg) {
  const tabId = msg.tabId;
  if (attached.has(tabId)) {
    send({ type: "result", id: msg.id, result: {} });
    return;
  }
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
    attached.add(tabId);
    send({ type: "result", id: msg.id, result: {} });
  } catch (err) {
    // The commonest cause by far is another debugger already on this tab — DevTools being open.
    // Say which, because "could not attach" sends you looking in the wrong place.
    send({ type: "result", id: msg.id, error: String(err && err.message ? err.message : err) });
  }
}

async function detach(msg) {
  detachTab(msg.tabId);
  send({ type: "result", id: msg.id, result: {} });
}

function detachTab(tabId) {
  if (!attached.has(tabId)) return;
  attached.delete(tabId);
  chrome.debugger.detach({ tabId }).catch(() => { /* the tab may already be gone */ });
}

// Closing is `chrome.tabs.remove`, not a CDP command. Study mode needs it, and doing it through
// the debugger would mean attaching to a tab purely in order to destroy it — which raises the
// "being debugged" banner on something that is about to vanish.
async function closeTab(msg) {
  const tabId = msg.tabId;
  try {
    detachTab(tabId);
    await chrome.tabs.remove(tabId);
    send({ type: "result", id: msg.id, result: { closed: true } });
  } catch (err) {
    send({ type: "result", id: msg.id, error: String(err && err.message ? err.message : err) });
  }
  sendTabs();
}

async function forward(msg) {
  const tabId = msg.tabId;
  if (!attached.has(tabId)) {
    try {
      await chrome.debugger.attach({ tabId }, "1.3");
      attached.add(tabId);
    } catch (err) {
      send({ type: "result", id: msg.id, error: String(err && err.message ? err.message : err) });
      return;
    }
  }
  try {
    const result = await chrome.debugger.sendCommand({ tabId }, msg.method, msg.params || {});
    send({ type: "result", id: msg.id, result: result || {} });
  } catch (err) {
    send({ type: "result", id: msg.id, error: String(err && err.message ? err.message : err) });
  }
}

// CDP events travel the other way — Page.loadEventFired and friends, which browser.py waits on.
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (source.tabId === undefined) return;
  send({ type: "event", tabId: source.tabId, method, params: params || {} });
});

// Someone closing DevTools, or the tab, detaches us without asking.
chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId !== undefined) attached.delete(source.tabId);
});

// The tab list is what Jarvis picks a target from, so it has to stay current.
chrome.tabs.onUpdated.addListener(() => sendTabs());
chrome.tabs.onRemoved.addListener((tabId) => {
  attached.delete(tabId);
  sendTabs();
});
chrome.tabs.onActivated.addListener(() => sendTabs());

chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.33 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== KEEPALIVE_ALARM) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) connect();
});

connect();
