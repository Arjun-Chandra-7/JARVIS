import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

// ---------- palette per state ----------
const STATES = {
  standby:  { color: new THREE.Color(0x2f9bb8), amp: 0.09, speed: 0.28, label: "STANDBY",  sub: "core online · awaiting input" },
  listening:{ color: new THREE.Color(0xc79a44), amp: 0.18, speed: 0.6,  label: "LISTENING",sub: "capturing audio…" },
  thinking: { color: new THREE.Color(0x7d6bc4), amp: 0.30, speed: 1.1,  label: "THINKING", sub: "reasoning…" },
  speaking: { color: new THREE.Color(0x38b98a), amp: 0.22, speed: 0.85, label: "SPEAKING", sub: "responding" },
};
let state = "standby";
let target = { color: STATES.standby.color.clone(), amp: STATES.standby.amp, speed: STATES.standby.speed };

// ---------- renderer ----------
const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100);
camera.position.set(0, 0, 6);

// ---------- energy core (noise-displaced icosahedron) ----------
const coreUniforms = {
  uTime: { value: 0 },
  uAmp: { value: 0.16 },
  uColor: { value: STATES.standby.color.clone() },
  uPulse: { value: 0 },
};
const coreMat = new THREE.ShaderMaterial({
  uniforms: coreUniforms,
  transparent: true,
  vertexShader: /* glsl */ `
    uniform float uTime; uniform float uAmp; uniform float uPulse;
    varying float vN; varying vec3 vPos;
    // simplex-ish 3d noise (Ashima)
    vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x,289.0);}
    vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
    float snoise(vec3 v){
      const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
      vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
      vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
      vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
      i=mod(i,289.0);
      vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
      float n_=1.0/7.0; vec3 ns=n_*D.wyz-D.xzx;
      vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
      vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
      vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
      vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
      vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
      vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
      vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
      p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
      vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
      return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
    }
    void main(){
      vec3 p=position;
      float n=snoise(normalize(position)*2.2+uTime*0.5);
      float disp=n*uAmp + uPulse*0.5*snoise(position*4.0+uTime);
      p+=normal*disp;
      vN=n; vPos=p;
      gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.0);
    }`,
  fragmentShader: /* glsl */ `
    uniform vec3 uColor; uniform float uTime; varying float vN; varying vec3 vPos;
    void main(){
      float fres=pow(1.0-abs(dot(normalize(vPos),vec3(0.0,0.0,1.0))),2.2);
      float glow=0.22+0.45*smoothstep(-0.4,0.7,vN);
      vec3 col=uColor*glow + vec3(0.55,0.75,0.95)*fres*0.22;
      gl_FragColor=vec4(col, 0.72);
    }`,
});
const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.4, 6), coreMat);
scene.add(core);

// faint wireframe shell
const shell = new THREE.Mesh(
  new THREE.IcosahedronGeometry(2.0, 1),
  new THREE.MeshBasicMaterial({ color: 0x2f9bb8, wireframe: true, transparent: true, opacity: 0.05 })
);
scene.add(shell);

// ---------- particle nebula ----------
const N = 1100;
const pos = new Float32Array(N * 3);
for (let i = 0; i < N; i++) {
  const r = 3.4 + Math.random() * 4.5;
  const t = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
  pos[i*3] = r*Math.sin(ph)*Math.cos(t); pos[i*3+1] = r*Math.sin(ph)*Math.sin(t); pos[i*3+2] = r*Math.cos(ph);
}
const pg = new THREE.BufferGeometry();
pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
const particles = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0x6fb3cc, size: 0.018, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending }));
scene.add(particles);

// orbit rings
const rings = [];
for (let i = 0; i < 2; i++) {
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(2.6 + i * 0.6, 0.005, 8, 140),
    new THREE.MeshBasicMaterial({ color: 0x2f9bb8, transparent: true, opacity: 0.12 })
  );
  ring.rotation.x = Math.PI / 2 + (Math.random() - 0.5);
  ring.rotation.y = Math.random();
  rings.push(ring); scene.add(ring);
}

// ---------- bloom ----------
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.5, 0.4, 0.22);
composer.addPass(bloom);

// ---------- interaction ----------
const mouse = { x: 0, y: 0 };
addEventListener("pointermove", (e) => { mouse.x = (e.clientX / innerWidth - 0.5); mouse.y = (e.clientY / innerHeight - 0.5); });
addEventListener("click", () => gsap.fromTo(coreUniforms.uPulse, { value: 1 }, { value: 0, duration: 1.1, ease: "power2.out" }));

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h); composer.setSize(w, h);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener("resize", resize); resize();

// ---------- render loop ----------
const clock = new THREE.Clock();
function tick() {
  const t = clock.getElapsedTime();
  coreUniforms.uTime.value = t * target.speed;
  coreUniforms.uAmp.value += (target.amp - coreUniforms.uAmp.value) * 0.05;
  coreUniforms.uColor.value.lerp(target.color, 0.05);
  core.rotation.y = t * 0.15; core.rotation.x = Math.sin(t * 0.2) * 0.2;
  shell.rotation.y = -t * 0.08; shell.rotation.z = t * 0.05;
  particles.rotation.y = t * 0.02;
  rings.forEach((r, i) => { r.rotation.z += 0.002 * (i + 1); });
  camera.position.x += (mouse.x * 1.6 - camera.position.x) * 0.05;
  camera.position.y += (-mouse.y * 1.0 - camera.position.y) * 0.05;
  camera.lookAt(0, 0, 0);
  bloom.strength = 0.45 + coreUniforms.uAmp.value * 0.4;
  composer.render();
  requestAnimationFrame(tick);
}
tick();

// ---------- state control ----------
function setState(s) {
  state = s; const cfg = STATES[s];
  target.color = cfg.color.clone(); target.amp = cfg.amp; target.speed = cfg.speed;
  document.getElementById("state").textContent = cfg.label;
  document.getElementById("state").style.color = "#" + cfg.color.getHexString();
  document.getElementById("substate").textContent = cfg.sub;
}

// ---------- HUD chrome ----------
function pad(n){return String(n).padStart(2,"0");}
setInterval(() => {
  const d = new Date();
  document.getElementById("clk").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  document.getElementById("date").textContent = d.toDateString().toUpperCase();
}, 1000);
function jitter(){
  for (let i = 1; i <= 4; i++){
    const v = 20 + Math.random() * 78;
    document.getElementById("b"+i).style.transform = `scaleX(${v/100})`;
    document.getElementById("v"+i).textContent = Math.round(v)+"%";
  }
}
setInterval(jitter, 1400); jitter();

// ---------- conversation ----------
const log = document.getElementById("log");
function addMsg(who, text) {
  const el = document.createElement("div");
  el.className = "msg " + (who === "you" ? "you" : "jarvis");
  el.innerHTML = `<span class="who">${who === "you" ? "YOU" : "JARVIS"}</span>${text}`;
  log.appendChild(el);
  while (log.children.length > 6) log.removeChild(log.firstChild);
  gsap.to(el, { opacity: 1, y: 0, duration: 0.5, ease: "power2.out" });
}

async function ask(text) {
  addMsg("you", text);
  setState("thinking");
  let reply;
  try {
    const r = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    reply = (await r.json()).reply;
  } catch {
    reply = "I'm running in preview mode — start me with `python -m jarvis --web` to connect my brain, sir.";
  }
  setState("speaking");
  addMsg("jarvis", reply || "…");
  if (window.speechSynthesis) { const u = new SpeechSynthesisUtterance(reply); u.rate = 1.05; u.pitch = 0.9; speechSynthesis.speak(u); }
  setTimeout(() => setState("standby"), Math.min(6000, 1500 + (reply||"").length * 35));
}

const cmd = document.getElementById("cmd");
cmd.addEventListener("keydown", (e) => { if (e.key === "Enter" && cmd.value.trim()) { const v = cmd.value.trim(); cmd.value = ""; ask(v); } });

// ---------- voice input (browser speech) ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const mic = document.getElementById("mic");
if (SR) {
  const rec = new SR(); rec.lang = "en-US"; rec.interimResults = false;
  let live = false;
  mic.addEventListener("click", () => { live ? rec.stop() : rec.start(); });
  rec.onstart = () => { live = true; mic.classList.add("live"); setState("listening"); };
  rec.onend = () => { live = false; mic.classList.remove("live"); if (state === "listening") setState("standby"); };
  rec.onresult = (e) => { const txt = e.results[0][0].transcript; ask(txt); };
} else {
  mic.title = "Voice input needs Chrome/Edge"; mic.style.opacity = 0.5;
}

// ---------- live voice/phone events streamed from the --voice process ----------
try {
  const es = new EventSource("/events");
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    onLiveEvent(d.kind, d.text || "");
  };
} catch {}
function onLiveEvent(kind, text) {
  if (kind === "wake") setState("listening");
  else if (kind === "heard") { addMsg("you", text); setState("thinking"); }
  else if (kind === "reply") { addMsg("jarvis", text); setState("speaking"); setTimeout(() => setState("standby"), Math.min(6000, 1500 + text.length * 30)); }
  else if (kind === "phone") addMsg("jarvis", "📱 " + text);
  else if (kind === "sleep") setState("standby");
  else if (kind === "ready") document.getElementById("substate").textContent = text;
}

addMsg("jarvis", "Systems online. How can I help, sir?");

