#!/usr/bin/env python3
"""Generate high-resolution Iron Man Arc Reactor HUD icons for Jarvis.

Produces:
- assets/icons/jarvis.svg (Scalable vector icon)
- assets/icons/jarvis-512.png, 256.png, 128.png, 64.png, 48.png
- assets/icons/jarvis.ico (Multi-size Windows icon)
- overlay/icon.png (Electron window icon)
"""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
ICONS_DIR = REPO / "assets" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# 1. Scalable Vector Graphic (SVG)
# -----------------------------------------------------------------------------
SVG_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <!-- Background Gradient -->
    <radialGradient id="bgGrad" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#0a192f"/>
      <stop offset="70%" stop-color="#050b14"/>
      <stop offset="100%" stop-color="#020408"/>
    </radialGradient>

    <!-- Core Glow Gradient -->
    <radialGradient id="coreGlow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="30%" stop-color="#70f3ff"/>
      <stop offset="70%" stop-color="#00d2ff"/>
      <stop offset="100%" stop-color="#0055ff" stop-opacity="0"/>
    </radialGradient>

    <!-- Arc Reactor Cyan Glow Filter -->
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="8" result="blur" />
      <feMerge>
        <feMergeNode in="blur" />
        <feMergeNode in="SourceGraphic" />
      </feMerge>
    </filter>
    
    <filter id="intenseGlow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="16" result="blur" />
      <feMerge>
        <feMergeNode in="blur" />
        <feMergeNode in="blur" />
        <feMergeNode in="SourceGraphic" />
      </feMerge>
    </filter>
  </defs>

  <!-- Base Badge -->
  <rect width="512" height="512" rx="110" fill="url(#bgGrad)"/>
  <rect width="510" height="510" x="1" y="1" rx="109" fill="none" stroke="#00d2ff" stroke-width="2" stroke-opacity="0.3"/>

  <!-- Outer HUD Targeting Brackets -->
  <g stroke="#00f0ff" stroke-width="4" stroke-linecap="round" opacity="0.85" filter="url(#glow)">
    <!-- Top-Left -->
    <path d="M 64 120 A 200 200 0 0 1 120 64" fill="none"/>
    <path d="M 50 110 L 50 80 L 80 50 L 110 50" fill="none" stroke-width="2.5" opacity="0.6"/>
    <!-- Top-Right -->
    <path d="M 392 64 A 200 200 0 0 1 448 120" fill="none"/>
    <path d="M 402 50 L 432 50 L 462 80 L 462 110" fill="none" stroke-width="2.5" opacity="0.6"/>
    <!-- Bottom-Right -->
    <path d="M 448 392 A 200 200 0 0 1 392 448" fill="none"/>
    <path d="M 462 402 L 462 432 L 432 462 L 402 462" fill="none" stroke-width="2.5" opacity="0.6"/>
    <!-- Bottom-Left -->
    <path d="M 120 448 A 200 200 0 0 1 64 392" fill="none"/>
    <path d="M 110 462 L 80 462 L 50 432 L 50 402" fill="none" stroke-width="2.5" opacity="0.6"/>
  </g>

  <!-- Outer Track Reticle -->
  <circle cx="256" cy="256" r="190" fill="none" stroke="#0055ff" stroke-width="2" opacity="0.4"/>
  <circle cx="256" cy="256" r="182" fill="none" stroke="#00f0ff" stroke-width="3" stroke-dasharray="12 8 4 8" opacity="0.7"/>

  <!-- Arc Reactor Coil Segments (10 Coils) -->
  <g id="coils" filter="url(#glow)">
    <!-- Radial Coils around 256, 256 -->
    <g transform="translate(256, 256)">
      <!-- 10 segments rotated every 36 deg -->
      <g id="coil-pair">
        <rect x="-14" y="-170" width="28" height="24" rx="4" fill="#032540" stroke="#00f0ff" stroke-width="2.5"/>
        <line x1="-8" y1="-170" x2="-8" y2="-146" stroke="#ffd700" stroke-width="2" opacity="0.8"/>
        <line x1="0" y1="-170" x2="0" y2="-146" stroke="#ffd700" stroke-width="2" opacity="0.8"/>
        <line x1="8" y1="-170" x2="8" y2="-146" stroke="#ffd700" stroke-width="2" opacity="0.8"/>
      </g>
      <use href="#coil-pair" transform="rotate(36)"/>
      <use href="#coil-pair" transform="rotate(72)"/>
      <use href="#coil-pair" transform="rotate(108)"/>
      <use href="#coil-pair" transform="rotate(144)"/>
      <use href="#coil-pair" transform="rotate(180)"/>
      <use href="#coil-pair" transform="rotate(216)"/>
      <use href="#coil-pair" transform="rotate(252)"/>
      <use href="#coil-pair" transform="rotate(288)"/>
      <use href="#coil-pair" transform="rotate(324)"/>
    </g>
  </g>

  <!-- Mid Ring & Inner Core Ring -->
  <circle cx="256" cy="256" r="138" fill="none" stroke="#00d2ff" stroke-width="4" opacity="0.9" filter="url(#glow)"/>
  <circle cx="256" cy="256" r="126" fill="none" stroke="#00f0ff" stroke-width="2" stroke-dasharray="8 6" opacity="0.8"/>
  <circle cx="256" cy="256" r="102" fill="#051c2f" stroke="#00f0ff" stroke-width="5" filter="url(#glow)"/>

  <!-- Energy spokes linking inner core to mid ring -->
  <g stroke="#00f0ff" stroke-width="3" opacity="0.75" transform="translate(256, 256)">
    <line x1="0" y1="-102" x2="0" y2="-138"/>
    <line x1="0" y1="102" x2="0" y2="138"/>
    <line x1="-102" y1="0" x2="-138" y2="0"/>
    <line x1="102" y1="0" x2="138" y2="0"/>
    <line x1="-72" y1="-72" x2="-98" y2="-98"/>
    <line x1="72" y1="-72" x2="98" y2="-98"/>
    <line x1="-72" y1="72" x2="-98" y2="98"/>
    <line x1="72" y1="72" x2="98" y2="98"/>
  </g>

  <!-- Central Arc Reactor Core (Inverted Triangle + Glowing Heart) -->
  <polygon points="256,330 190,215 322,215" fill="#093556" stroke="#00f0ff" stroke-width="4" filter="url(#glow)"/>
  <polygon points="256,312 205,225 307,225" fill="none" stroke="#70f3ff" stroke-width="2" opacity="0.9"/>
  
  <!-- Glowing Center Energy Node -->
  <circle cx="256" cy="256" r="60" fill="url(#coreGlow)" filter="url(#intenseGlow)"/>
  <circle cx="256" cy="256" r="28" fill="#ffffff" filter="url(#glow)"/>

  <!-- Subtle JARVIS HUD Text -->
  <text x="256" y="482" text-anchor="middle" fill="#00f0ff" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="24" font-weight="900" letter-spacing="8" opacity="0.85">JARVIS</text>
</svg>
"""

# -----------------------------------------------------------------------------
# 2. High-Quality Raster Rendering using Pillow (Supersampled for Crispness)
# -----------------------------------------------------------------------------
def render_icon(target_size: int) -> Image.Image:
    # 2x supersampling for ultra smooth antialiased rendering
    scale = 2
    dim = target_size * scale
    cx = cy = dim / 2
    
    img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. Rounded badge background with subtle metallic border
    radius = int(dim * 0.22)
    # Background gradient approximation
    draw.rounded_rectangle([2, 2, dim - 3, dim - 3], radius=radius, fill=(5, 11, 20, 255), outline=(0, 210, 255, 90), width=int(2 * scale))
    
    # Inner circular dark chamber
    draw.ellipse([cx - dim*0.44, cy - dim*0.44, cx + dim*0.44, cy + dim*0.44], fill=(7, 18, 34, 255))
    
    # 2. Outer HUD brackets
    bracket_col = (0, 240, 255, 200)
    bw = max(1, int(3 * scale))
    # TL
    draw.line([(dim*0.12, dim*0.18), (dim*0.12, dim*0.12), (dim*0.18, dim*0.12)], fill=bracket_col, width=bw)
    # TR
    draw.line([(dim*0.82, dim*0.12), (dim*0.88, dim*0.12), (dim*0.88, dim*0.18)], fill=bracket_col, width=bw)
    # BR
    draw.line([(dim*0.88, dim*0.82), (dim*0.88, dim*0.88), (dim*0.82, dim*0.88)], fill=bracket_col, width=bw)
    # BL
    draw.line([(dim*0.18, dim*0.88), (dim*0.12, dim*0.88), (dim*0.12, dim*0.82)], fill=bracket_col, width=bw)
    
    # 3. Outer dashed/segmented ring
    r_outer = dim * 0.38
    num_ticks = 40
    for i in range(num_ticks):
        ang = i * (2 * math.pi / num_ticks)
        if i % 4 == 0:
            continue
        p1 = (cx + (r_outer - 4 * scale) * math.cos(ang), cy + (r_outer - 4 * scale) * math.sin(ang))
        p2 = (cx + (r_outer + 4 * scale) * math.cos(ang), cy + (r_outer + 4 * scale) * math.sin(ang))
        draw.line([p1, p2], fill=(0, 200, 255, 160), width=max(1, int(2 * scale)))
        
    # 4. Arc Reactor Coils (10 segments)
    num_coils = 10
    r_coil = dim * 0.33
    for i in range(num_coils):
        ang = i * (2 * math.pi / num_coils)
        x = cx + r_coil * math.cos(ang)
        y = cy + r_coil * math.sin(ang)
        cw, ch = 12 * scale, 8 * scale
        draw.rectangle([x - cw/2, y - ch/2, x + cw/2, y + ch/2], fill=(3, 37, 64, 255), outline=(0, 240, 255, 230), width=max(1, int(1.5 * scale)))
        # copper wire accents
        draw.line([(x - cw/4, y - ch/2), (x - cw/4, y + ch/2)], fill=(255, 215, 0, 180), width=max(1, int(1 * scale)))
        draw.line([(x + cw/4, y - ch/2), (x + cw/4, y + ch/2)], fill=(255, 215, 0, 180), width=max(1, int(1 * scale)))

    # 5. Middle bright ring
    r_mid = dim * 0.27
    draw.ellipse([cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid], outline=(0, 220, 255, 240), width=max(1, int(3 * scale)))
    
    # 6. Inner Chamber Ring
    r_in = dim * 0.20
    draw.ellipse([cx - r_in, cy - r_in, cx + r_in, cy + r_in], fill=(5, 28, 47, 255), outline=(0, 240, 255, 255), width=max(1, int(4 * scale)))
    
    # Spokes connecting inner and mid
    for i in range(8):
        ang = i * (math.pi / 4)
        p1 = (cx + r_in * math.cos(ang), cy + r_in * math.sin(ang))
        p2 = (cx + r_mid * math.cos(ang), cy + r_mid * math.sin(ang))
        draw.line([p1, p2], fill=(0, 240, 255, 200), width=max(1, int(2 * scale)))

    # 7. Arc Reactor Core Triangle
    tr_h = dim * 0.13
    p_top_l = (cx - tr_h * 0.85, cy - tr_h * 0.5)
    p_top_r = (cx + tr_h * 0.85, cy - tr_h * 0.5)
    p_bot = (cx, cy + tr_h * 0.95)
    draw.polygon([p_top_l, p_top_r, p_bot], fill=(9, 53, 86, 255), outline=(0, 240, 255, 255))
    
    # 8. Bright Reactor Core Glow
    r_glow = dim * 0.11
    # Create glow layer
    glow_img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img)
    glow_draw.ellipse([cx - r_glow*1.6, cy - r_glow*1.6, cx + r_glow*1.6, cy + r_glow*1.6], fill=(0, 210, 255, 140))
    glow_draw.ellipse([cx - r_glow, cy - r_glow, cx + r_glow, cy + r_glow], fill=(112, 243, 255, 200))
    glow_draw.ellipse([cx - r_glow*0.5, cy - r_glow*0.5, cx + r_glow*0.5, cy + r_glow*0.5], fill=(255, 255, 255, 255))
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(radius=int(6 * scale)))
    
    # Composite glow
    img = Image.alpha_composite(img, glow_img)
    
    # Sharp center white pearl
    draw_final = ImageDraw.Draw(img)
    r_pearl = dim * 0.04
    draw_final.ellipse([cx - r_pearl, cy - r_pearl, cx + r_pearl, cy + r_pearl], fill=(255, 255, 255, 255), outline=(150, 250, 255, 255))

    # Downsample to target size with high quality Lanczos filter
    final_img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    return final_img


def main():
    # Write SVG
    svg_path = ICONS_DIR / "jarvis.svg"
    svg_path.write_text(SVG_CONTENT, encoding="utf-8")
    print(f"✓ Saved {svg_path}")

    # Generate PNG sizes
    sizes = [512, 256, 128, 64, 48]
    images = {}
    for s in sizes:
        icon_img = render_icon(s)
        out_png = ICONS_DIR / f"jarvis-{s}.png"
        icon_img.save(out_png, "PNG")
        images[s] = icon_img
        print(f"✓ Saved {out_png}")

    # Default canonical jarvis.png
    images[512].save(ICONS_DIR / "jarvis.png", "PNG")
    print(f"✓ Saved {ICONS_DIR / 'jarvis.png'}")

    # Copy to overlay/icon.png for Electron window
    overlay_icon = REPO / "overlay" / "icon.png"
    images[512].save(overlay_icon, "PNG")
    print(f"✓ Saved {overlay_icon}")

    # Generate Windows .ico file with all standard sizes
    ico_path = ICONS_DIR / "jarvis.ico"
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    images[512].save(
        ico_path,
        format="ICO",
        sizes=ico_sizes
    )
    print(f"✓ Saved Windows icon {ico_path}")


if __name__ == "__main__":
    main()
