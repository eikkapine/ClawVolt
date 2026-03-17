# -*- mode: python ; coding: utf-8 -*-
# ClawVolt — PyInstaller spec
# Run from repo root via:  build\build_exe_py312.bat
# Or manually:  py -3.12 -m PyInstaller build\ClawVolt.spec

import os
import sys

REPO_ROOT = os.path.dirname(SPEC)   # SPEC = path to this file = build/
# SPEC is the spec file path, its dir is build\ — repo root is one level up
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))

ONE_FILE = False   # True = single .exe (slow start); False = folder (fast start)

block_cipher = None

a = Analysis(
    [os.path.join(REPO_ROOT, 'src', 'claw_volt_gui.py')],
    pathex=[os.path.join(REPO_ROOT, 'src')],
    binaries=[
        # adlx_bridge.exe must be in repo root when building
        (os.path.join(REPO_ROOT, 'adlx_bridge.exe'), '.'),
    ],
    datas=[
        # Bundle icon so window titlebar works at runtime
        (os.path.join(REPO_ROOT, 'assets', 'icon.ico'), '.'),
    ],
    hiddenimports=[
        'crash_logger',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.filedialog',
        'win32evtlog',
        'win32api',
        'win32con',
        'pywintypes',
        'xml.etree.ElementTree',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy', 'PIL', 'cv2'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONE_FILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name='ClawVolt', debug=False, strip=False, upx=True,
        runtime_tmpdir=None, console=False, uac_admin=True,
        icon=os.path.join(REPO_ROOT, 'assets', 'icon.ico'))
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
        name='ClawVolt', debug=False, strip=False, upx=True,
        console=False, uac_admin=True,
        icon=os.path.join(REPO_ROOT, 'assets', 'icon.ico'))
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=True, name='ClawVolt')
