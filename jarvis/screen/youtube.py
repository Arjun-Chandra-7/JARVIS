"""The YouTube player, read as data rather than as pixels.

"Explain what he just said" needs three facts: which video, where in it, and the words around
that point. All three are in the page — the player knows its time and state, and the player
response lists the caption tracks — so they are read from there. A screenshot is only for when
the page has no words to give (no captions, a slide with nothing spoken over it).

Works through ``page.Page``, so the same code serves Zen/Firefox (Marionette) and Chromium (CDP).
Every script reaches the page's own objects through ``wrappedJSObject`` when it exists: Firefox
runs automation scripts behind X-ray wrappers that hide page-defined globals like the player.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

_STATE = r"""
const w = window.wrappedJSObject || window;
const d = w.document;
const host = location.hostname;
if (!/(^|\.)youtube\.com$/.test(host) && host !== "youtu.be") return {youtube: false, url: location.href, title: document.title};
const v = d.querySelector("video.html5-main-video") || d.querySelector("video");
const player = d.querySelector("#movie_player");
let pr = null;
try { pr = player && player.getPlayerResponse ? player.getPlayerResponse() : null; } catch (e) {}
if (!pr) { try { pr = w.ytInitialPlayerResponse || null; } catch (e) {} }
const details = (pr && pr.videoDetails) || {};
const urlId = new URL(location.href).searchParams.get("v") || (location.pathname.match(/\/(?:shorts|live)\/([\w-]+)/) || [])[1] || "";
const fresh = !details.videoId || !urlId || details.videoId === urlId;
let tracks = [];
try {
  tracks = ((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks || [];
  tracks = tracks.map(t => ({url: t.baseUrl, lang: t.languageCode, kind: t.kind || "",
                             name: (t.name && (t.name.simpleText || (t.name.runs || []).map(r => r.text).join(""))) || ""}));
} catch (e) { tracks = []; }
const text = sel => { const el = d.querySelector(sel); return el ? el.textContent.trim() : ""; };
return {
  youtube: true, url: location.href, videoId: urlId, fresh,
  title: (fresh && details.title) || text("h1.ytd-watch-metadata") || document.title.replace(/ - YouTube$/, ""),
  channel: (fresh && details.author) || text("#owner #channel-name a"),
  description: ((fresh && details.shortDescription) || "").slice(0, 600),
  time: v ? v.currentTime : null, duration: v ? v.duration : null,
  paused: v ? v.paused : null, ended: v ? v.ended : null, rate: v ? v.playbackRate : null,
  hasVideo: !!v, chapter: text(".ytp-chapter-title-content"),
  liveCaption: Array.from(d.querySelectorAll(".ytp-caption-segment")).map(s => s.textContent).join(" ").trim(),
  tracks: fresh ? tracks : [],
  ad: !!d.querySelector(".ad-showing"),
  // YouTube's own error overlay: "Something went wrong", "Sign in to confirm you're not a bot".
  playerError: (() => { const e = d.querySelector(".ytp-error:not([style*='display: none']) .ytp-error-content-wrap-reason, .ytp-error-content-wrap-reason");
                        return e && e.offsetParent !== null ? e.textContent.replace(/\s+/g, " ").trim().slice(0, 160) : ""; })(),
};
"""

# The caption track, fetched from inside the page so it carries the page's own session.
#
# A caption URL from the player response is no longer enough: YouTube answers it with an empty
# body unless it carries a proof-of-origin token ("pot"), which only the player itself obtains.
# So the player's own request is reused — it is in the page's resource timings whenever captions
# have been shown. If they have not, captions are switched on long enough for the player to
# fetch them, then switched back as they were.
_FETCH_TRACK = r"""
const [fallbackUrl, videoId] = arguments;
const w = window.wrappedJSObject || window;
const d = w.document;
const mine = () => performance.getEntriesByType("resource").map(e => e.name)
  .filter(n => n.includes("/api/timedtext") && n.includes("pot=") && (!videoId || n.includes("v=" + videoId)));
let urls = mine();
let toggled = false;
const button = d.querySelector(".ytp-subtitles-button");
if (!urls.length && button && button.getAttribute("aria-pressed") !== "true") {
  button.click(); toggled = true;
  for (let i = 0; i < 25 && !urls.length; i++) { await new Promise(r => setTimeout(r, 200)); urls = mine(); }
}
if (toggled && button.getAttribute("aria-pressed") === "true") button.click();
const url = urls.length ? urls[urls.length - 1] : fallbackUrl;
if (!url) return {ok: false, reason: "no caption request to reuse"};
try {
  const withFmt = /[?&]fmt=/.test(url) ? url.replace(/([?&]fmt=)[^&]*/, "$1json3") : url + "&fmt=json3";
  const r = await fetch(withFmt, {credentials: "include"});
  if (!r.ok) return {ok: false, reason: "HTTP " + r.status};
  const body = await r.text();
  if (!body) return {ok: false, reason: urls.length ? "empty" : "empty (no player token)"};
  const j = JSON.parse(body);
  const segs = (j.events || []).filter(e => e.segs).map(e => ({
    start: (e.tStartMs || 0) / 1000, dur: (e.dDurationMs || 0) / 1000,
    text: e.segs.map(s => s.utf8 || "").join("").replace(/\s+/g, " ").trim()})).filter(s => s.text);
  return {ok: true, segments: segs, reused: urls.length > 0};
} catch (e) { return {ok: false, reason: String(e)}; }
"""

# The transcript panel YouTube shows under the description. Opened if it is not already.
_PANEL = r"""
const w = window.wrappedJSObject || window;
const d = w.document;
const read = () => Array.from(d.querySelectorAll("ytd-transcript-segment-renderer")).map(s => ({
  stamp: ((s.querySelector(".segment-timestamp") || {}).textContent || "").trim(),
  text: ((s.querySelector(".segment-text") || {}).textContent || "").replace(/\s+/g, " ").trim()}));
let rows = read();
if (!rows.length) {
  const button = d.querySelector("ytd-video-description-transcript-section-renderer button") ||
                 Array.from(d.querySelectorAll("button")).find(b => /transcript/i.test(b.getAttribute("aria-label") || b.textContent || ""));
  if (!button) return {ok: false, reason: "no transcript button"};
  button.click();
  for (let i = 0; i < 30 && !rows.length; i++) { await new Promise(r => setTimeout(r, 200)); rows = read(); }
}
return rows.length ? {ok: true, rows} : {ok: false, reason: "transcript panel stayed empty"};
"""

_CONTROL = r"""
const [action] = arguments;
const w = window.wrappedJSObject || window;
const v = w.document.querySelector("video.html5-main-video") || w.document.querySelector("video");
if (!v) return {ok: false, reason: "No video on this page."};
if (action === "pause") v.pause();
if (action === "play") {
  // play() can wait indefinitely while the video buffers; what matters is the state it reaches.
  let error = null;
  const started = v.play();
  if (started && started.catch) started.catch(e => { error = String(e); });
  for (let i = 0; i < 15 && v.paused && !error; i++) await new Promise(r => setTimeout(r, 200));
  if (error) return {ok: false, reason: error};
}
const t0 = v.currentTime;
await new Promise(r => setTimeout(r, action === "play" ? 1200 : 250));
return {ok: true, paused: v.paused, time: v.currentTime, advanced: v.currentTime > t0 + 0.2};
"""


@dataclass
class Segment:
    start: float
    dur: float
    text: str

    @property
    def end(self) -> float:
        return self.start + (self.dur or 0.0)


@dataclass
class PlayerState:
    youtube: bool
    url: str = ""
    title: str = ""
    channel: str = ""
    description: str = ""
    video_id: str = ""
    time: Optional[float] = None
    duration: Optional[float] = None
    paused: Optional[bool] = None
    chapter: str = ""
    live_caption: str = ""
    ad: bool = False
    player_error: str = ""
    tracks: list[dict] = field(default_factory=list)
    fresh: bool = True        # the player's details belong to the video in the address bar
    read_at: float = 0.0      # when this was read from the page (time.time())

    @classmethod
    def from_page(cls, raw: dict) -> "PlayerState":
        raw = raw or {}
        return cls(youtube=bool(raw.get("youtube")), url=raw.get("url", ""), title=raw.get("title", ""),
                   channel=raw.get("channel", ""), description=raw.get("description", ""),
                   video_id=raw.get("videoId", ""), time=raw.get("time"), duration=raw.get("duration"),
                   paused=raw.get("paused"), chapter=raw.get("chapter", ""),
                   live_caption=raw.get("liveCaption", ""), ad=bool(raw.get("ad")),
                   player_error=raw.get("playerError", "") or "",
                   tracks=list(raw.get("tracks") or []),
                   fresh=raw.get("fresh", True) is not False, read_at=time.time())


def clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def parse_stamp(stamp: str) -> Optional[float]:
    parts = [p for p in re.split(r"[:.]", (stamp or "").strip()) if p.isdigit()]
    if not parts:
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return float(total)


def pick_track(tracks: list[dict], prefer: tuple[str, ...] = ("en", "hi")) -> Optional[dict]:
    """Human captions in a preferred language, then any human captions, then auto-generated."""
    def rank(t):
        lang = (t.get("lang") or "").split("-")[0]
        human = t.get("kind") != "asr"
        return (0 if human else 1, prefer.index(lang) if lang in prefer else len(prefer))
    return min(tracks, key=rank) if tracks else None


def window(segments: list[Segment], at: float, before: float = 30.0, after: float = 5.0) -> list[Segment]:
    lo, hi = at - before, at + after
    return [s for s in segments if s.end > lo and s.start <= hi]


def as_text(segments: list[Segment]) -> str:
    return "\n".join(f"[{clock(s.start)}] {s.text}" for s in segments)


class YouTube:
    def __init__(self, page) -> None:
        self.page = page

    async def state(self) -> PlayerState:
        return PlayerState.from_page(await self.page.run(_STATE))

    async def transcript(self, state: PlayerState) -> tuple[list[Segment], str]:
        """(segments, where they came from). Empty when the video has no words to give."""
        track = pick_track(state.tracks)
        if track and track.get("url"):
            got = await self.page.run(_FETCH_TRACK, [track["url"], state.video_id], timeout=25)
            if isinstance(got, dict) and got.get("ok") and got.get("segments"):
                kind = "auto-generated captions" if track.get("kind") == "asr" else "captions"
                return [Segment(s["start"], s["dur"], s["text"]) for s in got["segments"]], \
                    f"{kind} ({track.get('lang') or '?'})"
        panel = await self.page.run(_PANEL, timeout=15)
        if isinstance(panel, dict) and panel.get("ok"):
            rows = [(parse_stamp(r["stamp"]), r["text"]) for r in panel["rows"] if r.get("text")]
            rows = [(t, x) for t, x in rows if t is not None]
            segs = [Segment(t, (rows[i + 1][0] - t) if i + 1 < len(rows) else 5.0, x)
                    for i, (t, x) in enumerate(rows)]
            if segs:
                return segs, "the transcript panel"
        return [], ""

    async def control(self, action: str) -> dict:
        """pause / play, then the player's state *afterwards* — the verification."""
        reply = await self.page.run(_CONTROL, [action])
        if not isinstance(reply, dict) or not reply.get("ok"):
            return {"ok": False, "verified": False, "reason": (reply or {}).get("reason", "no reply")}
        if action == "pause":
            verified = reply.get("paused") is True
        else:
            # Not paused is not the same as playing: a buffering or undecodable video reports
            # paused=false and never moves. Playing means the clock advanced.
            verified = reply.get("paused") is False and bool(reply.get("advanced"))
        return {"ok": True, "verified": verified, "paused": reply.get("paused"), "time": reply.get("time"),
                "reason": "" if verified else ("it isn't advancing — still buffering?" if action == "play" else "")}
