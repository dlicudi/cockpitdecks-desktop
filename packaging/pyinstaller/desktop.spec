# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("PySide6")

block_cipher = None

ROOT = Path(os.getcwd()).resolve()
LAUNCHER_SIDECAR = ROOT / "resources" / "bin" / "cockpitdecks-launcher"

datas = []
if LAUNCHER_SIDECAR.exists():
    # Bundle launcher alongside desktop executable resources.
    datas.append((str(LAUNCHER_SIDECAR), "."))
else:
    print(f"[desktop.spec] warning: launcher sidecar not found at {LAUNCHER_SIDECAR}")

a = Analysis(
    [str(ROOT / "src" / "cockpitdecks_desktop" / "app.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cockpitdecks-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
