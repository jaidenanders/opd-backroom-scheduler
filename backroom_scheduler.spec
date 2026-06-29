# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for OPD Backroom Scheduler.
#
# Build:
#   pip install pyinstaller
#   pyinstaller backroom_scheduler.spec
#
# Output: dist/BackroomScheduler/  (folder with exe + dependencies)
# Or set onefile=True below for a single .exe (slower to start, easier to share).

import sys

onefile = False   # change to True for a single-file .exe

a = Analysis(
    ["opd_backroom_scheduler/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        # tkinter sub-modules that PyInstaller sometimes misses
        "tkinter",
        "tkinter.ttk",
        "tkinter.messagebox",
        "tkinter.colorchooser",
        # stdlib modules used at runtime
        "sqlite3",
        "json",
        "re",
        "collections",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Cut unnecessary weight
        "matplotlib", "numpy", "pandas", "PIL", "cv2",
        "scipy", "sklearn", "IPython", "jupyter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name="BackroomScheduler",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,          # no terminal window on Windows
        icon=None,              # replace with "assets/icon.ico" if you add one
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="BackroomScheduler",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        icon=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="BackroomScheduler",
    )
