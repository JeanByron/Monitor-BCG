"""
main.py
=======
Punto de entrada de la aplicación "Monitor BCG".

Responsabilidades:
- Configurar el logging (consola + log.txt junto al ejecutable).
- Cargar la configuración y abrir la base de datos.
- Crear la aplicación Qt y la ventana principal.
- Arrancar el monitor automáticamente al iniciar.

Para ejecutar en desarrollo:
    python main.py

Argumentos:
    --tray   Arrancar en SEGUNDO PLANO (solo bandeja, sin abrir la
             ventana). Es el modo que usa el arranque con Windows.
    bcgmonitor://<sección>
             Enlace de una notificación (lo pasa Windows al pulsarla):
             abre la aplicación por esa sección. Si ya estaba en marcha,
             el proceso nuevo solo le pasa el encargo y se cierra.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication

from app import autostart, deeplink
from app.config import Config, app_dir
from app.database import Database
from app.gui import MainWindow

LOG_PATH = app_dir() / "log.txt"


def setup_logging() -> None:
    """
    Configura el registro de la aplicación.

    - `log.txt` rotativo (máx. 2 MB × 3 archivos) para no crecer sin límite.
    - Salida también por consola en desarrollo.
    Registra: inicio, errores, correos leídos, descuentos detectados
    y notificaciones enviadas (los módulos emiten esos eventos).
    """
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)


def revisar_carpeta(db: Database) -> str | None:
    """
    Detecta que la aplicación ha cambiado de carpeta y lo deja en el log.

    La carpeta anterior se guarda en `meta.ruta_app`. No hace falta para
    reparar nada —el arranque con Windows y el enlace `bcgmonitor:` se
    reajustan solos en cada arranque—, pero SÍ para poder explicarlo:
    tras una mudanza, los avisos dejan de llegar hasta el primer
    arranque manual, y sin esta línea en el log no hay forma de atar una
    cosa con la otra (2026-08-13, cambio a `Desktop\\Proyectos\\`).

    Devuelve la carpeta anterior si había cambiado, o None.
    """
    actual = str(app_dir())
    try:
        anterior = db.get_meta("ruta_app")
        if anterior != actual:
            db.set_meta("ruta_app", actual)
    except Exception as exc:  # noqa: BLE001 - nunca impedir el arranque
        logging.warning("No se pudo comprobar la carpeta de la aplicación: %s",
                        exc)
        return None
    if anterior and anterior != actual:
        logging.warning(
            "La aplicación ha CAMBIADO de carpeta: %s → %s. Se reajustan el "
            "arranque con Windows y los enlaces bcgmonitor:.", anterior, actual
        )
        return anterior
    return None


def main() -> int:
    """Arranque de la aplicación."""
    setup_logging()
    logging.info("=" * 60)
    logging.info("Inicio de Monitor BCG")

    # Enlace de una notificación ("bcgmonitor://historial"): si la
    # aplicación ya está en marcha —lo normal, vive en la bandeja— se le
    # pasa el encargo y este proceso se va sin abrir una segunda copia.
    seccion = deeplink.seccion_de(sys.argv[1:])
    if seccion is not None:
        QApplication(sys.argv)          # QLocalSocket necesita la app Qt
        if deeplink.avisar_a_la_instancia_viva(seccion):
            logging.info("Enlace '%s' entregado a la instancia en marcha.",
                         seccion)
            return 0

    config = Config.load()
    db = Database()
    revisar_carpeta(db)

    app = QApplication.instance() or QApplication(sys.argv)
    # Muy importante: al cerrar la última ventana NO salir (vivimos en la bandeja)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Monitor BCG")

    window = MainWindow(config, db)

    # Canal por el que llegan los enlaces de las notificaciones, y
    # registro del esquema (se reescribe en cada arranque: así sigue
    # apuntando bien aunque el programa cambie de carpeta).
    deeplink.registrar()
    servidor = deeplink.escuchar(window.abrir_seccion)

    # Renombrado a "Monitor BCG" (2026-08-04): si el arranque con Windows
    # seguía apuntando al nombre anterior, se pasa al nuevo. Sin esto
    # quedarían las dos entradas y arrancarían dos copias.
    autostart.migrar_nombre()

    # Y la RUTA: la clave Run guarda la ruta entera de main.py, así que
    # mover el proyecto de carpeta dejaba a Windows lanzando un archivo
    # que ya no existe —en silencio—. Se reajusta en cada arranque, igual
    # que el enlace bcgmonitor: de arriba.
    autostart.sincronizar_ruta(config.start_with_windows)

    # Con --tray (arranque con Windows) la app nace en la bandeja del
    # sistema, sin abrir la ventana; el monitor vigila igualmente.
    start_in_tray = "--tray" in sys.argv[1:]
    if start_in_tray:
        logging.info("Arranque en segundo plano (--tray): ventana oculta.")
    else:
        window.show()
    if seccion is not None:          # se abrió DESDE una notificación
        window.abrir_seccion(seccion)

    # Arrancar la vigilancia automáticamente al iniciar el programa
    window.start_monitor()

    codigo = app.exec()
    if servidor is not None:
        servidor.close()
    # El buscador de textos deja un WAL abierto sobre BDtomos/textos.db
    from app import rag

    rag.cerrar_indice()
    return codigo


if __name__ == "__main__":
    sys.exit(main())
