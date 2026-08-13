"""
debug_panel.py
==============
Modo depuración del monitor de precios de Todocolección.

Muestra, para el último correo procesado:

- Asunto y remitente.
- Porcentaje de descuento detectado.
- Nivel de confianza del parser (con color: verde/naranja/rojo).
- Qué estrategia se utilizó para cada campo (HTML especializado,
  parser semántico, regex o heurística).
- Botón "Reprocesar último correo": vuelve a ejecutar el parser sobre
  el .eml guardado, sin esperar a que llegue un correo nuevo. Ideal
  para ajustar el parser cuando aparece un formato inesperado: se
  edita utils.py, se pulsa el botón y se ve el resultado al instante.

Integración con el monitor
--------------------------
En el bucle que procesa los correos, justo después de descargar el
mensaje (por ejemplo, los bytes que devuelve IMAP FETCH RFC822):

    from app import utils
    utils.save_last_email(raw_bytes)          # guarda last_email.eml

Y para abrir el panel (ventana independiente, no bloquea si se lanza
en su propio proceso):

    python debug_panel.py                      # usa ./last_email.eml
    python debug_panel.py ruta/al/correo.eml   # o un .eml concreto

También puede incrustarse en una app Tkinter existente creando
`DebugPanel(master, eml_path)` como Toplevel.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import importlib
import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from app import utils
logger = logging.getLogger(__name__)

# Campos mostrados en la tabla de estrategias, con su etiqueta
_FIELDS = (
    ("title", "Título"),
    ("old_price", "Precio anterior"),
    ("new_price", "Precio nuevo"),
    ("discount_percent", "Descuento %"),
    ("link", "Enlace"),
    ("cover_image_url", "Portada"),
)


def _shorten(value, width: int = 70) -> str:
    text = "—" if value in (None, "") else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


class DebugPanel(tk.Toplevel):
    """Ventana de depuración del parser."""

    def __init__(self, master: tk.Misc, eml_path: Path) -> None:
        super().__init__(master)
        self.eml_path = Path(eml_path)
        self.title("Depuración del parser — Todocolección")
        self.minsize(640, 420)
        self._build_widgets()
        self.refresh()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 3}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)

        self.var_file = tk.StringVar()
        self.var_subject = tk.StringVar()
        self.var_sender = tk.StringVar()
        self.var_percent = tk.StringVar()
        self.var_confidence = tk.StringVar()

        for label, var in (
            ("Archivo:", self.var_file),
            ("Asunto:", self.var_subject),
            ("Remitente:", self.var_sender),
            ("Descuento detectado:", self.var_percent),
        ):
            row = ttk.Frame(header)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=20, font=("", 9, "bold")).pack(side="left")
            ttk.Label(row, textvariable=var, anchor="w").pack(side="left", fill="x", expand=True)

        # La confianza lleva color propio, así que usa un tk.Label normal
        row = ttk.Frame(header)
        row.pack(fill="x")
        ttk.Label(row, text="Confianza:", width=20, font=("", 9, "bold")).pack(side="left")
        self.lbl_confidence = tk.Label(row, textvariable=self.var_confidence, anchor="w")
        self.lbl_confidence.pack(side="left", fill="x", expand=True)

        # Tabla: campo / valor / estrategia utilizada
        columns = ("campo", "valor", "estrategia")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=8)
        self.tree.heading("campo", text="Campo")
        self.tree.heading("valor", text="Valor extraído")
        self.tree.heading("estrategia", text="Estrategia utilizada")
        self.tree.column("campo", width=120, anchor="w")
        self.tree.column("valor", width=280, anchor="w")
        self.tree.column("estrategia", width=220, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=8)

        # Pie: botón de reprocesado + estado
        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(
            footer, text="Reprocesar último correo", command=self.refresh
        ).pack(side="left")
        self.var_status = tk.StringVar()
        ttk.Label(footer, textvariable=self.var_status, foreground="#666").pack(
            side="left", padx=12
        )

    # ------------------------------------------------------------------
    # Lógica de reprocesado
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """(Re)parsea el .eml y actualiza la ventana."""
        # Recargar utils por si se ha editado el parser entre pulsaciones:
        # así el botón sirve para iterar sobre el código sin reiniciar.
        try:
            importlib.reload(utils)
        except Exception as exc:  # noqa: BLE001 - un error de sintaxis no debe cerrar el panel
            self.var_status.set(f"Error recargando utils.py: {exc}")
            return

        if not self.eml_path.exists():
            self.var_status.set(
                f"No existe {self.eml_path}. El monitor debe llamar a "
                "utils.save_last_email(raw_bytes) al procesar cada correo."
            )
            return

        try:
            msg = utils.load_email_file(self.eml_path)
            alert = utils.parse_alert_email(msg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error reprocesando el correo")
            self.var_status.set(f"Error al parsear: {exc}")
            return

        self.var_file.set(str(self.eml_path))
        self.var_subject.set(_shorten(utils.decode_header_value(msg.get("Subject", "")), 90))
        self.var_sender.set(_shorten(utils.decode_header_value(msg.get("From", "")), 90))
        self.var_percent.set(
            "—" if alert.discount_percent is None else f"{alert.discount_percent:g} %"
        )

        # Confianza con semáforo de color
        conf = alert.confidence
        self.var_confidence.set(f"{conf:.2f}" + ("  ⚠ baja" if conf < 0.6 else ""))
        self.lbl_confidence.configure(
            fg="#1a7f37" if conf >= 0.8 else ("#b58900" if conf >= 0.6 else "#c00")
        )

        # Rellenar la tabla campo / valor / estrategia
        self.tree.delete(*self.tree.get_children())
        for key, label in _FIELDS:
            value = getattr(alert, key)
            strategy = alert.sources.get(
                key, "no detectado" if value in (None, "") else "?"
            )
            self.tree.insert("", "end", values=(label, _shorten(value, 45), strategy))

        self.var_status.set("Reprocesado correctamente.")


def show_debug_panel(eml_path: str | Path | None = None) -> None:
    """Abre el panel como ventana principal (uso desde línea de comandos)."""
    path = Path(eml_path) if eml_path else Path(utils.LAST_EMAIL_FILENAME)
    root = tk.Tk()
    root.withdraw()  # la ventana útil es el Toplevel
    panel = DebugPanel(root, path)
    panel.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    show_debug_panel(sys.argv[1] if len(sys.argv) > 1 else None)
