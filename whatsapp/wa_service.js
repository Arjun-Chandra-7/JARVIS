// Local WhatsApp Web bridge for Jarvis, via Baileys.
// Scan the QR once (like WhatsApp Web). Exposes a tiny localhost HTTP API:
//   GET  /status  -> {connected}
//   GET  /inbox   -> recent incoming messages [{from, name, text, ts}]
//   POST /send    -> {to, text}   (to = number with country code digits, or a full JID)

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  Browsers,
} = require("@whiskeysockets/baileys");
const qrcode = require("qrcode-terminal");
const http = require("http");
const P = require("pino");
const os = require("os");
const path = require("path");

const fs = require("fs");
const AUTH_DIR = process.env.WA_AUTH_DIR || path.join(os.homedir(), ".local/share/jarvis/whatsapp");
const PORT = parseInt(process.env.WA_PORT || "8765", 10);
const LOG = path.join(os.tmpdir(), "jarvis-wa-debug.log");
// Message text is private: the debug log is opt-in and owner-only, never on by default.
const DEBUG = process.env.WA_DEBUG === "1";
const dbg = (s) => { if (!DEBUG) return; try { fs.appendFileSync(LOG, `[${new Date().toISOString()}] ${s}\n`, { mode: 0o600 }); } catch {} };

function extractText(msg) {
  if (!msg) return "";
  return (
    msg.conversation ||
    msg.extendedTextMessage?.text ||
    msg.imageMessage?.caption ||
    msg.videoMessage?.caption ||
    msg.documentMessage?.caption ||
    msg.ephemeralMessage?.message?.conversation ||
    msg.ephemeralMessage?.message?.extendedTextMessage?.text ||
    msg.viewOnceMessage?.message?.extendedTextMessage?.text ||
    msg.buttonsResponseMessage?.selectedDisplayText ||
    msg.listResponseMessage?.title ||
    ""
  );
}

let sock = null;
let connected = false;
const inbox = []; // recent incoming messages
const contacts = new Map(); // jid -> best known name (for resolving "message <name>")
const history = []; // private local mirror, exposed only over localhost /chats
const HISTORY_FILE = path.join(AUTH_DIR, "history.json");
const MAX_HISTORY = 5000;

function messageId(m) {
  return String(m?.key?.id || `${m?.key?.remoteJid || ""}|${m?.messageTimestamp || Date.now()}`);
}
function chatFlags(jid) {
  const value = String(jid || "");
  return { isGroup: value.endsWith("@g.us"), isNewsletter: value.includes("@newsletter"), isStatus: value === "status@broadcast" };
}
function normaliseMessage(m) {
  const from = m?.key?.remoteJid || "";
  const flags = chatFlags(from);
  const rawTs = Number(m?.messageTimestamp || 0);
  return {
    // On an outgoing message pushName is the *owner's* name, not the chat's, so it names nobody here.
    id: messageId(m), from, name: (!m?.key?.fromMe && m?.pushName) || contacts.get(from) || from,
    text: extractText(m?.message),
    ts: rawTs > 100000000000 ? rawTs : (rawTs ? rawTs * 1000 : Date.now()),
    fromMe: !!m?.key?.fromMe, ...flags,
  };
}
function loadHistory() {
  try {
    const value = JSON.parse(fs.readFileSync(HISTORY_FILE, "utf8"));
    if (Array.isArray(value)) history.push(...value.slice(-MAX_HISTORY));
  } catch {}
}
let historyTimer = null;
function saveHistory() {
  clearTimeout(historyTimer);
  historyTimer = setTimeout(() => {
    try {
      fs.mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });
      fs.writeFileSync(HISTORY_FILE, JSON.stringify(history), { mode: 0o600 });
    } catch {}
  }, 1500);
}
function recordHistory(m) {
  const item = normaliseMessage(m);
  if (!item.id || history.some((x) => x.id === item.id)) return item;
  history.push(item);
  if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY);
  saveHistory();
  return item;
}
loadHistory();

// --- persist contacts across restarts (WhatsApp only re-sends the address book occasionally) ---
const CONTACTS_FILE = path.join(AUTH_DIR, "contacts.json");
function loadContacts() {
  try {
    const obj = JSON.parse(fs.readFileSync(CONTACTS_FILE, "utf8"));
    for (const [jid, name] of Object.entries(obj)) contacts.set(jid, name);
    console.log(`Loaded ${contacts.size} saved contacts.`);
  } catch {}
}
let saveTimer = null;
function saveContacts() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      fs.mkdirSync(AUTH_DIR, { recursive: true });
      fs.writeFileSync(CONTACTS_FILE, JSON.stringify(Object.fromEntries(contacts)));
    } catch {}
  }, 1500);
}

// Names the owner saved in their address book. A profile name someone chose for themselves
// (pushName) never overwrites one of these: Papa stays "Papa" after he changes his profile.
const SAVED_FILE = path.join(AUTH_DIR, "contacts-saved.json");
const savedNames = new Set();
try { for (const j of JSON.parse(fs.readFileSync(SAVED_FILE, "utf8"))) savedNames.add(j); } catch {}
function recordSavedContact(c) {
  if (c?.name && c?.id) {
    savedNames.add(c.id);
    try { fs.writeFileSync(SAVED_FILE, JSON.stringify([...savedNames]), { mode: 0o600 }); } catch {}
    return recordContact(c.id, c.name, true);
  }
  return recordContact(c?.id, c?.notify || c?.verifiedName);
}

function recordContact(jid, name, fromBook = false) {
  if (!jid || !name) return;
  if (!fromBook && savedNames.has(jid)) return;
  if (jid.includes("@g.us") || jid.includes("@newsletter") || jid.includes("broadcast")) return;
  const clean = String(name).trim();
  if (!clean || /^\d+$/.test(clean) || clean.includes("@")) return; // skip numbers / raw jids
  if (contacts.get(jid) !== clean) {
    contacts.set(jid, clean);
    saveContacts();
  }
}
loadContacts();

// Earlier builds recorded pushName on outgoing messages too, which labelled every chat the owner
// wrote to with the owner's own name. Drop those, keeping the owner's own chat.
function forgetOwnName() {
  const me = sock?.user;
  if (!me?.id) return;
  const meJid = me.id.split(":")[0] + "@s.whatsapp.net";
  const own = String(me.name || me.notify || "").trim();
  if (!own) return;
  let dropped = 0;
  for (const [jid, name] of contacts) {
    if (jid !== meJid && name === own) { contacts.delete(jid); dropped++; }
  }
  if (dropped) { console.log(`Forgot ${dropped} chats mislabelled with the owner's name.`); saveContacts(); }
}

// Exactly these origins may drive the bridge from a browser. A prefix test would let
// http://127.0.0.1.attacker.example through.
const TRUSTED_ORIGINS = new Set([
  "http://127.0.0.1:8770", "http://localhost:8770",
]);
// A DNS-rebinding page reaches 127.0.0.1 under its own hostname; the Host header gives it away.
function trustedHost(host) {
  const h = String(host || "").toLowerCase();
  return h === `127.0.0.1:${PORT}` || h === `localhost:${PORT}` || h === "127.0.0.1" || h === "localhost";
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  console.log("Using WhatsApp Web version", version.join("."));

  sock = makeWASocket({
    version,
    auth: state,
    browser: Browsers.ubuntu("Chrome"),
    logger: P({ level: "silent" }),
    syncFullHistory: true,   // pull the address book + chat history so we can resolve names
    markOnlineOnConnect: false,
  });
  sock.ev.on("creds.update", saveCreds);

  // Build the address book so we can resolve "message <name>" to the right JID.
  const ingest = (list) => (list || []).forEach(recordSavedContact);
  sock.ev.on("contacts.upsert", ingest);
  sock.ev.on("contacts.update", ingest);
  sock.ev.on("messaging-history.set", ({ contacts: cs, messages }) => {
    ingest(cs);
    (messages || []).forEach((m) => { if (!m?.key?.fromMe) recordContact(m?.key?.remoteJid, m?.pushName); recordHistory(m); }); // import-only local mirror; never sends or deletes
  });

  sock.ev.on("connection.update", (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr) {
      console.log("\nScan this QR in WhatsApp → Settings → Linked devices → Link a device:\n");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      connected = true;
      forgetOwnName();
      console.log("WhatsApp connected.");
    }
    if (connection === "close") {
      connected = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      console.log("Connection closed. status:", code, "-", lastDisconnect?.error?.message || "");
      const stop = [DisconnectReason.loggedOut, 401, 403].includes(code);
      if (!stop) {
        setTimeout(start, 3000);
      } else {
        console.log(`Not reconnecting. Delete ${AUTH_DIR} and re-run to re-link.`);
      }
    }
  });

  sock.ev.on("messages.upsert", ({ messages, type }) => {
    dbg(`upsert type=${type} count=${messages?.length}`);
    if (type !== "notify") return;
    for (const m of messages) {
      const text = extractText(m.message);
      dbg(`  from=${m.key.remoteJid} fromMe=${m.key.fromMe} keys=${Object.keys(m.message || {})} text=${JSON.stringify(text)}`);
      if (!m.key.fromMe) recordContact(m.key.remoteJid, m.pushName);
      const item = recordHistory(m);
      const meJid = sock.user.id.split(':')[0] + '@s.whatsapp.net';
      if (m.key.fromMe && m.key.remoteJid !== meJid) continue;
      if (!text) continue;
      inbox.push(item);
      if (inbox.length > 100) inbox.shift();
      dbg(`  -> stored (inbox size ${inbox.length})`);
    }
  });
}

async function sendMessage(to, text) {
  let jid;
  if (String(to).includes("@")) {
    jid = to; // already a JID (e.g. from the inbox)
  } else {
    const num = String(to).replace(/\D/g, "");
    if (!num) throw new Error("no phone number given");
    const found = await sock.onWhatsApp(num); // validate + resolve the real JID
    if (!found || !found[0] || !found[0].exists) {
      throw new Error("+" + num + " is not on WhatsApp");
    }
    jid = found[0].jid;
  }
  if (!connected) throw new Error("WhatsApp is not connected");
  const sent = await sock.sendMessage(jid, { text });
  // The server assigns the message a key once it accepts it; without one, it did not go out.
  if (!sent?.key?.id) throw new Error("WhatsApp did not acknowledge the message");
  return { jid, id: sent.key.id };
}

http
  .createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    const origin = req.headers.origin;
    if (!trustedHost(req.headers.host) || (origin && !TRUSTED_ORIGINS.has(origin))) {
      res.statusCode = 403;
      return res.end(JSON.stringify({ error: "Unauthorized cross-origin request" }));
    }
    if (req.url === "/status") return res.end(JSON.stringify({ connected }));
    if (req.url === "/inbox") return res.end(JSON.stringify(inbox.slice(-30)));
    if (req.url.startsWith("/chats")) {
      const url = new URL(req.url, "http://127.0.0.1");
      const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit")) || 2000, MAX_HISTORY));
      return res.end(JSON.stringify(history.slice(-limit)));
    }
    if (req.url === "/contacts") {
      return res.end(JSON.stringify([...contacts.entries()].map(([jid, name]) => ({ jid, name }))));
    }
    if (req.url.startsWith("/resolve")) {
      // /resolve?name=xxx  → exact/substring matches from the address book
      const q = decodeURIComponent((req.url.split("name=")[1] || "")).trim().toLowerCase();
      const matches = [...contacts.entries()]
        .filter(([, name]) => q && name.toLowerCase().includes(q))
        .map(([jid, name]) => ({ jid, name }));
      // exact matches first
      matches.sort((a, b) => (a.name.toLowerCase() === q ? -1 : 0) - (b.name.toLowerCase() === q ? -1 : 0));
      return res.end(JSON.stringify(matches.slice(0, 8)));
    }
    if (req.url === "/send" && req.method === "POST") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", async () => {
        try {
          const { to, text } = JSON.parse(body);
          const { jid, id } = await sendMessage(to, text);
          res.end(JSON.stringify({ ok: true, jid, id }));
        } catch (e) {
          res.statusCode = 500;
          res.end(JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) }));
        }
      });
      return;
    }
    res.statusCode = 404;
    res.end(JSON.stringify({ error: "not found" }));
  })
  .listen(PORT, "127.0.0.1", () => console.log(`Jarvis WhatsApp bridge on 127.0.0.1:${PORT}`));

start();
