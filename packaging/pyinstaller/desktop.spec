# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path
import certifi

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "yaml",
]

if sys.platform == "darwin":
    hiddenimports += [
        "AppKit",
        "objc",
    ]

block_cipher = None

ROOT = Path(os.getcwd()).resolve()

ICON_PNG  = ROOT / "src" / "cockpitdecks_desktop" / "resources" / "app_icon.png"
ICON_ICNS = ROOT / "src" / "cockpitdecks_desktop" / "resources" / "app_icon.icns"

datas = []
if ICON_PNG.exists():
    datas.append((str(ICON_PNG), "cockpitdecks_desktop/resources"))
else:
    print(f"[desktop.spec] warning: app icon not found at {ICON_PNG}")

datas.append((certifi.where(), "."))

# Read version from package at build time
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "cockpitdecks_desktop",
    str(ROOT / "src" / "cockpitdecks_desktop" / "__init__.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
APP_VERSION = getattr(_mod, "__version__", "0.0.0")

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
    [],
    exclude_binaries=True,
    name="cockpitdecks-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ICON_PNG) if ICON_PNG.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="cockpitdecks-desktop",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Cockpitdecks Desktop.app",
        icon=str(ICON_ICNS) if ICON_ICNS.exists() else None,
        bundle_identifier="com.cockpitdecks.desktop",
        info_plist={
            "CFBundleDisplayName": "Cockpitdecks Desktop",
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSBackgroundOnly": False,
        },
    )
