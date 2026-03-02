# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for FAM Market Manager."""

import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Collect data files (templates, JS, etc.) from packages that need them at runtime
folium_datas = collect_data_files('folium')
branca_datas = collect_data_files('branca')
xyzservices_datas = collect_data_files('xyzservices')
certifi_datas = collect_data_files('certifi')

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
        # Include the app icon for the window title bar and taskbar
        (os.path.join('fam', 'ui', 'fam_icon.ico'), os.path.join('fam', 'ui')),
    ] + folium_datas + branca_datas + xyzservices_datas + certifi_datas,
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
        'folium.utilities',
        'pgeocode',
        'branca',
        'branca.element',
        'xyzservices',
        # folium/branca dependencies
        'jinja2',
        'jinja2.ext',
        'requests',
        'certifi',
        'charset_normalizer',
        'idna',
        'urllib3',
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
    icon='fam_icon.ico',  # .exe icon shown in File Explorer
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
