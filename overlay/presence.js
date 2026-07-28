// Presence detection — counts people in front of the webcam (MediaPipe FaceDetector),
// shows a live badge on the optics, and broadcasts the count to the ambient radar via /emit.
// Runs at ~3 fps and SMOOTHS over a short window so a single bad frame never flips the count.
// Best-effort: if the model can't load (offline), it silently disables itself.
import { FaceDetector, FilesetResolver } from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";

const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");
const video = document.getElementById("cam");
const badge = document.getElementById("presence");

let detector = null, last = -1, stable = 0, lastSent = 0;
const window_ = [];               // recent raw counts for smoothing
const WIN = 6;                    // ~2 s of samples
const MIN_CONF = 0.6;            // reject weak detections
const MIN_AREA = 0.012;         // ignore tiny/far false positives (fraction of frame)

async function init() {
  try {
    const vision = await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm");
    detector = await FaceDetector.createFromOptions(vision, {
      baseOptions: { modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" },
      runningMode: "VIDEO",
      minDetectionConfidence: MIN_CONF,
    });
    loop();
  } catch (e) {
    console.log("presence detection unavailable:", e);
  }
}

function rawCount() {
  let res;
  try { res = detector.detectForVideo(video, performance.now()); } catch (e) { return null; }
  const dets = res.detections || [];
  const vw = video.videoWidth || 1, vh = video.videoHeight || 1;
  let n = 0;
  for (const d of dets) {
    const c = d.categories && d.categories[0] ? d.categories[0].score : 1;
    const bb = d.boundingBox;
    const area = bb ? (bb.width * bb.height) / (vw * vh) : 1;
    if (c >= MIN_CONF && area >= MIN_AREA) n++;
  }
  return n;
}

// mode (most common value) of the recent window → the smoothed count
function smoothed() {
  const freq = {};
  for (const v of window_) freq[v] = (freq[v] || 0) + 1;
  let best = 0, bestN = -1;
  for (const k in freq) if (freq[k] > bestN) { bestN = freq[k]; best = +k; }
  return best;
}

function loop() {
  setTimeout(loop, 320);                       // ~3 fps
  if (!detector || video.readyState < 2) return;
  const t = video.currentTime;
  if (t === last) return;
  last = t;
  const n = rawCount();
  if (n === null) return;
  window_.push(n);
  while (window_.length > WIN) window_.shift();
  if (window_.length < 3) return;              // warm up before reporting
  const count = smoothed();

  if (badge) {
    badge.textContent = count ? (count === 1 ? "👤" : "👤" + count) : "";
    badge.classList.toggle("show", count > 0);
  }
  if (count !== stable || Date.now() - lastSent > 10000) {
    stable = count; lastSent = Date.now();
    fetch(API + "/emit", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "presence", text: String(count) }) }).catch(() => {});
  }
}

init();
