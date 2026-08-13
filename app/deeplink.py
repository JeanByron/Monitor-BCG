"""
deeplink.py
===========
Enlaces propios de la aplicación (`bcgmonitor://…`).

Sirven para que al pulsar una NOTIFICACIÓN de Windows se abra el
programa por donde toca — el resumen del día lleva a Historial — en vez
de abrir el navegador o no hacer nada.

Cómo funciona:

1. El esquema `bcgmonitor:` se registra en el usuario actual
   (`HKCU\\Software\\Classes\\bcgmonitor`), sin permisos de
   administrador, igual que el arranque con Windows.
2. El toast lleva `launch="bcgmonitor://historial"`. Al pulsarlo,
   Windows ejecuta el comando registrado con esa URL como argumento.
3. Si la aplicación YA está en marcha (lo normal: vive en la bandeja),
   ese segundo proceso no abre otra ventana: le pasa la orden a la
   instancia viva por un socket local y se cierra.

Fuera de Windows todo es inofensivo: registrar no hace nada y el
enlace se ignora.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ESQUEMA = "bcgmonitor"
# Nombre del socket local que comparte la instancia viva
SERVIDOR = "MonitorBCG-deeplink"

# Secciones a las que puede llevar un enlace
SECCIONES = ("historial", "notificadas", "precios", "lotes", "coleccion",
             "textos", "buscar", "ventana")

_ES_WINDOWS = sys.platform.startswith("win")
_CLAVE = rf"Software\Classes\{ESQUEMA}"


def enlace(seccion: str) -> str:
    """URL de la aplicación para esa sección."""
    return f"{ESQUEMA}://{seccion}"


def seccion_de(argumentos) -> Optional[str]:
    """
    Sección pedida en la línea de órdenes, o None.

    Windows pasa la URL entera como argumento ("bcgmonitor://historial").
    """
    for arg in argumentos:
        texto = str(arg).strip().lower()
        if texto.startswith(f"{ESQUEMA}:"):
            destino = texto.split(":", 1)[1].strip("/")
            return destino if destino in SECCIONES else "ventana"
    return None


def _comando() -> str:
    """Comando que Windows ejecutará al pulsar el enlace."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interprete = pythonw if pythonw.exists() else python
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{interprete}" "{main_py}" "%1"'


def comando_registrado() -> Optional[str]:
    """Comando que Windows tiene hoy apuntado para `bcgmonitor:`, o None."""
    if not _ES_WINDOWS:
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, rf"{_CLAVE}\shell\open\command"
        ) as clave:
            return str(winreg.QueryValueEx(clave, "")[0])
    except OSError:
        return None


def apunta_aqui() -> bool:
    """
    True si el enlace registrado lanza ESTE main.py (o este .exe).

    Se compara lo que se LANZA, no el intérprete, por la misma razón que
    en `autostart.apunta_aqui`: en el equipo conviven varios Python y el
    que vale es el que tiene PySide6.
    """
    from app.autostart import _objetivo

    registrado = comando_registrado()
    if registrado is None:
        return False
    return _objetivo(registrado) == _objetivo(_comando())


def registrar() -> bool:
    """
    Registra el esquema `bcgmonitor:` para el usuario actual.

    Es idempotente y se reescribe en cada arranque: así el enlace sigue
    apuntando al sitio correcto aunque el programa se mueva de carpeta
    o se pase de desarrollo al .exe empaquetado.
    """
    if not _ES_WINDOWS:
        return False
    import winreg

    anterior = comando_registrado()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CLAVE) as clave:
            winreg.SetValueEx(
                clave, "", 0, winreg.REG_SZ, "URL:Monitor BCG"
            )
            winreg.SetValueEx(clave, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"{_CLAVE}\shell\open\command"
        ) as clave:
            winreg.SetValueEx(clave, "", 0, winreg.REG_SZ, _comando())
        if anterior and anterior != _comando():
            # Deja constancia del cambio de carpeta: hasta este arranque,
            # pulsar una notificación lanzaba la ruta vieja y no abría nada.
            logger.warning(
                "El enlace %s: apuntaba a otro sitio (%s); se actualiza a %s",
                ESQUEMA, anterior, _comando(),
            )
        return True
    except OSError as exc:
        logger.warning("No se pudo registrar el enlace %s: %s", ESQUEMA, exc)
        return False


def avisar_a_la_instancia_viva(seccion: str, espera_ms: int = 400) -> bool:
    """
    Manda la orden a la aplicación que ya esté corriendo.

    Devuelve True si había una instancia viva y aceptó el encargo (y
    entonces este proceso debe cerrarse sin abrir nada).
    """
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(SERVIDOR)
    if not socket.waitForConnected(espera_ms):
        return False
    socket.write(seccion.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(espera_ms)
    # Esperar el cierre: sin esto el proceso podía morirse antes de que
    # el otro lado leyera el encargo.
    socket.disconnectFromServer()
    socket.waitForDisconnected(espera_ms)
    logger.info("Orden '%s' entregada a la instancia en marcha.", seccion)
    return True


def escuchar(destinatario) -> Optional[object]:
    """
    Abre el socket local por el que llegan los enlaces.

    `destinatario(seccion)` se llama en el hilo de la interfaz cada vez
    que otro proceso pulsa una notificación. Devuelve el servidor (hay
    que conservarlo vivo) o None si no se pudo abrir.
    """
    from PySide6.QtNetwork import QLocalServer

    QLocalServer.removeServer(SERVIDOR)     # restos de un cierre brusco
    servidor = QLocalServer()
    if not servidor.listen(SERVIDOR):
        logger.warning(
            "No se pudo abrir el canal de enlaces: %s", servidor.errorString()
        )
        return None

    pendientes: list = []      # sockets vivos hasta que hablen

    def nueva_conexion() -> None:
        socket = servidor.nextPendingConnection()
        if socket is None:
            return
        pendientes.append(socket)

        def leer() -> None:
            datos = bytes(socket.readAll()).decode("utf-8", "replace").strip()
            if not datos:
                return
            socket.disconnectFromServer()
            if socket in pendientes:
                pendientes.remove(socket)
            logger.info("Enlace recibido: %s", datos)
            destinatario(datos)

        # Por señal, no esperando a pelo: el dato puede llegar ya en el
        # buffer o un instante después, y con waitForReadyRead se perdía.
        socket.readyRead.connect(leer)
        socket.disconnected.connect(
            lambda: pendientes.remove(socket) if socket in pendientes else None
        )
        if socket.bytesAvailable():
            leer()

    servidor.newConnection.connect(nueva_conexion)
    return servidor
