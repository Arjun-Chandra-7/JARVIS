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
const dbg = (s) => { try { fs.appendFileSync(LOG, `[${new Date().toISOString()}] ${s}\n`); } catch {} };

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

function recordContact(jid, name) {
  if (!jid || !name) return;
  if (jid.includes("@g.us") || jid.includes("@newsletter") || jid.includes("broadcast")) return;
  const clean = String(name).trim();
  if (!clean || /^\d+$/.test(clean) || clean.includes("@")) return; // skip numbers / raw jids
  if (contacts.get(jid) !== clean) {
    contacts.set(jid, clean);
    saveContacts();
  }
}
loadContacts();

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
  const ingest = (list) => (list || []).forEach((c) => recordContact(c.id, c.name || c.notify || c.verifiedName));
  sock.ev.on("contacts.upsert", ingest);
  sock.ev.on("contacts.update", ingest);
  sock.ev.on("messaging-history.set", ({ contacts: cs }) => ingest(cs));

  sock.ev.on("connection.update", (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr) {
      console.log("\nScan this QR in WhatsApp → Settings → Linked devices → Link a device:\n");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      connected = true;
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
      recordContact(m.key.remoteJid, m.pushName);
      if (m.key.fromMe) continue;
      if (!text) continue;
      inbox.push({ from: m.key.remoteJid, name: m.pushName || m.key.remoteJid, text, ts: Date.now() });
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
  await sock.sendMessage(jid, { text });
  return jid;
}

http
  .createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    const origin = req.headers.origin;
    if (origin && !origin.startsWith("http://127.0.0.1") && !origin.startsWith("http://localhost")) {
      res.statusCode = 403;
      return res.end(JSON.stringify({ error: "Unauthorized cross-origin request" }));
    }
    if (req.url === "/status") return res.end(JSON.stringify({ connected }));
    if (req.url === "/inbox") return res.end(JSON.stringify(inbox.slice(-30)));
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
          const jid = await sendMessage(to, text);
          res.end(JSON.stringify({ ok: true, jid }));
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
