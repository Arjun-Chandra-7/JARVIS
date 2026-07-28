// Presence detection — counts people in front of the webcam (MediaPipe FaceDetector),
// shows a live badge on the optics, and broadcasts the count to the ambient radar via /emit.
// Runs at ~2 fps so it never competes with the hand tracker for the GPU. Best-effort: if the
// model can't load (offline), it silently disables itself.
import { FaceDetector, FilesetResolver } from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";

const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");
const video = document.getElementById("cam");
const badge = document.getElementById("presence");

let detector = null, last = -1, lastCount = -1, lastSent = 0;

async function init() {
  try {
    const vision = await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm");
    detector = await FaceDetector.createFromOptions(vision, {
      baseOptions: { modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" },
      runningMode: "VIDEO",
      minDetectionConfidence: 0.5,
    });
    loop();
  } catch (e) {
    console.log("presence detection unavailable:", e);
  }
}

function loop() {
  setTimeout(loop, 450);                       // ~2 fps — light on the GPU
  if (!detector || video.readyState < 2) return;
  const t = video.currentTime;
  if (t === last) return;
  last = t;
  let count = 0;
  try { count = (detector.detectForVideo(video, performance.now()).detections || []).length; } catch (e) { return; }
  if (badge) {
    badge.textContent = count ? "👤" + count : "";
    badge.classList.toggle("show", count > 0);
  }
  if (count !== lastCount || Date.now() - lastSent > 8000) {
    lastCount = count; lastSent = Date.now();
    fetch(API + "/emit", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "presence", text: String(count) }) }).catch(() => {});
  }
}

init();
