#!/usr/bin/env python3
"""Screenshot the overlay HUD without putting a window on the user's desktop.

The overlay is an ordinary web page, so it can be rendered in headless Chromium and photographed.
That is the only way to iterate on a transparent always-on-top HUD from a background session, and
it is genuinely useful afterwards too — a visual regression check for the HUD.

Because the real window composites over whatever is on screen, a flat backdrop would flatter the
design dishonestly: any dark panel looks fine on black. So the page is rendered over a real
screenshot of the desktop when one can be captured, and over a representative bright photo-like
gradient otherwise. If the HUD is still legible there, it is legible in use.

    python scripts/hud-shot.py out.png [--state listening] [--mode text] [--backdrop light|dark|FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

OVERLAY = Path(__file__).resolve().parent.parent / "overlay"

# A deliberately awkward backdrop: bright, saturated, high-contrast. Panels that survive this
# survive a real desktop.
LIGHT_BACKDROP = """
  background:
    radial-gradient(900px 600px at 12% 18%, #ffd9a8 0%, transparent 60%),
    radial-gradient(1100px 700px at 82% 30%, #a8e6ff 0%, transparent 62%),
    radial-gradient(800px 900px at 55% 95%, #ffc8e0 0%, transparent 60%),
    linear-gradient(150deg, #f7f3ec 0%, #dfe9f0 45%, #cfd9e6 100%);
"""

DARK_BACKDROP = """
  background:
    radial-gradient(900px 600px at 18% 22%, #1d3348 0%, transparent 60%),
    radial-gradient(1000px 700px at 80% 70%, #2a1f3d 0%, transparent 62%),
    linear-gradient(160deg, #0b1016 0%, #131b24 60%, #0a0f14 100%);
"""


#  main.js: COMPACT = { w: 620, h: 128 }, EXPANDED = { w: 620, h: 520 }
FRAME_W = 620
COMPACT_H = 128
EXPANDED_H = 520


def _page(backdrop_css: str, mode: str, state: str, port: str, demo: bool, api_port: str = "") -> str:
    """Wrap the real overlay in an iframe over a backdrop, so nothing in overlay/ is modified."""
    frame_h = EXPANDED_H if mode == "text" else COMPACT_H
    demo_js = "true" if demo else "false"
    state_js = f'"{state}"' if state else "null"
    energy = {"listening": 0.72, "speaking": 0.55}.get(state, 0.0)
    api_q = f"?port={api_port}" if api_port else ""
    return f"""<!doctype html>
<meta charset="utf-8">
<style>
  html, body {{ margin: 0; height: 100%; overflow: hidden; }}
  body {{ {backdrop_css} }}
  /* A little desktop furniture, so the HUD is judged against real content and not empty space. */
  .doc {{ position: absolute; left: 6%; top: 8%; width: 46%; color: #22303c;
          font: 15px/1.7 ui-sans-serif, system-ui; opacity: .75; }}
  .doc h1 {{ font-size: 30px; margin: 0 0 12px; }}
  /* The real overlay window is 620 wide and 128 (compact) or 520 (expanded) tall, anchored near
     the bottom centre of the work area. Rendering it full-viewport would flatter the layout with
     space it never has, so the frame is sized exactly as Electron sizes the window. */
  iframe {{ position: absolute; left: 50%; bottom: 26px; translate: -50% 0;
            width: {FRAME_W}px; height: {frame_h}px; border: 0; background: transparent; }}
</style>
<div class="doc">
  <h1>Composited over real content</h1>
  <p>The overlay window is transparent, so the panels below are judged against whatever happens to
  be on screen. Text at this weight and size is the hardest case for a translucent surface: if the
  HUD still separates cleanly from it, the surface treatment is doing its job.</p>
  <p>backdrop-filter cannot help here — over a transparent window there is no page content behind
  the panel to sample, so depth has to come from the panel itself.</p>
</div>
<iframe id="f" src="http://127.0.0.1:{port}/__overlay__/index.html{api_q}"></iframe>
<script>
  const f = document.getElementById('f');
  f.addEventListener('load', () => {{
    const d = f.contentDocument, w = f.contentWindow;
    // Point the HUD at a live backend if one was named, so health, telemetry and history are the
    // real thing rather than a stub. Must be set before renderer.js reads it.
    // (the iframe has already run its scripts by 'load', so this only affects later fetches)
    // The overlay expects an Electron preload bridge; stub it so the page runs in a plain browser.
    w.jarvis = {{ overlayCamera: false, setMode(){{}}, setZoom(){{}}, launchPhone(){{}},
                 hide(){{}}, quit(){{}}, sportsToggle(){{}}, onToast(){{}} }};
    const apply = () => {{
      // Drive the HUD through its own API, not by poking classes: setState is what wires the
      // status line, the meter and the reactor together, so bypassing it would photograph a
      // HUD that looks right and does nothing.
      const hud = w.__hud;
      if (!hud) {{ window.__err = 'renderer did not expose __hud'; return; }}
      hud.applyMode('{mode}');
      hud.setState({state_js});
      if ({demo_js}) seedDemo(d, w, hud);
      window.__ready = true;
    }};
    setTimeout(apply, 500);
  }});

  // Populate the HUD with representative content so the layout is judged loaded, not empty.
  function seedDemo(d, w, hud) {{
    const log = d.getElementById('log');
    const turns = [
      ['you', 'what did I miss this morning?'],
      ['jarvis', 'Three things. Pradhyuman asked about the Saturday plan, your 11:00 with the design review moved to 14:30, and the build on `jarvis-1000x` went green at 09:41.'],
      ['you', 'move the review to tomorrow and tell him I am in'],
      ['jarvis', 'Done — review is now Thursday 14:30, and I have messaged Pradhyuman that you are in for Saturday.'],
    ];
    for (const [who, text] of turns) {{
      const el = d.createElement('div');
      el.className = 'msgline ' + who;
      el.style.animation = 'none';
      el.innerHTML = '<span class="who">' + (who === 'you' ? 'YOU' : 'JARVIS') + '</span>' +
        text.replace(/`([^`]+)`/g, '<code>$1</code>');
      log.appendChild(el);
    }}
    log.scrollTop = log.scrollHeight;

    // Subsystem dots and a brain label, as /health would supply them.
    d.getElementById('sysdots').innerHTML =
      ['on','on','on','on','','on','on'].map(c => '<span class="d ' + c + '"></span>').join('');
    d.getElementById('brainlabel').textContent = 'ollama · 6/7';
    d.getElementById('telemetry').innerHTML =
      '<span class="t">CPU<b>34%</b></span><span class="t hot">MEM<b>78%</b></span>' +
      '<span class="t">GPU<b>21%</b></span><span class="t">BAT<b>62%</b></span>';
    // Gauges as /stats would supply them, and a live-looking meter trace so the amplitude
    // display is photographed with signal in it.
    hud.reactor.set({{ cpu: 0.34, mem: 0.78, gpu: 0.21, batt: 0.62 }});
    const levels = [.12,.3,.55,.72,.61,.4,.66,.85,.7,.45,.28,.5,.74,.62,.38,.2,.44,.68,.8,.58,
                    .33,.47,.71,.55,.3,.18,.4,.63,.77,.6,.42,.25,.5,.7,.52,.35,.6,.8,.66,.44];
    if ({energy} > 0) for (const v of levels) hud.pushLevel(v);
  }}
</script>
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="hud.png")
    ap.add_argument("--mode", default="text", choices=["voice", "text"])
    ap.add_argument("--state", default="", choices=["", "listening", "thinking", "speaking", "error"])
    ap.add_argument("--backdrop", default="light", help="light | dark | path to an image")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--port", default="8899")
    ap.add_argument("--demo", action="store_true", help="seed representative content")
    ap.add_argument("--api-port", default="", help="a live --web backend to read real data from")
    ap.add_argument("--settle", type=float, default=2.2, help="seconds to let animation settle")
    args = ap.parse_args()

    if args.backdrop == "light":
        backdrop = LIGHT_BACKDROP
    elif args.backdrop == "dark":
        backdrop = DARK_BACKDROP
    else:
        data = base64.b64encode(Path(args.backdrop).read_bytes()).decode()
        backdrop = f"background: url(data:image/png;base64,{data}) center/cover;"

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("needs playwright:  .venv/bin/pip install playwright", file=sys.stderr)
        return 2

    # Serve overlay/ over HTTP. file:// would work for the assets but blocks ES module imports
    # under some Chromium versions, and the real overlay loads renderer.js as a module.
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OVERLAY.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", int(args.port)), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    host = OVERLAY.parent / "__hudshot__.html"
    # The iframe path has to resolve under the served root; overlay/ is the directory name.
    host.write_text(_page(backdrop, args.mode, args.state, args.port, args.demo, args.api_port)
                    .replace("__overlay__", "overlay"))

    try:
        async with async_playwright() as p:
            # Use the system Chrome, as the rest of Jarvis does — Playwright's own browser
            # download is not present and is not worth adding for a screenshot tool.
            browser = await p.chromium.launch(
                channel="chrome",
                args=["--no-sandbox", "--enable-unsafe-swiftshader"],
            )
            page = await browser.new_page(viewport={"width": args.width, "height": args.height},
                                          device_scale_factor=2)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type in ("error", "warning") else None)

            await page.goto(f"http://127.0.0.1:{args.port}/__hudshot__.html")
            await page.wait_for_timeout(int(args.settle * 1000))
            await page.screenshot(path=args.out)
            await browser.close()

        print(f"wrote {args.out}")
        for e in errors:
            print("  ", e)
        return 0
    finally:
        host.unlink(missing_ok=True)
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
