# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for JARVIS.

Builds:
- Linux: dist/jarvis (Standalone ELF executable binary)
- Windows: dist/jarvis.exe (Standalone PE executable with Arc Reactor icon)
"""

import os
import sys
from pathlib import Path

block_cipher = None
REPO = Path(SPECPATH).resolve()

datas = [
    (str(REPO / "webui"), "webui"),
    (str(REPO / "assets"), "assets"),
]

hidden_imports = [
    "jarvis",
    "jarvis.config",
    "jarvis.webserver",
    "jarvis.hud_state",
    "jarvis.nearby",
    "jarvis.agent",
    "jarvis.agent.factory",
    "jarvis.agent.remote",
    "jarvis.agent.groq_core",
    "jarvis.agent.groq_tools",
    "jarvis.agent.sdk_tools",
    "jarvis.agent.prompt",
    "jarvis.memory.vault",
    "jarvis.audio",
    "jarvis.audio.mic",
    "jarvis.audio.vad",
    "jarvis.audio.tts",
    "jarvis.audio.voice_session",
    "jarvis.jobs.runner",
    "jarvis.jobs.notify",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "fastapi.staticfiles",
    "starlette",
    "starlette.responses",
    "starlette.staticfiles",
    "sse_starlette",
    "pydantic",
    "psutil",
    "PIL",
    "dotenv",
    "sounddevice",
    "pvrecorder",
    "pvporcupine",
    "numpy",
    "httpx",
]

# Pick appropriate icon based on platform
icon_path = None
if sys.platform.startswith("win"):
    ico_candidate = REPO / "assets" / "icons" / "jarvis.ico"
    if ico_candidate.exists():
        icon_path = str(ico_candidate)
else:
    png_candidate = REPO / "assets" / "icons" / "jarvis-512.png"
    if png_candidate.exists():
        icon_path = str(png_candidate)

a = Analysis(
    [str(REPO / "run_jarvis.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
