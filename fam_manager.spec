# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for FAM Market Manager."""

import os

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Include the dropdown arrow image used by QSS stylesheet
        (os.path.join('fam', 'ui', '_dropdown_arrow.png'), os.path.join('fam', 'ui')),
        # Include the FAM logo for the sidebar
        (os.path.join('fam', 'ui', '_fam_logo_white.png'), os.path.join('fam', 'ui')),
        # Include the tiled background pattern for the sidebar
        (os.path.join('fam', 'ui', '_fam_background.jpg'), os.path.join('fam', 'ui')),
    ],
    hiddenimports=[
        # matplotlib backends needed at runtime
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_agg',
        # PySide6 modules that may not be auto-detected
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        # Geolocation heat map support
        'folium',
        'folium.plugins',
        'pgeocode',
        'branca',
        'xyzservices',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unused matplotlib backends to reduce size
        'matplotlib.backends.backend_tkagg',
        'matplotlib.backends.backend_gtk3agg',
        'matplotlib.backends.backend_gtk4agg',
        'matplotlib.backends.backend_wxagg',
        'matplotlib.backends.backend_cairo',
        # Exclude test frameworks
        'pytest',
        # Exclude tkinter (not needed)
        'tkinter',
        '_tkinter',
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
    [],
    exclude_binaries=True,
    name='FAM Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window — windowed app
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FAM Manager',
)
