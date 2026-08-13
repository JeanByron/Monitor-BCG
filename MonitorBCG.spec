# -*- mode: python ; coding: utf-8 -*-
# Empaquetado con PyInstaller:  pyinstaller MonitorBCG.spec
#
# IMPORTANTE: config.json NO se empaqueta dentro del .exe (contiene la
# contraseña de aplicación). El programa lo busca JUNTO al ejecutable:
# copia config.json a la carpeta del .exe (y allí vivirán también
# tc_monitor.db, log.txt y BDtomos/).
#
# Los iconos SÍ viajan dentro: `config.resource_path` los busca primero
# junto al .exe y, si no están, en el paquete (sys._MEIPASS), así los
# avisos conservan el logo sin copiar nada a mano.
#
# Tampoco viajan `tests/`, `tools/` ni `docs/`: PyInstaller solo sigue lo
# que main.py importa, y nada de eso se importa.
#
# NO EXCLUIR MÓDULOS DE Qt. La aplicación usa seis (QtCore, QtGui,
# QtWidgets, QtNetwork, QtWebEngineCore, QtWebEngineWidgets), pero
# QtWebEngine está construido sobre Qt Quick y carga QtQml/QtQuick POR
# DENTRO. Quitarlos adelgaza el .exe y rompe el lector de precios —y el
# fallo no se ve hasta consultar una publicación, ya empaquetado.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icon.png', 'assets'), ('assets/icon.ico', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tkinter solo lo usa tools/debug_panel.py, que no entra aquí.
    excludes=['tkinter', 'pytest', '_pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MonitorBCG',
    icon='assets/icon.ico',
    version='assets/version_info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
