// The reactor: one fullscreen WebGL2 quad that draws the orb, its gauges and its field.
//
// Everything here is signed-distance-field geometry in a single fragment shader — no three.js, no
// postprocessing library, no CDN. That is a deliberate constraint: this window composites over the
// desktop on a laptop whose GPU is already shared with Whisper, Piper, Ollama and a headless Chrome,
// so the budget is one quad and a handful of `smoothstep`s, not a scene graph.
//
// The design rule it follows is the one thing separating a real instrument from costume sci-fi:
// nothing moves at constant velocity for decoration. Every arc's sweep IS a measurement, every
// tremor in the ring IS microphone energy, and the idle state is genuinely still. Motion here means
// something changed. Values arrive through `set()` and are chased by critically-damped springs, so
// the needle settles like a physical gauge instead of snapping.
//
// Renders nothing and reports unsupported === true if WebGL2 is unavailable, so the CSS HUD stands
// on its own and the overlay never goes dark because a driver said no.

const VERT = `#version 300 es
in vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `#version 300 es
precision highp float;
out vec4 outColor;

uniform vec2  uRes;       // canvas size in device pixels
uniform float uTime;      // seconds since start
uniform vec3  uAccent;    // primary colour, linear 0-1
uniform vec3  uAccent2;   // highlight colour
uniform float uEnergy;    // 0-1 live audio level
uniform float uThink;     // 0-1 how much work is in flight
uniform float uSpeak;     // 0-1 speaking
uniform float uListen;    // 0-1 listening
uniform float uPresence;  // 0-1 someone is in front of the camera
uniform vec4  uGauges;    // cpu, memory, gpu, battery — each 0-1

#define TAU 6.28318530718

// --- Inigo Quilez 2D SDF primitives (iquilezles.org/articles/distfunctions2d) ---
float sdCircle(vec2 p, float r) { return length(p) - r; }

// Anti-aliased fill for a distance field, using the screen-space gradient so the edge is
// exactly one pixel wide at any resolution or zoom.
float aa(float d) {
  float w = fwidth(d) * 0.9;
  return 1.0 - smoothstep(-w, w, d);
}

// Cheap value noise — used only for the field grain, never for motion.
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void main() {
  vec2 res = uRes;
  // Normalise so the canvas spans roughly -1..1 on its short axis. Everything below is expressed
  // as a fraction of the orb's radius, which keeps the geometry readable and resolution-free.
  vec2 uv = (gl_FragCoord.xy - 0.5 * res) / (0.5 * min(res.x, res.y));

  vec3 col = vec3(0.0);
  float alpha = 0.0;

  // ---- core -------------------------------------------------------------
  // The core breathes with actual audio energy. At rest it is still, which is the point:
  // a permanent idle pulse is the visual equivalent of a progress bar that always spins.
  float beat = uEnergy * 0.075 + uSpeak * 0.025;
  float coreR = 0.335 + beat;
  float core = sdCircle(uv, coreR);

  // Body: bright centre falling to the accent at the rim.
  float body = aa(core);
  float grad = smoothstep(coreR, 0.0, length(uv));
  col += mix(uAccent, uAccent2, grad * grad) * body;
  alpha = max(alpha, body);

  // Fresnel rim — a thin bright edge is what makes a flat disc read as a sphere.
  float rim = exp(-abs(core) * 46.0);
  col += uAccent2 * rim * (0.55 + 0.45 * uEnergy);
  alpha = max(alpha, rim * 0.9);

  // Bloom: a wide, cheap falloff instead of a separate blur pass.
  float glow = exp(-max(core, 0.0) * 4.2);
  col += uAccent * glow * (0.30 + 0.55 * uEnergy + 0.25 * uSpeak);
  alpha = max(alpha, glow * 0.55);

  // ---- listening ring ---------------------------------------------------
  // A ring that deforms with the live signal. Its radius is modulated per-angle by energy, so
  // when the microphone is quiet it is a perfect circle and when you speak it ripples.
  if (uListen > 0.001) {
    float ang = atan(uv.y, uv.x);
    float wob = sin(ang * 7.0 + uTime * 2.3) * sin(ang * 3.0 - uTime * 1.7);
    float r = 0.475 + wob * uEnergy * 0.10;
    float ring = abs(length(uv) - r) - 0.010;
    col += uAccent2 * aa(ring) * uListen * (0.5 + 0.5 * uEnergy);
    alpha = max(alpha, aa(ring) * uListen);
  }

  // ---- telemetry arcs ---------------------------------------------------
  // Four concentric arcs, each a real reading. The sweep length is the value — the arc is the
  // gauge, not a decoration that happens to sit near one.
  for (int i = 0; i < 4; i++) {
    float v = clamp(uGauges[i], 0.0, 1.0);
    float radius = 0.60 + float(i) * 0.093;

    // Track: drawn whether or not there is a reading, so the dial exists before the needle does.
    // A gauge that only appears once it has data reads as an effect; one that is always there,
    // waiting, reads as an instrument.
    float track = abs(length(uv) - radius) - 0.005;
    float t = aa(track) * 0.26;
    col += uAccent * t;
    alpha = max(alpha, t);

    if (v <= 0.001) continue;

    // Sweep clockwise from twelve o'clock by the value. Doing this angularly rather than with a
    // symmetric SDF arc matters: sdArc is centred on an axis, so rotating it to "start at the top"
    // moves both ends and the gauge appears to grow in both directions from a drifting origin.
    // atan(x, y) is zero at +Y and increases clockwise, which is exactly a dial.
    float ang = atan(uv.x, uv.y);
    if (ang < 0.0) ang += TAU;
    float sweep = v * 0.80 * TAU;                   // 0.8 leaves a permanent gap at the top

    float ring = abs(length(uv) - radius) - 0.013;
    // Feather the leading edge over roughly a pixel of arc length so the tip is not a hard stair.
    float feather = min(fwidth(ang) * 1.5, 0.05) + 0.004;
    float within = 1.0 - smoothstep(sweep - feather, sweep + feather, ang);
    float f = aa(ring) * within;

    // Fuller arcs read hotter — a saturated tip is how a physical gauge shows a high reading.
    vec3 tint = mix(uAccent, vec3(1.0, 0.42, 0.48), smoothstep(0.72, 1.0, v));
    col += tint * f * 0.95;
    alpha = max(alpha, f * 0.95);
  }

  // ---- thinking sweep ---------------------------------------------------
  // The only continuously moving element, and it exists *only* while work is genuinely in
  // flight — so movement always means "busy", never "idle but animated".
  if (uThink > 0.001) {
    float ang = atan(uv.y, uv.x) + uTime * 2.6;
    float head = pow(max(0.0, cos(ang)), 26.0);         // a comet head, not a solid ring
    float band = abs(length(uv) - 0.475) - 0.016;
    col += uAccent2 * aa(band) * head * uThink * 1.6;
    alpha = max(alpha, aa(band) * head * uThink);
  }

  // ---- presence halo ----------------------------------------------------
  if (uPresence > 0.001) {
    float h = exp(-abs(sdCircle(uv, 0.90)) * 15.0);
    col += uAccent * h * uPresence * 0.34;
    alpha = max(alpha, h * uPresence * 0.34);
  }

  // ---- grain ------------------------------------------------------------
  // Static per-pixel dither. Breaks up the gradient banding that otherwise makes a dark glow
  // look like cheap CSS, and costs one hash.
  float g = (hash(gl_FragCoord.xy + floor(uTime * 24.0)) - 0.5) * 0.022;
  col += g;

  outColor = vec4(col * alpha, alpha);
}`;

/** Critically-damped spring: reaches the target quickly with no overshoot or oscillation. */
class Spring {
  constructor(value = 0, rate = 9) {
    this.value = value;
    this.target = value;
    this.rate = rate;
  }
  step(dt) {
    // Frame-rate independent exponential approach; clamped so a stalled tab can't overshoot.
    const k = 1 - Math.exp(-this.rate * Math.min(dt, 0.1));
    this.value += (this.target - this.value) * k;
    return this.value;
  }
}

function hexToRgb(css) {
  const s = (css || "").trim();
  const m = /^#?([0-9a-f]{6})$/i.exec(s);
  if (m) {
    const n = parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const rgb = /rgba?\(([^)]+)\)/i.exec(s);
  if (rgb) {
    const p = rgb[1].split(",").map((x) => parseFloat(x));
    return [(p[0] || 0) / 255, (p[1] || 0) / 255, (p[2] || 0) / 255];
  }
  return [0.21, 0.9, 1.0];
}

export class Reactor {
  constructor(canvas) {
    this.canvas = canvas;
    this.unsupported = false;
    this.gl = canvas.getContext("webgl2", {
      alpha: true,
      premultipliedAlpha: true,   // we output colour already multiplied by alpha
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",  // shares the GPU with whisper/piper/ollama — don't fight them
    });
    if (!this.gl) {
      this.unsupported = true;
      return;
    }
    try {
      this._build();
    } catch (err) {
      console.warn("[reactor] shader build failed, falling back to CSS only:", err);
      this.unsupported = true;
      return;
    }

    this.springs = {
      energy: new Spring(0, 18),      // audio must feel immediate
      think: new Spring(0, 6),
      speak: new Spring(0, 7),
      listen: new Spring(0, 8),
      presence: new Spring(0, 3),
      cpu: new Spring(0, 2.5),        // telemetry settles slowly, like a real needle
      mem: new Spring(0, 2.5),
      gpu: new Spring(0, 2.5),
      batt: new Spring(0, 1.2),
    };
    this.accent = [0.21, 0.9, 1.0];
    this.accent2 = [0.75, 0.95, 1.0];
    this._t0 = performance.now();
    this._last = this._t0;
    this._raf = null;
    this._running = false;
  }

  _compile(type, src) {
    const gl = this.gl;
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(sh) || "shader compile failed");
    }
    return sh;
  }

  _build() {
    const gl = this.gl;
    const prog = gl.createProgram();
    gl.attachShader(prog, this._compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, this._compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(prog) || "link failed");
    }
    this.prog = prog;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    this.u = {};
    for (const name of ["uRes", "uTime", "uAccent", "uAccent2", "uEnergy", "uThink",
                        "uSpeak", "uListen", "uPresence", "uGauges"]) {
      this.u[name] = gl.getUniformLocation(prog, name);
    }
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);   // premultiplied
  }

  /** Update targets. Unknown keys are ignored, so callers can pass partial state freely. */
  set(state = {}) {
    if (this.unsupported) return;
    for (const [k, v] of Object.entries(state)) {
      const s = this.springs[k];
      if (s && Number.isFinite(v)) s.target = Math.max(0, Math.min(1, v));
    }
  }

  /** Read the accent colours straight from CSS so the shader always matches the stylesheet. */
  syncTheme(el = document.body) {
    if (this.unsupported) return;
    const cs = getComputedStyle(el);
    this.accent = hexToRgb(cs.getPropertyValue("--accent"));
    this.accent2 = hexToRgb(cs.getPropertyValue("--accent2"));
  }

  _resize() {
    const gl = this.gl;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  }

  _frame = (now) => {
    if (!this._running) return;
    this._raf = requestAnimationFrame(this._frame);
    const dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;

    const gl = this.gl;
    this._resize();
    gl.useProgram(this.prog);

    const s = this.springs;
    gl.uniform2f(this.u.uRes, this.canvas.width, this.canvas.height);
    gl.uniform1f(this.u.uTime, (now - this._t0) / 1000);
    gl.uniform3fv(this.u.uAccent, this.accent);
    gl.uniform3fv(this.u.uAccent2, this.accent2);
    gl.uniform1f(this.u.uEnergy, s.energy.step(dt));
    gl.uniform1f(this.u.uThink, s.think.step(dt));
    gl.uniform1f(this.u.uSpeak, s.speak.step(dt));
    gl.uniform1f(this.u.uListen, s.listen.step(dt));
    gl.uniform1f(this.u.uPresence, s.presence.step(dt));
    gl.uniform4f(this.u.uGauges, s.cpu.step(dt), s.mem.step(dt), s.gpu.step(dt), s.batt.step(dt));

    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };

  start() {
    if (this.unsupported || this._running) return;
    this._running = true;
    this._last = performance.now();
    this._raf = requestAnimationFrame(this._frame);
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }
}
