# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ED Colony Scout.

Build via packaging/build.ps1, or directly from the repo root:
    pyinstaller packaging/ed_colony_scout.spec --clean

Produces a windowed (no console) ONEDIR build under
dist/ED Colony Scout/ -- onedir rather than onefile deliberately, to avoid
onefile's self-extraction-to-a-temp-dir-on-every-launch behavior, which is
a well-documented trigger for antivirus/SmartScreen false positives on
unsigned hobby executables (this app isn't code-signed). Zip the resulting
dist/ED Colony Scout/ folder for distribution.

gui/pipeline.py calls straight into scripts/*.py in-process (no
subprocess), so PyInstaller's static import analysis picks up nearly
everything automatically -- the one addition below is ijson's compiled
backend, which it selects via a conditional try/except import PyInstaller's
analysis doesn't always catch.
"""

from pathlib import Path

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "ED Colony Scout"

a = Analysis(
    [str(REPO_ROOT / "gui" / "main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[
        # Bundled so gui.app._icon_path() can find it at runtime via
        # sys._MEIPASS for the window's iconphoto. The .exe's own file/
        # taskbar icon comes separately from EXE(icon=...) below.
        (str(REPO_ROOT / "icon.png"), "."),
    ],
    hiddenimports=["ijson.backends.yajl2_c"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(REPO_ROOT / "packaging" / "icon.ico"),
    version=str(REPO_ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
