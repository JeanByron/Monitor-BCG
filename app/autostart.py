"""
autostart.py
============
Arranque automático con Windows.

Registra (o retira) el programa en la clave Run del usuario actual:

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

Sin permisos de administrador y sin tocar la carpeta de inicio. El
comando registrado depende del modo de ejecución:

- Empaquetado con PyInstaller (`sys.frozen`): la ruta del .exe.
- En desarrollo: `pythonw.exe main.py` (pythonw = sin ventana de
  consola; si no existe, se usa python.exe).

En sistemas que no sean Windows todas las funciones son inofensivas
(devuelven False / no hacen nada), para poder desarrollar y probar.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "MonitorBCG"

# Nombres que tuvo el programa antes. Hay que RETIRARLOS al escribir el
# nuevo: si no, Windows conservaría los dos y arrancaría dos copias de
# la aplicación al iniciar sesión (2026-08-04, renombrado a Monitor BCG).
_VALORES_ANTIGUOS = ("MonitorTodocoleccion",)

_IS_WINDOWS = sys.platform.startswith("win")


def _command() -> str:
    """
    Comando a registrar en la clave Run.

    Siempre con `--tray`: al iniciar sesión en Windows la aplicación
    nace en segundo plano (bandeja del sistema), sin abrir la ventana.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    # Desarrollo: preferir pythonw.exe (sin consola)
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{interpreter}" "{main_py}" --tray'


def _valor_registrado() -> tuple[Optional[str], Optional[str]]:
    """
    Nombre y comando que hoy tiene la clave Run, o (None, None).

    Mira también los nombres antiguos: quien activó el arranque antes
    del renombrado sigue teniéndolo con el nombre viejo.
    """
    if not _IS_WINDOWS:
        return None, None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            for nombre in (_VALUE_NAME, *_VALORES_ANTIGUOS):
                try:
                    return nombre, str(winreg.QueryValueEx(key, nombre)[0])
                except FileNotFoundError:
                    continue
    except OSError:
        return None, None
    return None, None


def _partes(comando: str) -> list[str]:
    """Rutas entrecomilladas del comando: intérprete y, si la hay, main.py."""
    return re.findall(r'"([^"]+)"', comando)


def _objetivo(comando: str) -> Optional[str]:
    """
    Qué LANZA ese comando: main.py en desarrollo, el .exe empaquetado.

    Se mira la forma del propio comando, no el modo en que corremos
    ahora: así se detecta también el salto de desarrollo a .exe (y al
    revés), que cambia el número de rutas.
    """
    partes = _partes(comando)
    if not partes:
        return None
    ruta = partes[1] if len(partes) > 1 else partes[0]
    return ruta.casefold()


def _interprete(comando: str) -> Optional[str]:
    """El programa que Windows ejecuta (pythonw.exe, o el propio .exe)."""
    partes = _partes(comando)
    return partes[0] if partes else None


def apunta_aqui() -> bool:
    """
    True si el arranque registrado lanza ESTE main.py (o este .exe).

    Es lo que distingue "activado" de "activado pero apuntando a la
    carpeta de antes": tras mover el proyecto, la clave Run conserva la
    ruta vieja y Windows lanza un archivo que ya no existe — sin ningún
    aviso, porque `pythonw.exe` muere en silencio.

    NO se compara el INTÉRPRETE, solo lo que se lanza. En este equipo
    conviven varios Python (Miniconda y uno del sistema) y el registrado
    es el que tiene instalado PySide6; si se comparase el comando entero,
    lanzar main.py o cualquier herramienta UNA vez desde otro Python
    repuntaría el arranque a ese otro —que puede no tener las
    dependencias— y la aplicación dejaría de arrancar con Windows sin
    decir por qué (2026-08-13). Lo que sí obliga a reescribir es que el
    intérprete registrado haya DESAPARECIDO.
    """
    nombre, registrado = _valor_registrado()
    if registrado is None or nombre != _VALUE_NAME:
        return False
    if _objetivo(registrado) != _objetivo(_command()):
        return False
    if "--tray" not in registrado:      # arrancaría abriendo la ventana
        return False
    interprete = _interprete(registrado)
    return bool(interprete) and Path(interprete).exists()


def is_enabled() -> bool:
    """
    True si el programa ya está registrado para arrancar con Windows.

    Cuenta también un nombre antiguo: quien lo tenía activado antes del
    renombrado lo sigue teniendo, y la casilla debe salir marcada.
    """
    return _valor_registrado()[1] is not None


def _borrar_antiguos(key) -> bool:
    """Retira los nombres de versiones anteriores. True si había alguno."""
    import winreg

    habia = False
    for nombre in _VALORES_ANTIGUOS:
        try:
            winreg.DeleteValue(key, nombre)
            habia = True
            logger.info("Retirado el arranque con el nombre antiguo '%s'.",
                        nombre)
        except FileNotFoundError:
            pass
    return habia


def migrar_nombre() -> bool:
    """
    Pasa el arranque automático al nombre actual, si hacía falta.

    Se llama al arrancar: sin esto, quien ya tenía el arranque activado
    seguiría viéndolo en el Administrador de tareas con el nombre viejo.
    Devuelve True si se migró algo.
    """
    if not _IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if not _borrar_antiguos(key):
                return False
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _command())
            logger.info("Arranque con Windows migrado a '%s'.", _VALUE_NAME)
            return True
    except OSError as exc:
        logger.error("No se pudo migrar la clave Run: %s", exc)
        return False


def sincronizar_ruta(deseado: Optional[bool] = None) -> bool:
    """
    Deja el arranque con Windows apuntando a ESTA carpeta.

    Se llama en CADA arranque, igual que `deeplink.registrar()`. Hace
    falta porque la clave Run guarda la ruta ENTERA de main.py y solo se
    reescribía al aceptar el diálogo de Configuración: al mover el
    proyecto de carpeta, Windows seguía lanzando la ruta vieja y —si ya
    no existe— no arrancaba nada NI avisaba de nada (pythonw.exe muere
    en silencio), así que el usuario solo lo notaba al echar de menos
    los avisos. Verificado con el cambio a `Desktop\\Proyectos\\`
    (2026-08-13).

    `deseado` es lo que dice la configuración (`start_with_windows`): si
    está pedido y la clave falta —copiar el proyecto a otro equipo o a
    otro usuario de Windows deja el config.json pero no el registro—, se
    vuelve a poner. Nunca RETIRA nada: quitar el arranque es una acción
    explícita del usuario (`set_enabled(False)`).

    Devuelve True si hubo que tocar el registro.
    """
    if not _IS_WINDOWS:
        return False

    nombre, registrado = _valor_registrado()
    if registrado is None:
        if not deseado:
            return False
        logger.warning(
            "El arranque con Windows estaba pedido en la configuración "
            "pero no había clave; se vuelve a registrar."
        )
        return set_enabled(True)

    if apunta_aqui():
        return False

    logger.warning(
        "El arranque con Windows apuntaba a otro sitio (%s = %s); "
        "se actualiza a %s",
        nombre, registrado, _command(),
    )
    return set_enabled(True)


def set_enabled(enabled: bool) -> bool:
    """
    Activa o desactiva el arranque con Windows.

    Devuelve True si la operación se aplicó (o ya estaba aplicada).
    Nunca lanza: los errores quedan en el log y devuelve False.
    """
    if not _IS_WINDOWS:
        logger.info("Autostart ignorado: no es Windows.")
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            # En ambos casos: fuera los nombres antiguos (si no, quedaría
            # una segunda entrada arrancando otra copia, o el programa
            # seguiría arrancando después de decir que no).
            _borrar_antiguos(key)
            if enabled:
                winreg.SetValueEx(
                    key, _VALUE_NAME, 0, winreg.REG_SZ, _command()
                )
                logger.info("Arranque con Windows ACTIVADO: %s", _command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                    logger.info("Arranque con Windows desactivado.")
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        logger.error("No se pudo modificar la clave Run: %s", exc)
        return False
