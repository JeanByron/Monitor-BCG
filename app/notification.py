"""
notification.py
===============
Notificaciones nativas de Windows 10 (Centro de actividades).

Implementación ROBUSTA en cascada: si un mecanismo falla, se intenta el
siguiente, y todo queda registrado en el log con el motivo del fallo.

    1. winotify (toasts WinRT nativos; clic abre el anuncio).
    2. PowerShell + WinRT directo (sin dependencias: funciona aunque
       winotify no esté instalado o falle, p. ej. en un .exe empaquetado
       sin el paquete).
    3. Globo de la bandeja del sistema (callback opcional que provee la
       GUI): último recurso visible para el usuario.

En sistemas que no sean Windows la notificación solo se registra en el
log, lo que facilita el desarrollo y las pruebas.

Nota histórica: la versión anterior dependía EXCLUSIVAMENTE de winotify;
si el paquete no estaba instalado (era el caso), las notificaciones
"funcionaban" solo en el log y el usuario no veía nada. Ahora el nivel 2
garantiza el toast en cualquier Windows con PowerShell (todos).
"""

from __future__ import annotations

import base64
import logging
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape

from app.config import resource_path
from app.utils import LotAlert, PriceAlert, format_price

logger = logging.getLogger(__name__)

# Import tolerante: en Windows con winotify instalado funcionará;
# en otros entornos el programa sigue arrancando.
# OJO: no avisar aquí con logger.warning — este import ocurre ANTES de
# que main.py configure el logging. El aviso se emite en Notifier.__init__().
try:
    from winotify import Notification, audio  # type: ignore

    _WINOTIFY_OK = True
except ImportError:  # pragma: no cover - entorno sin winotify
    _WINOTIFY_OK = False

_IS_WINDOWS = sys.platform.startswith("win")

# AppUserModelID SIN acentos: los caracteres no ASCII en el app_id han
# dado problemas de codificación con los scripts PowerShell que generan
# los toasts. El nombre visible se define en el propio toast.
APP_ID = "BCG.Monitor"
ICON_PATH: Path = resource_path("assets/icon.png")

# Separación mínima entre toasts consecutivos. Varias notificaciones "a
# la vez" (p. ej. varias bajadas en el mismo lote de correos) lanzaban
# procesos de toast solapados y Windows descartaba parte de ellos; con
# la cola serializada cada aviso se muestra completo antes del siguiente.
TOAST_SPACING_SECONDS = 2.0


def toast_available() -> bool:
    """True si hay ALGÚN mecanismo de toast nativo disponible."""
    return _WINOTIFY_OK or _IS_WINDOWS


def _ps_toast(
    title: str,
    body: str,
    launch: str = "",
    silent: bool = False,
    image: Optional[Path] = None,
) -> bool:
    """
    Nivel 2: toast nativo vía PowerShell + WinRT, sin dependencias.

    Construye el XML del toast y lo muestra con
    ToastNotificationManager. El script se pasa con -EncodedCommand
    (UTF-16LE en base64) para evitar cualquier problema de comillas,
    acentos o caracteres especiales.
    """
    if not _IS_WINDOWS:
        return False

    lines = "".join(f"<text>{escape(ln)}</text>" for ln in body.split("\n") if ln)
    icon_xml = (
        f'<image placement="appLogoOverride" src="{escape(ICON_PATH.as_uri())}"/>'
        if ICON_PATH.exists()
        else ""
    )
    # Portada del libro como imagen "hero" (banner grande del toast)
    if image is not None and image.exists():
        icon_xml += f'<image placement="hero" src="{escape(image.as_uri())}"/>'
    launch_attr = f' activationType="protocol" launch="{escape(launch, {chr(34): "&quot;"})}"' if launch else ""
    audio_xml = '<audio silent="true"/>' if silent else ""
    toast_xml = (
        f"<toast{launch_attr}>"
        f'<visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text>{lines}{icon_xml}"
        f"</binding></visual>{audio_xml}</toast>"
    )

    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$doc.LoadXml(@'\n{toast_xml}\n'@)\n"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $doc\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier('{APP_ID}').Show($toast)\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-EncodedCommand", encoded,
            ],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            logger.error(
                "Toast por PowerShell falló (código %d): %s",
                completed.returncode,
                completed.stderr.decode(errors="replace").strip()[:400],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - nunca tumbar el monitor por un toast
        logger.error("Toast por PowerShell falló: %s", exc)
        return False


class Notifier:
    """
    Encargado de mostrar las notificaciones (precio y lotes).

    `fallback` es un callable opcional `(titulo, mensaje) -> None` que la
    GUI conecta al globo de la bandeja del sistema; se usa como último
    recurso si todos los mecanismos de toast fallan.
    """

    def __init__(
        self,
        enable_sound: bool = True,
        auto_open_link: bool = False,
        fallback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.enable_sound = enable_sound
        self.auto_open_link = auto_open_link
        self.fallback = fallback

        # Cola FIFO + hilo daemon: los toasts se muestran de uno en uno
        # con TOAST_SPACING_SECONDS de separación. Sin esto, una ráfaga
        # (varias bajadas detectadas en la misma revisión) perdía avisos.
        self._queue: "queue.Queue[tuple[str, str, str, Optional[Path]]]" = queue.Queue()
        self._worker = threading.Thread(
            target=self._drain_queue, name="toast-queue", daemon=True
        )
        self._worker.start()

        if not _WINOTIFY_OK:
            if _IS_WINDOWS:
                logger.warning(
                    "winotify no está disponible: se usará el mecanismo de "
                    "respaldo por PowerShell. Para el mecanismo principal: "
                    "pip install winotify (y reconstruir el .exe si usas "
                    "PyInstaller)."
                )
            else:
                logger.warning(
                    "Entorno sin Windows: las notificaciones solo se "
                    "registrarán en el log."
                )

    # ------------------------------------------------------------------
    # Cola de envío
    # ------------------------------------------------------------------
    def _show_toast(
        self,
        title: str,
        body: str,
        launch: str = "",
        image: Optional[Path] = None,
    ) -> None:
        """Encola el toast; el hilo de la cola lo muestra en orden."""
        self._queue.put((title, body, launch, image))

    def _drain_queue(self) -> None:
        """Hilo daemon: muestra los toasts encolados de uno en uno."""
        while True:
            title, body, launch, image = self._queue.get()
            try:
                backend = self._show_toast_now(title, body, launch, image)
                logger.info(
                    "Notificación mostrada mediante: %s%s",
                    backend,
                    " (con portada)" if image else "",
                )
            except Exception as exc:  # noqa: BLE001 - la cola nunca debe morir
                logger.error("Error mostrando la notificación: %s", exc)
            finally:
                self._queue.task_done()
            time.sleep(TOAST_SPACING_SECONDS)

    # ------------------------------------------------------------------
    # Núcleo: cascada de mecanismos
    # ------------------------------------------------------------------
    def _show_toast_now(
        self,
        title: str,
        body: str,
        launch: str = "",
        image: Optional[Path] = None,
    ) -> str:
        """
        Muestra un toast probando cada mecanismo en orden.

        `image` (opcional) es la portada del libro: winotify la usa como
        icono grande y el nivel PowerShell como imagen "hero".

        Devuelve el nombre del mecanismo que funcionó ("winotify",
        "powershell", "bandeja" o "log") para el registro.
        """
        # 1) winotify — icono SIEMPRE el logo de la app
        if _WINOTIFY_OK:
            try:
                toast = Notification(
                    app_id=APP_ID,
                    title=title,
                    msg=body,
                    icon=str(ICON_PATH) if ICON_PATH.exists() else "",
                    launch=launch or "",
                    duration="short",
                )
                if self.enable_sound:
                    toast.set_audio(audio.Default, loop=False)
                toast.show()
                return "winotify"
            except Exception as exc:  # noqa: BLE001
                logger.error("winotify falló (%s); probando PowerShell.", exc)

        # 2) PowerShell + WinRT
        if _ps_toast(
            title, body, launch=launch, silent=not self.enable_sound, image=image
        ):
            return "powershell"

        # 3) Globo de la bandeja (provisto por la GUI)
        if self.fallback is not None:
            try:
                self.fallback(title, body)
                return "bandeja"
            except Exception as exc:  # noqa: BLE001
                logger.error("Globo de bandeja falló: %s", exc)

        logger.error(
            "Ningún mecanismo de notificación funcionó; el aviso queda "
            "solo en el log."
        )
        return "log"

    # ------------------------------------------------------------------
    # Notificaciones concretas
    # ------------------------------------------------------------------
    def notify_price_drop(
        self, alert: PriceAlert, extra_line: Optional[str] = None
    ) -> None:
        """
        Toast de bajada de precio con el formato corto requerido:

            📚 Biblioteca Clásica Gredos
            Plutarco - Vidas Paralelas II
            40 € → 4 €
            Descuento: 90 %
            [Tomo BCG nº 86 — Plutarco]   ← extra_line opcional (colección)
        """
        title = "📚 Biblioteca Clásica Gredos"
        discount = (
            f"{alert.discount_percent:.0f} %" if alert.discount_percent is not None else "?"
        )
        body = (
            f"{alert.title}\n"
            f"{format_price(alert.old_price)} → {format_price(alert.new_price)}\n"
            f"Descuento: {discount}"
        )
        if extra_line:
            body += f"\n{extra_line}"
        logger.info("NOTIFICACIÓN: %s | %s", title, body.replace("\n", " | "))
        # Sin portada del tomo: solo el logo de la app (petición 2026-07-26).
        self._show_toast(title, body, launch=alert.link or "")

        # Apertura automática del enlace (desactivada por defecto)
        if self.auto_open_link and alert.link:
            try:
                webbrowser.open(alert.link)
            except Exception as exc:  # noqa: BLE001
                logger.error("No se pudo abrir el enlace automáticamente: %s", exc)

    def notify_lot(self, lot: LotAlert) -> None:
        """
        Toast de LOTE de libros (5 o más, según configuración):

            📦 Lote de 7 libros — Biblioteca Clásica
            LOTE 7 LIBROS BIBLIOTECA CLÁSICA GREDOS
            Precio: 45 €
        """
        title = f"📦 Lote de {lot.book_count} libros — Biblioteca Clásica"
        price_line = f"Precio: {format_price(lot.price)}" if lot.price is not None else ""
        body = "\n".join(ln for ln in (lot.title, price_line) if ln)
        logger.info("NOTIFICACIÓN LOTE: %s | %s", title, body.replace("\n", " | "))
        self._show_toast(title, body, launch=lot.link or "")

    def notify_info(self, title: str, message: str, link: Optional[str] = None) -> None:
        """Notificación genérica (por ejemplo, errores de conexión persistentes)."""
        logger.info("NOTIFICACIÓN INFO: %s - %s", title, message)
        self._show_toast(title, message, launch=link or "")
