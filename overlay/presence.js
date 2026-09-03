// Presence detection — detects real people via webcam using MediaPipe FaceDetector.
// Uses the LOCALLY INSTALLED @mediapipe/tasks-vision (not CDN) so it works offline
// and doesn't break when CDN versions rotate.
//
// Pipeline:  webcam video → MediaPipe face detection → face positions + areas
//          → POST /emit {kind:"presence_data"} → ambient overlay radar
//
// Also sends status events so the radar shows what's happening (loading/scanning/error).

import { FaceDetector, FilesetResolver } from "./node_modules/@mediapipe/tasks-vision/vision_bundle.mjs";

const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");
const video = document.getElementById("cam");
const badge = document.getElementById("presence");

let detector = null;
let lastVideoTime = -1;
let initAttempts = 0;
const MAX_INIT_RETRIES = 3;

// Detection tuning
const MIN_CONF = 0.55;        // confidence threshold (slightly lower = catch more faces)
const MIN_AREA = 0.008;       // minimum face area as fraction of frame (reject tiny noise)
const LOOP_MS = 120;          // detection interval (~8 fps, keeps CPU low)

// Smoothing: keep last N frames to avoid flicker
const frameBuffer = [];
const BUFFER_SIZE = 4;

// ---- status reporting (so the ambient radar shows what's happening) ----
let lastStatus = "";
function sendStatus(status) {
  if (status === lastStatus) return;
  lastStatus = status;
  fetch(API + "/emit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "presence_status", text: status })
  }).catch(() => {});
}

// ---- webcam readiness ----
function waitForCamera() {
  return new Promise((resolve, reject) => {
    if (video.readyState >= 2) { resolve(); return; }
    let waited = 0;
    const check = setInterval(() => {
      waited += 200;
      if (video.readyState >= 2) { clearInterval(check); resolve(); return; }
      if (waited > 15000) { clearInterval(check); reject(new Error("camera timeout — no video after 15s")); }
    }, 200);
  });
}

// ---- initialise MediaPipe ----
async function init() {
  sendStatus("loading");
  console.log("[presence] initialising face detection…");

  try {
    // Check webcam is actually streaming
    if (!video || !video.srcObject) {
      // Wait a bit — renderer.js might not have called getUserMedia yet
      console.log("[presence] waiting for webcam stream…");
      await new Promise(r => setTimeout(r, 2000));
      if (!video || !video.srcObject) {
        console.warn("[presence] no webcam stream available — face detection disabled");
        sendStatus("no_camera");
        return;
      }
    }

    await waitForCamera();
    console.log("[presence] camera ready:", video.videoWidth, "x", video.videoHeight);

    // Load WASM from local node_modules (not CDN — reliable, offline-safe)
    const wasmPath = "./node_modules/@mediapipe/tasks-vision/wasm";
    const vision = await FilesetResolver.forVisionTasks(wasmPath);

    detector = await FaceDetector.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
        delegate: "GPU"    // try GPU acceleration first, falls back to CPU
      },
      runningMode: "VIDEO",
      minDetectionConfidence: MIN_CONF,
    });

    console.log("[presence] ✓ face detector ready — starting detection loop");
    sendStatus("scanning");
    loop();

  } catch (e) {
    console.error("[presence] init failed:", e);
    initAttempts++;
    if (initAttempts < MAX_INIT_RETRIES) {
      console.log(`[presence] retrying in 5s (attempt ${initAttempts}/${MAX_INIT_RETRIES})…`);
      sendStatus("retrying");
      setTimeout(init, 5000);
    } else {
      console.error("[presence] gave up after", MAX_INIT_RETRIES, "attempts");
      sendStatus("error");
    }
  }
}

// ---- detect faces in the current video frame ----
function detectFaces() {
  if (!detector || video.readyState < 2) return null;

  const t = video.currentTime;
  if (t === lastVideoTime) return null;  // same frame, skip
  lastVideoTime = t;

  let result;
  try {
    result = detector.detectForVideo(video, performance.now());
  } catch (e) {
    console.warn("[presence] detection error:", e);
    return null;
  }

  const dets = result.detections || [];
  const vw = video.videoWidth || 1;
  const vh = video.videoHeight || 1;

  const faces = [];
  for (const d of dets) {
    const score = d.categories?.[0]?.score ?? 0;
    const bb = d.boundingBox;
    if (!bb) continue;

    const area = (bb.width * bb.height) / (vw * vh);

    if (score >= MIN_CONF && area >= MIN_AREA) {
      // Normalised center X (0 = left edge, 1 = right edge)
      const cx = (bb.originX + bb.width / 2) / vw;
      // Normalised center Y (0 = top, 1 = bottom)  
      const cy = (bb.originY + bb.height / 2) / vh;

      faces.push({
        x: cx,
        y: cy,
        area: area,
        score: Math.round(score * 100) / 100
      });
    }
  }

  return faces;
}

// ---- smooth across frames to avoid flicker ----
function getSmoothedFaces() {
  if (frameBuffer.length === 0) return [];

  // Use the latest frame but only if at least 2 of the last N frames agree on the count
  const latest = frameBuffer[frameBuffer.length - 1];
  const latestCount = latest.length;

  // Count how many recent frames have the same count (±1)
  let agreement = 0;
  for (const f of frameBuffer) {
    if (Math.abs(f.length - latestCount) <= 1) agreement++;
  }

  // If majority of frames agree, use latest; otherwise use the mode
  if (agreement >= Math.ceil(frameBuffer.length / 2)) {
    return latest;
  }

  // Fallback: return frame with most common count
  const counts = {};
  for (const f of frameBuffer) {
    counts[f.length] = (counts[f.length] || 0) + 1;
  }
  const modeCount = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
  return frameBuffer.find(f => f.length == modeCount) || latest;
}

// ---- main detection loop ----
function loop() {
  setTimeout(loop, LOOP_MS);

  const faces = detectFaces();
  if (faces === null) return;

  // Buffer for smoothing
  frameBuffer.push(faces);
  while (frameBuffer.length > BUFFER_SIZE) frameBuffer.shift();

  const smoothed = getSmoothedFaces();
  const count = smoothed.length;

  // Update the presence badge on the interactive console
  if (badge) {
    badge.textContent = count ? (count === 1 ? "👤" : "👤" + count) : "";
    badge.classList.toggle("show", count > 0);
  }

  // Send face data to the ambient overlay radar via the backend event stream
  sendStatus(count > 0 ? "tracking" : "scanning");
  fetch(API + "/emit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: "presence_data",
      text: JSON.stringify(smoothed)
    })
  }).catch(() => {});
}


// ---- boot: wait a moment for the webcam to initialise, then start ----
setTimeout(init, 1500);
