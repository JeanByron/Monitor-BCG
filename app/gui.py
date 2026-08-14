"""
gui.py
======
Interfaz gráfica (PySide6) — diseño "Biblioteca Clásica Gredos".

Ventana principal con la estética de un tomo clásico de la colección
(primeras ediciones desde 1970): cubierta símil piel en azul Oxford,
letras y filetes en oro que imitan un BORDADO sobre cuero, tipografía
serif clásica.

Diseño de la versión actual:
- Ventana SIN el marco de Windows (FramelessWindowHint): barra de
  título propia con filetes, botones de minimizar/cerrar dorados,
  esquinas redondeadas y sombra de ventana propia.
- Brillos dorados: el título y los botones irradian un halo dorado
  animado al pasar el ratón (QGraphicsDropShadowEffect + animación).
- "Bordado": los paneles llevan doble borde — filete sólido exterior
  y pespunte discontinuo interior, como hilo de oro sobre la piel.
- Todos los diálogos (configuración, historial, estadísticas y avisos)
  usan la misma encuadernación sin marco.

Comportamiento clave (idéntico a la versión original):
- Al CERRAR la ventana el programa NO termina: se oculta a la bandeja
  del sistema y el monitor sigue funcionando.
- Desde el icono de la bandeja se puede reabrir la ventana o salir.

Contrato con main.py (sin cambios):
    window = MainWindow(config, db); window.show(); window.start_monitor()

Novedades funcionales:
- Contador "Lotes detectados" + notificación de lotes (ver utils.detect_lot).
- Si todos los toasts de Windows fallan, el aviso cae al globo de la
  bandeja del sistema (puente thread-safe mediante señal Qt).
"""

from __future__ import annotations

import importlib
import logging
import math
import random
import re
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QProcess,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTime,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCursor,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app import autostart, notification
from app.config import Config, app_dir, resource_path
from app.database import Database
from app.imap_monitor import ImapMonitor
from app.notification import Notifier, toast_available
from app.utils import LAST_EMAIL_FILENAME, format_price, normalize

# La gestión del dataset es opcional: la GUI funciona aunque falte.
try:
    from app.dataset import DatasetManager

    _DATASET_OK = True
except Exception:  # noqa: BLE001 - import tolerante
    _DATASET_OK = False

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Paleta "tomo Gredos": similpiel azul oscuro + estampación dorada.
#
# Referencia real: la Biblioteca Clásica Gredos va encuadernada en
# cartoné con similpiel AZUL OSCURO — bajo poca luz se ve casi negra y
# con luz directa asoma el azul. Base tomada del Oxford Blue auténtico
# (#002147, Pantone 282) ligeramente ajustado al cuero.
# ----------------------------------------------------------------------
AZUL_OXFORD = "#03142a"        # cubierta (casi negro azulado)
AZUL_OXFORD_CLARO = "#082343"  # donde la luz golpea la piel
AZUL_OXFORD_OSCURO = "#01070f"  # sombra inferior (negro-azul)
AZUL_CAMPO = "#020c1c"          # fondo de campos y tablas
ORO = "#d4af37"                 # dorado metalizado (base)
ORO_CLARO = "#efd88f"           # brillo del pan de oro
ORO_TEXTO = "#e7cd7f"           # texto dorado general
ORO_VIEJO = "#b08d2e"           # filetes y bordes
ORO_APAGADO = "#7d6420"         # bordes secundarios / pespuntes

# Tipografía de TODA la aplicación: Georgia (2026-08-05, a petición del
# usuario). Es una serif de pantalla —trazo grueso, ojo medio grande,
# cifras elzevirianas— y se lee cómoda en tamaños pequeños.
#
# Las RESERVAS no son decorativas, cubren dos huecos reales de Georgia
# (medidos, no supuestos):
#   · Palatino Linotype → GRIEGO POLITÓNICO (ἀ ᾳ ῥ ὧ). Georgia solo
#     trae el griego moderno, y el corpus va lleno de politónico: sin
#     esta reserva, Qt escogería cualquier fuente del sistema y el
#     griego saldría de otro estilo en cada equipo.
#   · Segoe UI Symbol → los glifos de la propia interfaz (▸ ▾ ⧉ ✔ ✖),
#     que Georgia tampoco tiene.
FUENTE = "Georgia"
FUENTES_RESERVA = ("Palatino Linotype", "Segoe UI Symbol", "Cambria")
FONT_STACK = (
    "'Georgia', 'Palatino Linotype', 'Segoe UI Symbol', 'Cambria', "
    "'Book Antiqua', serif"
)


# --- Griego -----------------------------------------------------------
# Georgia trae el griego MODERNO (λόγος) pero NO el politónico
# (ἀ ᾳ ῥ ὧ), que es el que llevan los tomos. Medido con QTextLayout:
# sin reservas, Qt resolvía esas letras con TAHOMA —una fuente de palo
# seco en mitad de una serif— y, aun con la reserva puesta, una misma
# palabra se partía entre dos tipografías: la ἀ de Palatino y el resto
# «λλήλων» de Georgia. Cuatro de cada cinco palabras politónicas salían
# así. Por eso el griego se trata por RACHAS enteras y con su propia
# fuente, no carácter a carácter.
FUENTE_GRIEGA = "Palatino Linotype"
FUENTES_GRIEGAS_RESERVA = ("Cambria", "Segoe UI Symbol", "Times New Roman")
# Palatino tiene la altura-x un 8 % mayor que Georgia (7,70 frente a
# 8,30 a 12 pt): sin compensarlo, el griego se ve más grande que el
# castellano que lo rodea.
_ESCALA_GRIEGA = 7.70 / 8.30

_RANGO_GRIEGO = "Ͱ-Ͽἀ-῿"
# Una racha griega: letras griegas y lo que va ENTRE ellas (espacios,
# comas, el punto alto ·, apóstrofos de elisión y los signos
# combinantes de los acentos).
_RACHA_GRIEGA = re.compile(
    f"[{_RANGO_GRIEGO}]"
    f"[{_RANGO_GRIEGO}̀-ͯ ,.·;’'()\\[\\]-]*"
    f"[{_RANGO_GRIEGO}]"
    f"|[{_RANGO_GRIEGO}]"
)


def partir_por_griego(texto: str) -> list[tuple[str, bool]]:
    """
    Parte el texto en trozos `(fragmento, ¿es griego?)`.

    Sirve para que cada racha griega se pinte ENTERA con una fuente que
    tenga politónico, en vez de dejar que Qt resuelva letra a letra y
    parta la palabra entre dos tipografías.
    """
    if not texto:
        return []
    trozos: list[tuple[str, bool]] = []
    fin = 0
    for m in _RACHA_GRIEGA.finditer(texto):
        if m.start() > fin:
            trozos.append((texto[fin:m.start()], False))
        trozos.append((m.group(0), True))
        fin = m.end()
    if fin < len(texto):
        trozos.append((texto[fin:], False))
    return trozos or [(texto, False)]


def es_griego(texto: str, proporcion: float = 0.25) -> bool:
    """¿Este texto es griego en su mayor parte?"""
    letras = [c for c in texto if c.isalpha()]
    if not letras:
        return False
    griegas = sum(
        1 for c in letras if "Ͱ" <= c <= "Ͽ" or "ἀ" <= c <= "῿"
    )
    return griegas / len(letras) >= proporcion


def fuente_griega(tam: float = 0.0, cursiva: bool = False) -> QFont:
    """La fuente del griego: serif con politónico completo, al tamaño
    justo para que no cante al lado del castellano."""
    f = QFont(FUENTE_GRIEGA)
    f.setFamilies([FUENTE_GRIEGA, *FUENTES_GRIEGAS_RESERVA])
    f.setPointSizeF((tam or 10.5) * _ESCALA_GRIEGA)
    f.setItalic(cursiva)
    f.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def fuente(
    tam: float = 0.0,
    negrita: bool = False,
    espaciado: float = 0.0,
    cursiva: bool = False,
) -> QFont:
    """
    La fuente de la aplicación, ya suavizada.

    Tres ajustes hacen el texto más blando a la vista, y los tres hacen
    falta:

    - `PreferAntialias` pide bordes suavizados aunque el sistema tenga
      el alisado desactivado.
    - `PreferQuality` deja que Qt use el tamaño exacto en vez de saltar
      al cuerpo entero más cercano.
    - `PreferNoHinting` es el que de verdad se nota: sin él, Windows
      DEFORMA cada letra para encajarla en la rejilla de píxeles, y en
      una serif con remates finos como Georgia eso se ve como trazos
      desiguales y un renglón que "vibra". Sin ajuste a rejilla las
      letras quedan algo más difusas pero con su forma real y el
      espaciado parejo, que es lo que descansa la vista al leer.
    """
    f = QFont(FUENTE)
    f.setFamilies([FUENTE, *FUENTES_RESERVA])
    if tam:
        f.setPointSizeF(tam)
    f.setBold(negrita)
    f.setItalic(cursiva)
    f.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if espaciado:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, espaciado)
    return f

# Hoja de estilos global: encuadernación completa. El fondo lo pinta
# SOLO el marco #chrome (las ventanas son translúcidas para poder tener
# esquinas redondeadas y sombra propia sin el marco de Windows).
GREDOS_QSS = f"""
* {{
    font-family: {FONT_STACK};
    font-size: 10.5pt;
}}
QMainWindow, QDialog {{ background: transparent; }}

/* El marco #chrome (LeatherFrame) se pinta a mano: cuero azul con
   grano, brillo dorado que sigue al ratón y filete de borde. */
QFrame#chrome {{ background: transparent; border: none; }}

QLabel {{ color: {ORO_TEXTO}; background: transparent; }}
QLabel#titulo {{ color: {ORO_CLARO}; }}
QLabel#subtitulo {{ color: {ORO}; }}
QLabel#titlebar_text {{
    color: {ORO};
    font-size: 10pt;
    letter-spacing: 2px;
}}
QLabel#mensaje {{ color: {ORO_VIEJO}; font-size: 9pt; }}

/* Panel "bordado": filete sólido fuera, pespunte de hilo dentro */
QFrame#panel {{
    border: 1px solid {ORO_VIEJO};
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.035);
}}
QFrame#stitch {{
    border: 1px dashed {ORO_APAGADO};
    border-radius: 5px;
    background: transparent;
}}
QFrame.filete {{ background: {ORO_VIEJO}; border: none; }}

/* Los GlowButton se pintan a mano (letras que se encienden en oro
   siguiendo al ratón); aquí solo el estado base por si algún botón
   estándar queda sin repintar. */
QPushButton {{
    color: {ORO_TEXTO};
    background: transparent;
    border: 1px solid {ORO_VIEJO};
    border-radius: 5px;
    padding: 7px 14px;
}}
QPushButton:disabled {{ color: #6f6a54; border-color: #4a4630; }}

/* Botones de la barra de título (minimizar / cerrar) */
QPushButton#winbtn, QPushButton#winbtn_close {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px 10px;
    color: {ORO_VIEJO};
    font-size: 11pt;
}}
QPushButton#winbtn:hover {{
    background: rgba(212, 175, 55, 0.22);
    color: {ORO_CLARO};
}}
QPushButton#winbtn_close:hover {{
    background: {ORO};
    color: {AZUL_OXFORD_OSCURO};
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit {{
    background: {AZUL_CAMPO};
    color: {ORO_CLARO};
    border: 1px solid {ORO_APAGADO};
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: {ORO};
    selection-color: {AZUL_OXFORD};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus {{
    border: 1px solid {ORO};
}}

/* Lectura de un pasaje del tomo. Sin esta regla saldría en blanco,
   como todo widget con fondo propio. */
QTextEdit, QTextBrowser {{
    background: {AZUL_CAMPO};
    color: {ORO_CLARO};
    border: 1px solid {ORO_VIEJO};
    border-radius: 6px;
    padding: 8px 10px;
    selection-background-color: {ORO};
    selection-color: {AZUL_OXFORD};
}}

QListWidget {{
    background: {AZUL_CAMPO};
    alternate-background-color: {AZUL_OXFORD};
    color: {ORO_CLARO};
    border: 1px solid {ORO_VIEJO};
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item {{ padding: 4px 6px; }}
QListWidget::item:selected {{ background: {ORO}; color: {AZUL_OXFORD}; }}
QListWidget::item:hover {{ background: rgba(212, 175, 55, 0.18); }}
QCheckBox {{ color: {ORO_TEXTO}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {ORO_VIEJO}; border-radius: 3px;
    background: {AZUL_CAMPO};
}}
QCheckBox::indicator:checked {{ background: {ORO}; }}
QCheckBox::indicator:hover {{ border: 1px solid {ORO_CLARO}; }}

/* Casillas dentro de tablas (Obtenido/Deseado): mismo estampado que
   QCheckBox — campo azul, hilo dorado, oro al marcar. */
QTableWidget::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {ORO_VIEJO}; border-radius: 3px;
    background: {AZUL_CAMPO};
}}
QTableWidget::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {ORO_CLARO}, stop:1 {ORO});
    border: 1px solid {ORO_CLARO};
}}
QTableWidget::indicator:hover {{ border: 1px solid {ORO_CLARO}; }}

QTableWidget {{
    background: {AZUL_CAMPO};
    alternate-background-color: rgba(22, 56, 107, 0.45);
    color: {ORO_CLARO};
    border: 1px solid {ORO_VIEJO};
    border-radius: 8px;
    padding: 2px;
    outline: none;  /* sin recuadro de foco punteado */
    selection-background-color: transparent;  /* solo pinta ::item:selected */
    selection-color: {ORO_CLARO};
}}
QTableWidget::item {{
    padding: 8px 12px;
    border: none;
    border-bottom: 1px solid rgba(176, 141, 46, 0.14);
}}
/* Selección: el fondo metalizado lo pinta _GlowRowDelegate (veladura
   dorada + destello especular en la punta del ratón). Aquí solo el
   color del texto y el subrayado. */
QTableWidget::item:selected {{
    background: transparent;
    color: {ORO_CLARO};
    border-bottom: 1px solid rgba(212, 175, 55, 0.5);
}}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {AZUL_OXFORD_CLARO}, stop:1 {AZUL_OXFORD});
    color: {ORO};
    border: none;
    border-bottom: 2px solid {ORO_VIEJO};
    border-right: 1px solid rgba(176, 141, 46, 0.22);
    padding: 8px 10px;
    letter-spacing: 1.5px;
    font-weight: bold;
    text-transform: uppercase;
    font-size: 9pt;
}}
QHeaderView::section:hover {{ color: {ORO_CLARO}; }}
QTableCornerButton::section {{ background: {AZUL_OXFORD_CLARO}; border: none; }}

QComboBox {{
    background: {AZUL_CAMPO};
    color: {ORO_CLARO};
    border: 1px solid {ORO_APAGADO};
    border-radius: 4px;
    padding: 4px 10px;
    min-width: 150px;
}}
QComboBox:hover {{ border: 1px solid {ORO}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {ORO};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {AZUL_OXFORD};
    color: {ORO_TEXTO};
    border: 1px solid {ORO_VIEJO};
    selection-background-color: {ORO};
    selection-color: {AZUL_OXFORD};
    outline: none;
}}

QMenu {{
    background: {AZUL_OXFORD};
    color: {ORO_TEXTO};
    border: 1px solid {ORO_VIEJO};
}}
QMenu::item:selected {{ background: {ORO}; color: {AZUL_OXFORD}; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; border: none; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {ORO_APAGADO}; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {ORO_VIEJO}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; border: none; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {ORO_APAGADO}; border-radius: 4px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ORO_VIEJO}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{
    background: {AZUL_OXFORD_OSCURO};
    color: {ORO_TEXTO};
    border: 1px solid {ORO_VIEJO};
}}
"""


def _make_icon() -> QIcon:
    """
    Icono de la aplicación: usa icon.png si existe junto al ejecutable;
    si no, dibuja un pequeño lomo de tomo Gredos (azul Oxford con
    filetes dorados y una 'G' en oro).
    """
    icon_file = resource_path("assets/icon.png")
    if icon_file.exists():
        return QIcon(str(icon_file))

    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    # Lomo azul Oxford (símil piel)
    painter.setBrush(QColor(AZUL_OXFORD))
    painter.setPen(QPen(QColor(AZUL_OXFORD_OSCURO), 2))
    painter.drawRoundedRect(10, 2, 44, 60, 5, 5)

    # Filetes dorados dobles, arriba y abajo (como en la encuadernación)
    painter.setPen(QPen(QColor(ORO), 2))
    for y in (10, 15, 49, 54):
        painter.drawLine(14, y, 50, y)

    # 'G' dorada de Gredos
    painter.setFont(fuente(18, negrita=True))
    painter.setPen(QPen(QColor(ORO_CLARO)))
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "G")
    painter.end()
    return QIcon(pm)


# ----------------------------------------------------------------------
# Seguimiento del cursor, compartido por toda la aplicación
# ----------------------------------------------------------------------
class _RastreadorDeCursor(QObject):
    """
    Avisa de dónde está el ratón, para lo que se ilumina al acercarlo.

    Se sondea la posición GLOBAL en vez de escuchar `mouseMoveEvent`
    porque los hijos se comen los eventos del padre: el marco de la
    ventana nunca vería pasar el cursor por encima de un botón.

    Hay UNO solo para toda la aplicación: un temporizador por cada campo
    de texto sería tirar CPU. Y solo corre mientras haya alguien
    escuchando, así que con las ventanas cerradas no queda nada vivo.
    """

    movio = Signal(QPoint)

    def __init__(self) -> None:
        super().__init__()
        self._oyentes = 0
        self._ultimo: Optional[QPoint] = None
        self._timer = QTimer(self)
        self._timer.setInterval(40)          # ~25 fps: suave y barato
        self._timer.timeout.connect(self._mirar)

    def suscribir(self, receptor) -> None:
        self.movio.connect(receptor)
        self._oyentes += 1
        if not self._timer.isActive():
            self._timer.start()

    def desuscribir(self, receptor) -> None:
        try:
            self.movio.disconnect(receptor)
        except (RuntimeError, TypeError):
            return
        self._oyentes = max(0, self._oyentes - 1)
        if not self._oyentes:
            self._timer.stop()

    def _mirar(self) -> None:
        from PySide6.QtGui import QCursor

        pos = QCursor.pos()
        if pos != self._ultimo:
            self._ultimo = pos
            self.movio.emit(pos)


_RASTREADOR: Optional[_RastreadorDeCursor] = None


def rastreador_de_cursor() -> _RastreadorDeCursor:
    """El rastreador compartido (se crea al primer uso)."""
    global _RASTREADOR
    if _RASTREADOR is None:
        _RASTREADOR = _RastreadorDeCursor()
    return _RASTREADOR


class _BordeQueSigueAlRaton:
    """
    Mezcla para widgets cuyo BORDE se aviva al acercarse el ratón.

    Guarda la posición del cursor en coordenadas locales (`_cerca`) o
    None si está lejos, y solo repinta cuando el dato cambia de verdad
    — si no, serían 25 repintados por segundo por cada campo.
    """

    _ALCANCE = 150.0          # a qué distancia empieza a notarse

    def _iniciar_seguimiento(self) -> None:
        self._cerca: Optional[QPointF] = None

    def _apuntar_cursor(self, pos: QPoint) -> None:
        local = self.mapFromGlobal(pos)
        zona = self.rect().adjusted(
            -int(self._ALCANCE), -int(self._ALCANCE),
            int(self._ALCANCE), int(self._ALCANCE),
        )
        nuevo = QPointF(local) if zona.contains(local) else None
        if (nuevo is None) != (self._cerca is None) or (
            nuevo is not None and nuevo != self._cerca
        ):
            self._cerca = nuevo
            self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        rastreador_de_cursor().suscribir(self._apuntar_cursor)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        rastreador_de_cursor().desuscribir(self._apuntar_cursor)
        self._cerca = None
        super().hideEvent(event)

    def _pincel_de_borde(self, base: str, alcance: float) -> QBrush:
        """Filete dorado que se enciende hacia el punto del cursor."""
        luz = QRadialGradient(self._cerca, alcance)
        vivo = QColor(ORO_CLARO)
        vivo.setAlpha(210)
        medio = QColor(ORO)
        medio.setAlpha(90)
        luz.setColorAt(0.0, vivo)
        luz.setColorAt(0.6, medio)
        luz.setColorAt(1.0, QColor(base))
        return QBrush(luz)


def _filete(alto: int = 1) -> QFrame:
    """Línea horizontal dorada (filete de encuadernación)."""
    line = QFrame()
    line.setProperty("class", "filete")
    line.setFixedHeight(alto)
    line.setStyleSheet(f"background: {ORO_VIEJO}; border: none;")
    return line


def _gold_glow(widget: QWidget, blur: int = 22, alpha: int = 170) -> QGraphicsDropShadowEffect:
    """Aplica un halo dorado fijo a un widget (título, adornos)."""
    glow = QGraphicsDropShadowEffect(widget)
    color = QColor(ORO)
    color.setAlpha(alpha)
    glow.setColor(color)
    glow.setOffset(0, 0)
    glow.setBlurRadius(blur)
    widget.setGraphicsEffect(glow)
    return glow


# ----------------------------------------------------------------------
# Botón premium: letras estampadas en oro que siguen al ratón
# ----------------------------------------------------------------------
class GlowButton(QPushButton):
    """
    Botón pintado a mano al estilo del estampado dorado de la cubierta.

    - En reposo: letras en oro apagado, borde de hilo dorado, fondo del
      cuero visible (ligera pátina translúcida).
    - Con el ratón encima: NO se rellena de oro; las LETRAS se
      encienden con un brillo dorado radial centrado en el puntero, y
      el borde se aviva alrededor del punto donde apunta el ratón,
      como pan de oro reflejando una luz que se mueve.
    - El ancho mínimo se calcula del texto: ninguna etiqueta se corta.
    """

    _PAD_X = 30  # aire horizontal (letra + margen de estampado)
    _PAD_Y = 16

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        # Nunca botón "por defecto" de un diálogo: Enter en un campo de
        # búsqueda disparaba "Cerrar" y la ventana se cerraba (2026-07-26).
        self.setAutoDefault(False)
        self.setDefault(False)
        self._mouse: Optional[QPointF] = None
        self.setFont(fuente(10, espaciado=1.0))

        # "Calor" del dorado (0 = reposo, 1 = encendido): el brillo se
        # enciende y se apaga con un fundido suave, no de golpe.
        self._heat = 0.0
        self._heat_anim = QVariantAnimation(self)
        self._heat_anim.setDuration(180)
        self._heat_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._heat_anim.valueChanged.connect(self._set_heat)

    def _set_heat(self, value) -> None:
        self._heat = float(value)
        self.update()

    def _animate_heat(self, target: float) -> None:
        self._heat_anim.stop()
        self._heat_anim.setStartValue(self._heat)
        self._heat_anim.setEndValue(target)
        self._heat_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.isEnabled():
            self._animate_heat(1.0)
        super().enterEvent(event)

    # --- geometría: que el texto SIEMPRE quepa -------------------------
    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        fm = self.fontMetrics()
        return QSize(
            fm.horizontalAdvance(self.text()) + self._PAD_X,
            fm.height() + self._PAD_Y,
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return self.sizeHint()

    # --- seguimiento del ratón ----------------------------------------
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._mouse = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._animate_heat(0.0)  # el punto de luz se apaga en fundido
        self.update()
        super().leaveEvent(event)

    # --- pintado -------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.rect().adjusted(1, 1, -2, -2)
        radius = 7.0
        enabled = self.isEnabled()
        heat = self._heat if enabled else 0.0
        focus = self._mouse if self._mouse is not None else QPointF(
            rect.center()
        )
        pressed = self.isDown()

        # Fondo: pátina sutil sobre el cuero (nunca oro macizo)
        bg = QColor(255, 255, 255, int(7 + 6 * heat))
        if pressed:
            bg = QColor(0, 0, 0, 46)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, radius, radius)

        # Borde base: hilo dorado (apagado si está deshabilitado)
        base_border = QColor(ORO_VIEJO if enabled else "#4a4630")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(base_border, 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        # Borde vivo alrededor del puntero (intensidad = calor)
        if heat > 0.01:
            grad = QRadialGradient(focus, max(rect.width() * 0.55, 60.0))
            bright = QColor(ORO_CLARO)
            bright.setAlpha(int(235 * heat))
            mid = QColor(ORO)
            mid.setAlpha(int(95 * heat))
            grad.setColorAt(0.0, bright)
            grad.setColorAt(0.55, mid)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(QPen(QBrush(grad), 1.4))
            painter.drawRoundedRect(rect, radius, radius)

        # Letras estampadas: sombra de grabado (siempre) + oro de base
        text_rect = rect.adjusted(0, 1 if pressed else 0, 0, 1 if pressed else 0)
        painter.setFont(self.font())
        engraving = QColor(0, 0, 0, 110)
        painter.setPen(engraving)
        painter.drawText(
            text_rect.adjusted(0, 1, 0, 1),
            Qt.AlignmentFlag.AlignCenter, self.text(),
        )
        base_text = QColor(ORO_TEXTO if enabled else "#6f6a54")
        painter.setPen(base_text)
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignCenter, self.text()
        )

        # Encendido de las letras: halo difuso + núcleo, según el calor
        if heat > 0.01:
            # halo: la misma palabra repetida en corona de 1 px
            halo_color = QColor(ORO)
            halo_color.setAlpha(int(34 * heat))
            painter.setPen(halo_color)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(
                    text_rect.adjusted(dx, dy, dx, dy),
                    Qt.AlignmentFlag.AlignCenter, self.text(),
                )
            # núcleo: gradiente radial centrado en el puntero
            glow = QRadialGradient(focus, max(rect.width() * 0.45, 55.0))
            core = QColor(ORO_CLARO)
            core.setAlpha(int(255 * heat))
            mid = QColor(ORO)
            mid.setAlpha(int(150 * heat))
            glow.setColorAt(0.0, core)
            glow.setColorAt(0.5, mid)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(QPen(QBrush(glow), 0))
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignCenter, self.text()
            )
        painter.end()


# ----------------------------------------------------------------------
# Barra de progreso: pan de oro corriendo por el campo del cuero
# ----------------------------------------------------------------------
class GlowLineEdit(_BordeQueSigueAlRaton, QLineEdit):
    """
    Campo de texto cuyo filete se aviva al acercar el ratón.

    Es el mismo gesto que el borde de la ventana: la luz sigue al
    cursor, así que el campo al que vas a escribir se insinúa antes de
    pincharlo. El fondo y el texto los sigue poniendo `GREDOS_QSS`;
    aquí solo se repinta el BORDE encima, con el mismo radio de 4 px
    que fija la hoja de estilos (si se cambia allí, cambiarlo aquí).

    Usar SIEMPRE este en vez de `QLineEdit` a secas.
    """

    _ALCANCE = 110.0          # más corto que en la ventana: es pequeño
    _RADIO = 4.0              # el mismo border-radius del QSS

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._iniciar_seguimiento()

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().paintEvent(event)
        if self._cerca is None or self.hasFocus():
            # Con el foco puesto, el QSS ya pinta el borde en oro vivo:
            # superponer la luz solo lo emborronaría.
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        borde = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(self._pincel_de_borde(ORO_APAGADO, self._ALCANCE), 1.0)
        )
        painter.drawRoundedRect(borde, self._RADIO, self._RADIO)
        painter.end()


def pintar_estrella(painter: QPainter, centro: QPointF, radio: float,
                    fuerza: float) -> None:
    """
    Un destello de cuatro puntas con su núcleo encendido.

    Lo comparten el resaltado de los pasajes y el botón del pasaje del
    día: es el mismo brillo, y duplicarlo sería que se separasen al
    tocar uno de los dos.
    """
    nucleo = QRadialGradient(centro, radio)
    vivo = QColor("#fffdf0")
    vivo.setAlpha(int(200 * fuerza))
    medio = QColor(ORO_CLARO)
    medio.setAlpha(int(80 * fuerza))
    nucleo.setColorAt(0.0, vivo)
    nucleo.setColorAt(0.45, medio)
    nucleo.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(nucleo)
    painter.drawEllipse(centro, radio, radio)
    # Las cuatro puntas, finas y desiguales: la vertical más corta,
    # como en un destello de verdad.
    punta = QColor("#fffdf0")
    punta.setAlpha(int(150 * fuerza))
    painter.setPen(QPen(punta, 0.7))
    largo = radio * 2.4
    painter.drawLine(
        QPointF(centro.x() - largo, centro.y()),
        QPointF(centro.x() + largo, centro.y()),
    )
    painter.drawLine(
        QPointF(centro.x(), centro.y() - largo * 0.6),
        QPointF(centro.x(), centro.y() + largo * 0.6),
    )


class BotonDelDia(GlowButton):
    """
    El botón ancho del «Pasaje del día», con su propia luz.

    Junta los dos brillos que ya usa la aplicación, que es lo que pidió
    el usuario: la VELADURA metálica de la fila seleccionada de las
    tablas (oro claro arriba, sombra abajo, más el destello especular
    que sigue al puntero) y las ESTRELLAS del resaltado de los pasajes.

    Las estrellas se sortean al nacer cada una y no en cada fotograma:
    si no, en vez de brillar temblarían.
    """

    _MS = 40
    _VIDA_ESTRELLA = 2100
    _CUANTAS = 5
    _RADIO = 2.0
    _ALTO = 32

    def __init__(self, texto: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(texto, parent)
        self.setMinimumHeight(self._ALTO)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setFont(fuente(10.5, negrita=True, espaciado=1.3))
        self._estrellas = [
            {"fase": random.random(), **self._sitio()}
            for _ in range(self._CUANTAS)
        ]
        self._timer = QTimer(self)
        self._timer.setInterval(self._MS)
        self._timer.timeout.connect(self._latir)

    @staticmethod
    def _sitio() -> dict:
        """Dónde nace una estrella, en coordenadas del botón (0 a 1)."""
        return {"x": random.uniform(0.02, 0.98),
                "y": random.uniform(0.12, 0.88)}

    def _latir(self) -> None:
        paso = self._MS / self._VIDA_ESTRELLA
        for estrella in self._estrellas:
            antes = estrella["fase"]
            estrella["fase"] = (antes + paso) % 1.0
            if estrella["fase"] < antes:        # se apagó y vuelve a nacer
                estrella.update(self._sitio())
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radio = 7.0

        painter.save()
        camino = QPainterPath()
        camino.addRoundedRect(rect, radio, radio)
        painter.setClipPath(camino)

        # 1) Veladura metálica, la misma de la fila seleccionada. Va
        #    atada al CALOR del botón (0 en reposo, 1 con el ratón
        #    encima): pintándola siempre al máximo, el botón parecía
        #    encendido aunque el ratón estuviera lejos (2026-08-08).
        calor = self._heat
        veladura = QLinearGradient(
            QPointF(rect.left(), rect.top()),
            QPointF(rect.left(), rect.bottom()),
        )
        alto = QColor(ORO_CLARO)
        alto.setAlpha(int(16 + 42 * calor))
        medio = QColor(ORO)
        medio.setAlpha(int(10 + 26 * calor))
        bajo = QColor(ORO_VIEJO)
        bajo.setAlpha(int(14 + 34 * calor))
        veladura.setColorAt(0.0, alto)
        veladura.setColorAt(0.45, medio)
        veladura.setColorAt(1.0, bajo)
        painter.fillRect(rect, veladura)

        # 2) Destello especular donde está el puntero. También por
        #    CALOR: `GlowButton.leaveEvent` no borra la posición del
        #    ratón, así que sin esto el destello se quedaba clavado
        #    donde se sacó el cursor.
        if self._mouse is not None and calor > 0.02:
            especular = QRadialGradient(
                QPointF(self._mouse.x(), rect.center().y()), 170.0
            )
            nucleo = QColor("#fff6d0")
            nucleo.setAlpha(int(120 * calor))
            caliente = QColor(ORO_CLARO)
            caliente.setAlpha(int(84 * calor))
            calido = QColor(ORO)
            calido.setAlpha(int(36 * calor))
            especular.setColorAt(0.0, nucleo)
            especular.setColorAt(0.18, caliente)
            especular.setColorAt(0.55, calido)
            especular.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(rect, especular)

        # 3) Las estrellas, en suma de luz para que no tapen las letras
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Plus
        )
        for estrella in self._estrellas:
            fuerza = math.sin(math.pi * estrella["fase"]) ** 2
            if fuerza < 0.05:
                continue
            pintar_estrella(
                painter,
                QPointF(rect.left() + rect.width() * estrella["x"],
                        rect.top() + rect.height() * estrella["y"]),
                self._RADIO * (0.45 + 0.55 * fuerza),
                fuerza,
            )
        painter.restore()

        # 4) Filete de oro, más marcado que en un botón normal
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ORO), 1.2))
        painter.drawRoundedRect(rect, radio, radio)
        painter.end()

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        # La clase base solo apaga el calor; la posición del ratón se
        # queda, y con ella el destello si no se borra aquí.
        self._mouse = None
        super().leaveEvent(event)


class GlowProgress(QWidget):
    """
    Barra de avance a juego con la estampación dorada de la cubierta.

    - Campo hundido (el mismo azul de tablas y cajas) con filete de hilo
      dorado y pespunte interior.
    - Relleno de pan de oro con gradiente vertical (metal en sombra),
      nunca oro macizo plano.
    - Un destello especular recorre el oro: aunque el número tarde en
      moverse, se ve que el trabajo sigue vivo.
    - Sin total conocido (`setRange(0)`) el destello se convierte en una
      banda que va y viene: hay trabajo, pero todavía no se puede decir
      cuánto falta.
    """

    _ALTO = 24
    _RADIO = 7.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self._ALTO)
        self._total = 0
        self._valor = 0
        self._texto = ""
        self.setFont(fuente(9, espaciado=0.8))

        # Fase del destello (0→1 en bucle). Solo corre mientras la barra
        # se ve: animar una ventana oculta es CPU tirada.
        self._fase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(1600)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._set_fase)

    def _set_fase(self, value) -> None:
        self._fase = float(value)
        self.update()

    # --- API (a la manera de QProgressBar, en corto) --------------------
    def setRange(self, total: int) -> None:  # noqa: N802 - API a la Qt
        """`total` 0 = indeterminado (no se sabe cuántos pasos hay)."""
        self._total = max(0, int(total))
        self.update()

    def setValue(self, valor: int) -> None:  # noqa: N802 - API a la Qt
        self._valor = max(0, int(valor))
        self.update()

    def setText(self, texto: str) -> None:  # noqa: N802 - API a la Qt
        self._texto = texto
        self.update()

    def value(self) -> int:
        return self._valor

    def maximum(self) -> int:
        return self._total

    def text(self) -> str:
        return self._texto

    def arrancar(self, texto: str = "", total: int = 0) -> None:
        """Muestra la barra desde cero y enciende el destello."""
        self._texto = texto
        self._total = max(0, int(total))
        self._valor = 0
        self.show()
        self.update()

    def parar(self) -> None:
        self.hide()

    def avanzar(self, texto: str, hechas: int, total: int) -> None:
        """Un solo paso: fase, hechas y total, como llega del análisis."""
        self._texto = texto
        self._total = max(0, int(total))
        self._valor = max(0, int(hechas))
        self.update()

    # --- ciclo de vida --------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        if self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._anim.stop()
        super().hideEvent(event)

    # --- pintado --------------------------------------------------------
    @property
    def _fraccion(self) -> float:
        if self._total <= 0:
            return 0.0
        return min(1.0, self._valor / self._total)

    def _etiqueta(self) -> str:
        if self._total > 0:
            pct = f"{self._fraccion * 100:.0f} %"
            return f"{self._texto}  ·  {pct}" if self._texto else pct
        return self._texto

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect().adjusted(1, 1, -2, -2))
        marco = QPainterPath()
        marco.addRoundedRect(rect, self._RADIO, self._RADIO)

        # Campo hundido, como el de las cajas de texto
        painter.fillPath(marco, QColor(AZUL_CAMPO))
        painter.save()
        painter.setClipPath(marco)

        if self._total > 0:
            ancho = rect.width() * self._fraccion
            if ancho > 0.5:
                oro = QRectF(rect.left(), rect.top(), ancho, rect.height())
                painter.fillRect(oro, QBrush(self._pan_de_oro(oro)))
                self._destello(painter, oro, oro.left(), oro.width())
        else:
            # Indeterminado: banda de oro en vaivén
            banda = max(60.0, rect.width() * 0.3)
            ida = self._fase * 2.0
            t = ida if ida <= 1.0 else 2.0 - ida
            x = rect.left() + (rect.width() - banda) * t
            franja = QRectF(x, rect.top(), banda, rect.height())
            velo = QLinearGradient(franja.topLeft(), franja.topRight())
            centro = QColor(ORO)
            centro.setAlpha(120)
            borde = QColor(ORO_VIEJO)
            borde.setAlpha(0)
            velo.setColorAt(0.0, borde)
            velo.setColorAt(0.5, centro)
            velo.setColorAt(1.0, borde)
            painter.fillRect(franja, QBrush(velo))
        painter.restore()

        # Filete exterior y pespunte interior (mismo remate que los paneles)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ORO_VIEJO), 1.0))
        painter.drawPath(marco)
        pespunte = QPen(QColor(ORO_APAGADO), 1.0, Qt.PenStyle.DotLine)
        painter.setPen(pespunte)
        painter.drawRoundedRect(
            rect.adjusted(2.5, 2.5, -2.5, -2.5),
            self._RADIO - 2, self._RADIO - 2,
        )

        # Letras estampadas: grabado + oro, como en los botones. Sobre la
        # parte ya cubierta de oro se cambia a tinta oscura (letra
        # hundida en el pan de oro): en dorado sobre dorado no se leería.
        etiqueta = self._etiqueta()
        if etiqueta:
            painter.setFont(self.font())
            caja = self.rect()
            painter.setPen(QColor(0, 0, 0, 140))
            painter.drawText(
                caja.adjusted(0, 1, 0, 1),
                Qt.AlignmentFlag.AlignCenter, etiqueta,
            )
            painter.setPen(QColor(ORO_TEXTO))
            painter.drawText(caja, Qt.AlignmentFlag.AlignCenter, etiqueta)
            if self._total > 0 and self._fraccion > 0.01:
                painter.save()
                painter.setClipRect(QRectF(
                    rect.left(), rect.top(),
                    rect.width() * self._fraccion, rect.height(),
                ))
                painter.setPen(QColor(255, 255, 255, 60))
                painter.drawText(
                    caja.adjusted(0, 1, 0, 1),
                    Qt.AlignmentFlag.AlignCenter, etiqueta,
                )
                painter.setPen(QColor(AZUL_OXFORD_OSCURO))
                painter.drawText(caja, Qt.AlignmentFlag.AlignCenter, etiqueta)
                painter.restore()
        painter.end()

    def _pan_de_oro(self, oro: QRectF) -> QLinearGradient:
        """Veladura dorada con la luz arriba y el metal en sombra abajo."""
        grad = QLinearGradient(oro.topLeft(), oro.bottomLeft())
        alto = QColor(ORO_CLARO)
        alto.setAlpha(150)
        medio = QColor(ORO)
        medio.setAlpha(190)
        bajo = QColor(ORO_VIEJO)
        bajo.setAlpha(165)
        grad.setColorAt(0.0, alto)
        grad.setColorAt(0.42, medio)
        grad.setColorAt(1.0, bajo)
        return grad

    def _destello(
        self, painter: QPainter, oro: QRectF, x0: float, ancho: float
    ) -> None:
        """Reflejo especular que recorre la parte ya cubierta."""
        if ancho < 8:
            return
        brillo_ancho = min(90.0, max(40.0, ancho * 0.35))
        centro = x0 - brillo_ancho + (ancho + brillo_ancho * 2) * self._fase
        franja = QRectF(
            centro - brillo_ancho / 2, oro.top(), brillo_ancho, oro.height()
        )
        grad = QLinearGradient(franja.topLeft(), franja.topRight())
        nucleo = QColor("#fff6d0")
        nucleo.setAlpha(120)
        apagado = QColor(ORO_CLARO)
        apagado.setAlpha(0)
        grad.setColorAt(0.0, apagado)
        grad.setColorAt(0.5, nucleo)
        grad.setColorAt(1.0, apagado)
        painter.fillRect(franja.intersected(oro), QBrush(grad))


# ----------------------------------------------------------------------
# Cabecera de tabla con el mismo estampado dorado que los botones
# ----------------------------------------------------------------------
class GlowHeader(QHeaderView):
    """
    QHeaderView horizontal pintado a mano, a juego con GlowButton:
    letras doradas que se ENCIENDEN con un brillo radial centrado en el
    puntero (nunca fondo de oro macizo) e indicador de orden ▴/▾.
    """

    def __init__(
        self, parent: Optional[QWidget] = None, compact: bool = False
    ) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setMouseTracking(True)
        self._mouse: Optional[QPointF] = None
        self._compact = compact
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(True)
        # compact: para tablas auxiliares dentro de fichas densas
        self.setFixedHeight(28 if compact else 42)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._mouse = event.position()
        self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._mouse = None
        self.viewport().update()
        super().leaveEvent(event)

    @classmethod
    def header_font(cls, compact: bool = False) -> QFont:
        """Fuente de las cabeceras (única fuente de verdad)."""
        font = fuente(
            8 if compact else 9, negrita=True,
            espaciado=1.0 if compact else 1.5,
        )
        return font

    @classmethod
    def required_width(cls, text: str) -> int:
        """
        Ancho de columna necesario para que el rótulo NO se corte:
        texto en mayúsculas + márgenes + hueco del indicador ▴/▾.
        """
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(cls.header_font())
        return fm.horizontalAdvance(text.upper()) + 4 + 14 + 8

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Fondo: piel algo más clara + filete doble inferior
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, QColor(AZUL_OXFORD_CLARO))
        grad.setColorAt(1.0, QColor(AZUL_OXFORD))
        painter.fillRect(rect, grad)
        painter.setPen(QPen(QColor(176, 141, 46, 55), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        # Filete DOBLE inferior: grueso + hairline (encuadernación)
        painter.setPen(QPen(QColor(ORO_VIEJO), 2))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setPen(QPen(QColor(176, 141, 46, 90), 1))
        painter.drawLine(
            QPointF(rect.left(), rect.bottom() - 3.0),
            QPointF(rect.right(), rect.bottom() - 3.0),
        )

        model = self.model()
        text = ""
        if model is not None:
            text = str(
                model.headerData(
                    logicalIndex, Qt.Orientation.Horizontal,
                    Qt.ItemDataRole.DisplayRole,
                ) or ""
            ).upper()

        painter.setFont(self.header_font(self._compact))

        text_rect = rect.adjusted(4, 0, -14, 0)
        painter.setPen(QColor(ORO))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

        # Letras encendidas alrededor del puntero (igual que GlowButton)
        if self._mouse is not None:
            glow = QRadialGradient(self._mouse, 70.0)
            core = QColor(ORO_CLARO)
            halo = QColor(ORO)
            halo.setAlpha(140)
            glow.setColorAt(0.0, core)
            glow.setColorAt(0.5, halo)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(QPen(QBrush(glow), 0))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

        # Indicador de orden
        if self.sortIndicatorSection() == logicalIndex:
            asc = self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
            painter.setPen(QColor(ORO_CLARO))
            painter.drawText(
                rect.adjusted(0, 0, -5, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "▴" if asc else "▾",
            )
        painter.restore()


# ----------------------------------------------------------------------
# Tabla con brillo dorado que sigue a la punta del ratón
# ----------------------------------------------------------------------
class _GlowRowDelegate(QStyledItemDelegate):
    """
    Delegado de las filas: luz dorada que sigue la PUNTA del ratón.

    - Fila normal bajo el cursor: resplandor tenue.
    - Fila SELECCIONADA: veladura de oro claro (metal en sombra) y, con
      el cursor encima, DESTELLO ESPECULAR de oro metalizado real —
      núcleo casi blanco (#fff6d0) que decae a oro claro → oro → nada,
      como pan de oro reflejando una luz puntual.
    - Elimina el recuadro azul de foco de Windows (State_HasFocus).
    """

    def __init__(self, table: "GlowTable") -> None:
        super().__init__(table)
        self._table = table

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus  # sin recuadro de foco
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        super().paint(painter, opt, index)  # fondo QSS + texto

        rect = option.rect
        painter.save()

        # Veladura metálica de la fila seleccionada: oro claro con
        # gradiente vertical (arriba luz, abajo sombra = plancha de metal)
        if selected:
            veil = QLinearGradient(
                QPointF(rect.left(), rect.top()),
                QPointF(rect.left(), rect.bottom()),
            )
            top = QColor(ORO_CLARO)
            top.setAlpha(64)
            mid = QColor(ORO)
            mid.setAlpha(40)
            low = QColor(ORO_VIEJO)
            low.setAlpha(52)
            veil.setColorAt(0.0, top)
            veil.setColorAt(0.45, mid)
            veil.setColorAt(1.0, low)
            painter.fillRect(rect, veil)

        pos = self._table.hover_pos
        if pos is not None and rect.top() <= pos.y() <= rect.bottom():
            center = QPointF(pos.x(), rect.center().y())
            if selected:
                # DESTELLO ESPECULAR: oro metalizado real — núcleo casi
                # blanco, transición corta y cola larga cálida
                spec = QRadialGradient(center, 150.0)
                core = QColor("#fff6d0")
                core.setAlpha(160)
                hot = QColor(ORO_CLARO)
                hot.setAlpha(110)
                warm = QColor(ORO)
                warm.setAlpha(48)
                spec.setColorAt(0.0, core)
                spec.setColorAt(0.18, hot)
                spec.setColorAt(0.55, warm)
                spec.setColorAt(1.0, QColor(0, 0, 0, 0))
                painter.fillRect(rect, spec)
            else:
                glow = QRadialGradient(center, 110.0)
                core = QColor(ORO_CLARO)
                core.setAlpha(34)
                mid = QColor(ORO)
                mid.setAlpha(14)
                glow.setColorAt(0.0, core)
                glow.setColorAt(0.5, mid)
                glow.setColorAt(1.0, QColor(0, 0, 0, 0))
                painter.fillRect(rect, glow)
        painter.restore()


class GlowTable(QTableWidget):
    """QTableWidget con seguimiento del puntero para _GlowRowDelegate."""

    def __init__(self, rows: int, columns: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(rows, columns, parent)
        self.hover_pos: Optional[QPointF] = None
        self.viewport().setMouseTracking(True)
        self.setItemDelegate(_GlowRowDelegate(self))

    def viewportEvent(self, event) -> bool:  # noqa: N802 - API Qt
        etype = event.type()
        if etype == QEvent.Type.MouseMove:
            self.hover_pos = event.position()
            self.viewport().update()
        elif etype in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            self.hover_pos = None
            self.viewport().update()
        return super().viewportEvent(event)


# ----------------------------------------------------------------------
# Marco de cuero: grano texturizado + brillo dorado que sigue al ratón
# ----------------------------------------------------------------------
class LeatherFrame(_BordeQueSigueAlRaton, QFrame):
    """
    Marco-cubierta pintado a mano imitando la piel azul de los tomos.

    - Fondo: degradado azul Oxford + GRANO de cuero procedural (miles de
      motas claras/oscuras y vetas sutiles, generadas una sola vez y
      teseladas), con viñeteado hacia los bordes.
    - Borde: filete dorado que se AVIVA hacia donde está el ratón, y
      marco interior estampado con ornamentos de esquina.

    Lo que NO lleva es el reflejo sobre la piel: eran tres capas
    concéntricas que paseaban una mancha de luz por toda la cubierta y
    el usuario pidió quitar «el efecto linterna» (2026-08-08). La
    iluminación del BORDE sí se conserva. No reponer el reflejo del
    fondo sin que lo pida.
    """

    _texture_cache: Optional[QPixmap] = None

    def __init__(
        self, parent: Optional[QWidget] = None, dark: bool = False
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chrome")
        # Ventanas secundarias: mismo cuero, tonos algo más oscuros
        # (ver CLAUDE.md — sistema de diseño).
        self._dark = dark
        # Borde superior de la orla interior: _wrap_chrome lo baja hasta
        # debajo de la TitleBar para que los ornamentos de esquina no se
        # solapen con el icono ni con los botones de la barra.
        self.orla_top = 9
        self._iniciar_seguimiento()

    def _shade(self, hex_color: str) -> QColor:
        """Color de la piel; algo más oscuro en ventanas secundarias."""
        color = QColor(hex_color)
        # 120 (no 132): con la base casi negra, oscurecer más la
        # volvería negro puro y se perdería el azul del tomo.
        return color.darker(120) if self._dark else color

    # --- textura procedural (una sola vez por proceso) -----------------
    @classmethod
    def _texture(cls) -> QPixmap:
        """
        Tesela de similpiel de 512×512, TESELABLE SIN COSTURAS.

        El cuero real no es ruido: es una RED CELULAR de arrugas que
        delimitan pequeñas células abombadas, con poros y un tinte
        ligeramente desigual. Capas (de fondo a frente):

        1. Moteado de tinte: manchas grandes y muy suaves (radiales).
        2. Células: rejilla de puntos con desplazamiento PERIÓDICO
           (mismo desplazamiento en los bordes → la tesela encaja);
           cada célula recibe un leve realce arriba-izquierda (relieve).
        3. Red de arrugas: líneas quebradas entre células vecinas,
           oscuras con un filo claro (surco iluminado).
        4. Poros y grano fino, concentrados junto a las arrugas.
        """
        if cls._texture_cache is not None:
            return cls._texture_cache
        size = 1024                     # tesela grande: el patrón apenas se repite
        cells = 22                      # 22×22 células ≈ 46 px por célula
        cell = size / cells
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rng = random.Random(1970)  # semilla fija: mismo cuero en cada arranque

        # 1) Moteado de tinte (manchas grandes, casi invisibles) + una
        #    veladura oscura general: cuero más profundo
        for _ in range(90):
            cx, cy = rng.uniform(0, size), rng.uniform(0, size)
            radius = rng.uniform(80, 220)
            dark = rng.random() < 0.62
            alpha = rng.randint(5, 10)
            blob = QRadialGradient(QPointF(cx, cy), radius)
            base = QColor(0, 0, 0, alpha) if dark else QColor(110, 150, 210, alpha)
            blob.setColorAt(0.0, base)
            blob.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(blob)
            painter.drawEllipse(QPointF(cx, cy), radius, radius)

        # 2) Rejilla de células con desplazamiento periódico
        def jitter(ix: int, iy: int) -> tuple[float, float]:
            local = random.Random((ix % cells) * 73 + (iy % cells) * 179)
            return (
                local.uniform(-cell * 0.32, cell * 0.32),
                local.uniform(-cell * 0.32, cell * 0.32),
            )

        def point(ix: int, iy: int) -> QPointF:
            jx, jy = jitter(ix, iy)
            return QPointF(ix * cell + jx, iy * cell + jy)

        # Realce de cada célula (piel abombada que refleja la luz)
        painter.setPen(Qt.PenStyle.NoPen)
        for iy in range(cells + 1):
            for ix in range(cells + 1):
                center = point(ix, iy)
                sheen = QRadialGradient(
                    QPointF(center.x() - cell * 0.12, center.y() - cell * 0.12),
                    cell * 0.55,
                )
                sheen.setColorAt(0.0, QColor(180, 200, 235, 6))
                sheen.setColorAt(0.55, QColor(255, 255, 255, 2))
                sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
                painter.setBrush(sheen)
                painter.drawEllipse(center, cell * 0.55, cell * 0.55)

        # 3) Red de arrugas entre células vecinas: CURVAS suaves
        #    (quadTo) — surco oscuro con filo claro, como cuero real
        def crease(a: QPointF, b: QPointF, seed: int) -> None:
            local = random.Random(seed)
            mid = QPointF(
                (a.x() + b.x()) / 2 + local.uniform(-6.5, 6.5),
                (a.y() + b.y()) / 2 + local.uniform(-6.5, 6.5),
            )
            dark = QPen(QColor(0, 0, 0, local.randint(30, 48)),
                        local.uniform(1.0, 1.8))
            dark.setCapStyle(Qt.PenCapStyle.RoundCap)
            lite = QPen(QColor(190, 210, 240, local.randint(5, 8)), 0.5)
            for pen, off in ((dark, 0.0), (lite, 1.2)):
                path = QPainterPath(QPointF(a.x() + off, a.y() + off))
                path.quadTo(
                    QPointF(mid.x() + off, mid.y() + off),
                    QPointF(b.x() + off, b.y() + off),
                )
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

        for iy in range(cells + 1):
            for ix in range(cells + 1):
                p = point(ix, iy)
                crease(p, point(ix + 1, iy), (ix % cells) * 7 + (iy % cells) * 31 + 1)
                crease(p, point(ix, iy + 1), (ix % cells) * 13 + (iy % cells) * 57 + 2)

        # 4) Poros oscuros (densidad acorde a la tesela de 1024)
        painter.setPen(Qt.PenStyle.NoPen)
        for _ in range(11000):
            x, y = rng.uniform(0, size), rng.uniform(0, size)
            painter.setBrush(QColor(0, 0, 0, rng.randint(9, 22)))
            r = rng.uniform(0.3, 1.1)
            painter.drawEllipse(QPointF(x, y), r, r)

        # 5) Micrograno claro: polvillo sub-píxel, denso y casi
        #    invisible — con antialias queda como satinado del acabado,
        #    JAMÁS motas blancas discernibles.
        for _ in range(38000):
            x, y = rng.uniform(0, size), rng.uniform(0, size)
            painter.setBrush(QColor(200, 215, 240, rng.randint(3, 6)))
            r = rng.uniform(0.07, 0.20)
            painter.drawEllipse(QPointF(x, y), r, r)

        # 6) Pliegues largos y tenues del cuero curvado (curvas suaves)
        for _ in range(70):
            x, y = rng.uniform(-60, size), rng.uniform(0, size)
            length = rng.uniform(120, 340)
            slope = rng.uniform(-18, 18)
            mid = QPointF(x + length / 2, y + slope / 2 + rng.uniform(-9, 9))
            path = QPainterPath(QPointF(x, y))
            path.quadTo(mid, QPointF(x + length, y + slope))
            painter.setPen(QPen(QColor(0, 0, 0, rng.randint(5, 9)), 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        painter.end()
        cls._texture_cache = pm
        return pm

    # --- pintado -------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        # Pantalla completa: la cubierta llena todo, sin esquinas curvas
        win = self.window()
        radius = 0.0 if (win.isFullScreen() or win.isMaximized()) else 12.0

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        # 1) Piel: degradado azul Oxford (más oscuro en secundarias)
        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0.0, self._shade(AZUL_OXFORD_CLARO))
        grad.setColorAt(0.35, self._shade(AZUL_OXFORD))
        grad.setColorAt(1.0, self._shade(AZUL_OXFORD_OSCURO))
        painter.fillPath(path, grad)

        # 2) Grano de cuero teselado
        painter.drawTiledPixmap(rect, self._texture())

        # 3) Viñeteado: bordes ligeramente más oscuros (cuero curvado)
        vign = QRadialGradient(
            QPointF(rect.center()), max(rect.width(), rect.height()) * 0.62
        )
        vign.setColorAt(0.0, QColor(0, 0, 0, 0))
        vign.setColorAt(0.74, QColor(0, 0, 0, 0))
        vign.setColorAt(1.0, QColor(0, 0, 0, 95))
        painter.fillPath(path, vign)

        # (El reflejo dorado que seguía al ratón por la cubierta se
        # retiró a petición del usuario el 2026-08-08: molestaba a la
        # vista. Con él se fue el temporizador que sondeaba el cursor a
        # 25 fps, así que la ventana en reposo ya no repinta nada.)

        # 5) Marco interior estampado: hairline + ornamentos de esquina
        #    (como la orla dorada de la cubierta de los tomos). Empieza
        #    DEBAJO de la barra de título: nunca solapar icono/botones.
        if radius > 0:  # solo en ventana normal, no a pantalla completa
            inner = rect.adjusted(9, self.orla_top, -9, -9)
            hair = QColor(ORO_APAGADO)
            hair.setAlpha(110)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(hair, 1.0))
            painter.drawRoundedRect(inner, 6, 6)
            # Esquinas: remate clásico de encuadernación — L interior de
            # doble filete que acompaña al marco + diamante dorado con
            # centro encendido en la diagonal.
            gold = QColor(ORO_VIEJO)
            gold.setAlpha(180)
            soft = QColor(ORO_APAGADO)
            soft.setAlpha(120)
            L = 24   # largo de los brazos de la L
            G = 4.5  # separación respecto al marco hairline
            for cx, cy, dx, dy in (
                (inner.left(), inner.top(), 1, 1),
                (inner.right(), inner.top(), -1, 1),
                (inner.left(), inner.bottom(), 1, -1),
                (inner.right(), inner.bottom(), -1, -1),
            ):
                # L exterior (filete fino largo)
                painter.setPen(QPen(gold, 1.1))
                painter.drawLine(
                    QPointF(cx + dx * G, cy + dy * G),
                    QPointF(cx + dx * (G + L), cy + dy * G),
                )
                painter.drawLine(
                    QPointF(cx + dx * G, cy + dy * G),
                    QPointF(cx + dx * G, cy + dy * (G + L)),
                )
                # L interior (hairline más corto: doble filete)
                painter.setPen(QPen(soft, 0.8))
                painter.drawLine(
                    QPointF(cx + dx * (G + 3), cy + dy * (G + 3)),
                    QPointF(cx + dx * (G + 3 + L * 0.55), cy + dy * (G + 3)),
                )
                painter.drawLine(
                    QPointF(cx + dx * (G + 3), cy + dy * (G + 3)),
                    QPointF(cx + dx * (G + 3), cy + dy * (G + 3 + L * 0.55)),
                )
                # Diamante en la diagonal, con centro de pan de oro
                center = QPointF(cx + dx * (G + 12.5), cy + dy * (G + 12.5))
                diamond = QPainterPath()
                s = 3.4
                diamond.moveTo(center.x(), center.y() - s)
                diamond.lineTo(center.x() + s, center.y())
                diamond.lineTo(center.x(), center.y() + s)
                diamond.lineTo(center.x() - s, center.y())
                diamond.closeSubpath()
                painter.setPen(QPen(gold, 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(diamond)
                bright = QColor(ORO_CLARO)
                bright.setAlpha(200)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bright)
                painter.drawEllipse(center, 1.1, 1.1)

        # 6) Filete de borde: dorado, avivado hacia donde está el ratón.
        #    Esto SÍ se conserva (lo que se quitó fue la mancha de luz
        #    sobre la piel); el filete es una arista, no una linterna.
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ORO_VIEJO), 1.0))
        painter.drawRoundedRect(rect, radius, radius)
        if self._cerca is not None:
            painter.setPen(
                QPen(self._pincel_de_borde(ORO_VIEJO, 130.0), 1.3)
            )
            painter.drawRoundedRect(rect, radius, radius)
        painter.end()


# ----------------------------------------------------------------------
# Barra de título propia (la ventana no usa el marco de Windows)
# ----------------------------------------------------------------------
class TitleBar(QWidget):
    """
    Barra de título personalizada: icono, título en oro, filete y
    botones de minimizar/cerrar. Arrastrando la barra se mueve la
    ventana (startSystemMove: movimiento nativo y suave).
    """

    def __init__(
        self,
        window: QWidget,
        title: str,
        minimizable: bool = True,
        maximizable: bool = False,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._maximizable = maximizable
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 8, 4)
        lay.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_make_icon().pixmap(20, 20))
        lay.addWidget(icon_lbl)

        text = QLabel(title.upper())
        text.setObjectName("titlebar_text")
        text.setFont(fuente(10, negrita=True))
        lay.addWidget(text)
        lay.addStretch(1)

        if minimizable:
            btn_min = QPushButton("─")
            btn_min.setAutoDefault(False)
            btn_min.setObjectName("winbtn")
            btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_min.setToolTip("Minimizar")
            btn_min.clicked.connect(self._window.showMinimized)
            lay.addWidget(btn_min)

        if maximizable:
            self.btn_max = QPushButton("⛶")
            self.btn_max.setAutoDefault(False)
            self.btn_max.setObjectName("winbtn")
            self.btn_max.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_max.setToolTip("Pantalla completa")
            self.btn_max.clicked.connect(self.toggle_fullscreen)
            lay.addWidget(self.btn_max)

        btn_close = QPushButton("✕")
        btn_close.setAutoDefault(False)
        btn_close.setObjectName("winbtn_close")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("Cerrar")
        btn_close.clicked.connect(self._window.close)
        lay.addWidget(btn_close)

    # --- pantalla completa (DOS estados: normal ↔ completa) ------------
    def toggle_fullscreen(self) -> None:
        if self._window.isFullScreen():
            self._window.showNormal()
        else:
            self._window.showFullScreen()
        self.update_max_glyph()

    def update_max_glyph(self) -> None:
        if hasattr(self, "btn_max"):
            full = self._window.isFullScreen()
            self.btn_max.setText("❐" if full else "⛶")
            self.btn_max.setToolTip(
                "Salir de pantalla completa" if full else "Pantalla completa"
            )

    # --- arrastre / doble clic ----------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        window_fixed = self._window.isFullScreen() or self._window.isMaximized()
        if event.button() == Qt.MouseButton.LeftButton and not window_fixed:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._maximizable and event.button() == Qt.MouseButton.LeftButton:
            self.toggle_fullscreen()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def _wrap_chrome(
    window: QWidget,
    title: str,
    minimizable: bool,
    maximizable: bool = False,
    dark: bool = False,
) -> tuple[QVBoxLayout, QVBoxLayout]:
    """
    Convierte una ventana en "tomo sin marco":

    - Quita el marco de Windows y hace el fondo translúcido.
    - Crea el marco #chrome (piel azul, filete dorado, esquinas
      redondeadas) con sombra propia y una TitleBar personalizada.

    Devuelve `(layout_exterior, layout_contenido)`: el primero se asigna
    a la ventana; en el segundo el llamador monta su contenido.
    """
    window.setWindowFlags(
        window.windowFlags()
        | Qt.WindowType.FramelessWindowHint
    )
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    outer = QVBoxLayout()
    outer.setContentsMargins(
        _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN
    )  # espacio para la sombra pintada por _ShadowFrameMixin

    # OJO: sin QGraphicsDropShadowEffect en el marco (los efectos
    # anidados impiden pintar a los hijos). La sombra de la ventana la
    # dibuja _ShadowFrameMixin.paintEvent(); el cuero, LeatherFrame.
    chrome = LeatherFrame(dark=dark)
    outer.addWidget(chrome)

    inner = QVBoxLayout(chrome)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.setSpacing(0)
    titlebar = TitleBar(
        window, title, minimizable=minimizable, maximizable=maximizable
    )
    inner.addWidget(titlebar)
    inner.addWidget(_filete(1))
    # La orla interior del cuero arranca bajo la barra de título
    chrome.orla_top = titlebar.height() + 8

    # Referencias para el ajuste al maximizar (márgenes y glifo □/❐)
    window._outer_layout = outer  # type: ignore[attr-defined]
    window._titlebar = titlebar  # type: ignore[attr-defined]

    body = QVBoxLayout()
    body.setContentsMargins(24, 16, 24, 18)
    body.setSpacing(10)
    inner.addLayout(body)
    return outer, body


_SHADOW_MARGIN = 14  # margen translúcido reservado para la sombra


class _ShadowFrameMixin:
    """
    Pinta la sombra de la "ventana-tomo" a mano, sin efectos gráficos.

    Dibuja anillos redondeados concéntricos con alfa decreciente en el
    margen translúcido que rodea al marco #chrome. Así los botones
    conservan sus halos dorados (QGraphicsDropShadowEffect propios), que
    Qt no pinta si un ancestro también tiene un efecto aplicado.
    """

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.isFullScreen() or self.isMaximized():  # type: ignore[attr-defined]
            super().paintEvent(event)  # type: ignore[misc]
            return  # pantalla completa: sin margen ni sombra
        painter = QPainter(self)  # type: ignore[arg-type]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        m = _SHADOW_MARGIN
        base = self.rect().adjusted(m, m, -m, -m)  # type: ignore[attr-defined]
        for i in range(m):
            alpha = int(46 * (1 - i / m) ** 2)
            if alpha <= 0:
                continue
            painter.setPen(QPen(QColor(0, 0, 0, alpha), 1))
            painter.drawRoundedRect(
                base.adjusted(-i, -i + 2, i, i + 2), 12 + i, 12 + i
            )
        painter.end()
        super().paintEvent(event)  # type: ignore[misc]


class _EdgeResizeMixin:
    """
    Redimensionado por bordes para ventanas sin marco nativo.

    Un filtro de eventos global (instalado al mostrarse, retirado al
    ocultarse) detecta el ratón sobre la franja del margen de sombra,
    cambia el cursor a las flechas de redimensionar y delega en
    `startSystemResize` (redimensionado nativo y suave de Windows).
    Lo usan la ventana principal Y todos los diálogos secundarios.
    """

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)  # type: ignore[arg-type]
        super().showEvent(event)  # type: ignore[misc]

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)  # type: ignore[arg-type]
        super().hideEvent(event)  # type: ignore[misc]

    def _edges_under_cursor(self, gpos=None) -> Qt.Edge:
        """
        Bordes de la ventana bajo el cursor (franja de agarre).

        `gpos`: posición GLOBAL del evento; si no se da, se usa
        QCursor.pos(). Con eventos siempre pasar la del evento —
        QCursor puede no coincidir (multi-monitor, tests) y el filtro
        llegó a robar clics legítimos (2026-07-26).
        """
        from PySide6.QtGui import QCursor

        none = Qt.Edge(0)
        if (
            not self.isVisible()  # type: ignore[attr-defined]
            or self.isFullScreen()  # type: ignore[attr-defined]
            or self.isMaximized()  # type: ignore[attr-defined]
            or self.isMinimized()  # type: ignore[attr-defined]
        ):
            return none
        if gpos is None:
            gpos = QCursor.pos()
        if not self.geometry().contains(gpos):  # type: ignore[attr-defined]
            return none
        # Solo si lo que hay bajo el cursor pertenece a ESTA ventana
        widget = QApplication.widgetAt(gpos)
        if widget is None or widget.window() is not self:
            return none
        local = self.mapFromGlobal(gpos)  # type: ignore[attr-defined]
        band = _SHADOW_MARGIN + 6
        r = self.rect()  # type: ignore[attr-defined]
        edges = Qt.Edge(0)
        if local.x() <= band:
            edges |= Qt.Edge.LeftEdge
        if local.x() >= r.width() - band:
            edges |= Qt.Edge.RightEdge
        if local.y() <= band:
            edges |= Qt.Edge.TopEdge
        if local.y() >= r.height() - band:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _update_resize_cursor(self, gpos=None) -> None:
        edges = self._edges_under_cursor(gpos)
        E = Qt.Edge
        diag_f = (E.LeftEdge | E.TopEdge, E.RightEdge | E.BottomEdge)
        diag_b = (E.RightEdge | E.TopEdge, E.LeftEdge | E.BottomEdge)
        if edges in diag_f:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)  # type: ignore[attr-defined]
        elif edges in diag_b:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)  # type: ignore[attr-defined]
        elif edges in (E.LeftEdge, E.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)  # type: ignore[attr-defined]
        elif edges in (E.TopEdge, E.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)  # type: ignore[attr-defined]
        else:
            self.unsetCursor()  # type: ignore[attr-defined]

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        etype = event.type()
        if etype == QEvent.Type.MouseMove:
            gpos = event.globalPosition().toPoint()
            self._update_resize_cursor(gpos)
        elif (
            etype == QEvent.Type.MouseButtonPress
            and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton
        ):
            edges = self._edges_under_cursor(event.globalPosition().toPoint())
            if edges:
                handle = self.windowHandle()  # type: ignore[attr-defined]
                if handle is not None:
                    handle.startSystemResize(edges)
                    return True
        return super().eventFilter(watched, event)  # type: ignore[misc]

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)  # type: ignore[misc]
        if event.type() == QEvent.Type.WindowStateChange:
            # Pantalla completa: sin margen de sombra ni esquinas curvas
            full = self.isFullScreen() or self.isMaximized()  # type: ignore[attr-defined]
            m = 0 if full else _SHADOW_MARGIN
            outer = getattr(self, "_outer_layout", None)
            if outer is not None:
                outer.setContentsMargins(m, m, m, m)
            titlebar = getattr(self, "_titlebar", None)
            if titlebar is not None:
                titlebar.update_max_glyph()
            self.update()  # type: ignore[attr-defined]


# ----------------------------------------------------------------------
# Diálogo base sin marco + caja de mensajes propia
# ----------------------------------------------------------------------
class FramelessDialog(_EdgeResizeMixin, _ShadowFrameMixin, QDialog):
    """
    Diálogo con la misma encuadernación sin marco que la ventana.

    Regla de diseño (ver CLAUDE.md): TODAS las ventanas secundarias usan
    el cuero en tonos más oscuros (`dark=True`) para distinguirse de la
    cubierta principal. Cualquier diálogo futuro debe heredar de aquí.
    """

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        # Destrucción DETERMINISTA al cerrar (hilo de la GUI). Sin esto,
        # el recolector de ciclos de Python podía destruir el diálogo
        # (con QTimer/animaciones vivos) desde el hilo del monitor:
        # "QBasicTimer::stop ... different thread" + cierre de la app
        # (2026-07-25). Consecuencia: NO tocar widgets del diálogo
        # después de exec() — capturar valores en accept().
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer, self.body = _wrap_chrome(
            self, title, minimizable=False, dark=True
        )
        self.setLayout(outer)


class GredosMessageBox(FramelessDialog):
    """Sustituto de QMessageBox con la estética del tomo."""

    def __init__(
        self,
        parent: Optional[QWidget],
        title: str,
        text: str,
        cancellable: bool = False,
        accept_text: str = "Aceptar",
        cancel_text: str = "Cancelar",
        estado: Optional[dict] = None,
    ) -> None:
        super().__init__(title, parent)
        # Cómo salió el usuario del aviso. Se anota en un diccionario de
        # fuera porque con WA_DeleteOnClose no se puede consultar el
        # diálogo después de exec(). Por defecto "cerrar": la ✕ y Esc no
        # tocan nada, así que valen como "déjalo estar".
        self._estado = estado if estado is not None else {}
        self._estado.setdefault("salida", "cerrar")

        label = QLabel(text)
        label.setWordWrap(True)
        label.setMinimumWidth(380)
        self.body.addWidget(label)

        row = QHBoxLayout()
        row.addStretch(1)
        btn = GlowButton(accept_text)
        btn.clicked.connect(self._aceptar)
        row.addWidget(btn)
        if cancellable:
            btn_cancel = GlowButton(cancel_text)
            btn_cancel.clicked.connect(self._cancelar)
            row.addWidget(btn_cancel)
        self.body.addLayout(row)

    def _aceptar(self) -> None:
        self._estado["salida"] = "aceptar"
        self.accept()

    def _cancelar(self) -> None:
        self._estado["salida"] = "cancelar"
        self.reject()

    @staticmethod
    def show_info(parent: Optional[QWidget], title: str, text: str) -> None:
        GredosMessageBox(parent, title, text).exec()

    @staticmethod
    def ask(
        parent: Optional[QWidget],
        title: str,
        text: str,
        accept_text: str = "Aceptar",
        cancel_text: str = "Cancelar",
    ) -> bool:
        """Confirmación Sí/Cancelar; True solo si el usuario acepta."""
        return GredosMessageBox.ask_ex(
            parent, title, text, accept_text, cancel_text
        ) == "aceptar"

    @staticmethod
    def ask_ex(
        parent: Optional[QWidget],
        title: str,
        text: str,
        accept_text: str = "Aceptar",
        cancel_text: str = "Cancelar",
    ) -> str:
        """
        Como `ask`, pero distingue las TRES salidas: `"aceptar"`,
        `"cancelar"` (el segundo botón) y `"cerrar"` (la ✕ o Esc). Cerrar
        no es lo mismo que elegir la segunda opción: quien cierra el
        aviso quiere dejarlo, no seguir por otro camino.
        """
        estado: dict = {}
        dialog = GredosMessageBox(
            parent, title, text, cancellable=True,
            accept_text=accept_text, cancel_text=cancel_text, estado=estado,
        )
        dialog.exec()
        return estado.get("salida", "cerrar")


# ----------------------------------------------------------------------
# Diálogo de configuración
# ----------------------------------------------------------------------
class ConfigDialog(FramelessDialog):
    """Diálogo para editar config.json desde la interfaz."""

    def __init__(self, config: Config, parent: Optional[QWidget] = None) -> None:
        super().__init__("Configuración", parent)
        self.config = config

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        self.ed_server = GlowLineEdit(config.imap_server)
        self.ed_port = QSpinBox()
        self.ed_port.setRange(1, 65535)
        self.ed_port.setValue(config.imap_port)
        self.ed_user = GlowLineEdit(config.email_user)
        self.ed_pass = GlowLineEdit(config.email_password)
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_folder = GlowLineEdit(config.mail_folder)

        self.ed_percent = QDoubleSpinBox()
        self.ed_percent.setRange(0.0, 100.0)
        self.ed_percent.setSuffix(" %")
        self.ed_percent.setValue(config.min_discount_percent)

        self.ed_interval = QSpinBox()
        self.ed_interval.setRange(5, 3600)
        self.ed_interval.setSuffix(" s")
        self.ed_interval.setValue(config.check_interval_seconds)

        self.ed_lot_books = QSpinBox()
        self.ed_lot_books.setRange(2, 100)
        self.ed_lot_books.setSuffix(" libros")
        self.ed_lot_books.setValue(config.min_lot_books)

        self.ed_wished = QDoubleSpinBox()
        self.ed_wished.setRange(0.0, 100.0)
        self.ed_wished.setSuffix(" %")
        self.ed_wished.setToolTip(
            "Umbral reducido para tomos marcados ⭐ Deseado (0 = avisar "
            "con cualquier bajada)"
        )
        self.ed_wished.setValue(config.wished_discount_percent)

        self.ed_startup = QSpinBox()
        self.ed_startup.setRange(5, 500)
        self.ed_startup.setSuffix(" correos")
        self.ed_startup.setValue(config.startup_check_count)

        self.ed_summary_time = QTimeEdit()
        self.ed_summary_time.setDisplayFormat("HH:mm")
        parsed = QTime.fromString(config.daily_summary_time, "HH:mm")
        self.ed_summary_time.setTime(parsed if parsed.isValid() else QTime(21, 0))

        self.cb_lots = QCheckBox("Notificar lotes de libros (5 o más)")
        self.cb_lots.setChecked(config.enable_lot_detection)
        self.cb_idle = QCheckBox(
            "Usar IMAP IDLE (tiempo real) si el servidor lo soporta"
        )
        self.cb_idle.setChecked(config.use_imap_idle)
        self.cb_sound = QCheckBox("Sonido en las notificaciones")
        self.cb_sound.setChecked(config.enable_sound)
        self.cb_open = QCheckBox("Abrir el anuncio automáticamente al notificar")
        self.cb_open.setChecked(config.auto_open_link)
        self.cb_summary = QCheckBox("Resumen diario de actividad (notificación)")
        self.cb_summary.setChecked(config.daily_summary_enabled)
        self.cb_mark_read = QCheckBox(
            "Marcar como leídas las ofertas ya procesadas"
        )
        self.cb_mark_read.setChecked(config.mark_processed_as_read)
        self.cb_autostart = QCheckBox("Iniciar automáticamente con Windows")
        # Estado real del registro, no solo lo guardado en config.json
        self.cb_autostart.setChecked(
            autostart.is_enabled() or config.start_with_windows
        )

        # La clave de OpenAI se retiró de aquí el 2026-08-05, con el
        # botón «Describir»: ya no queda nada en la aplicación que use
        # la API, y un campo que no hace nada es peor que ninguno.
        self.ed_tesseract = GlowLineEdit(config.tesseract_path)
        self.ed_tesseract.setPlaceholderText(
            "vacío = buscarlo donde se instala por defecto"
        )
        self.ed_tesseract.setToolTip(
            "Ruta de tesseract.exe, para reconocer las páginas de un PDF\n"
            "que no traen texto. Solo si lo instalaste en otra carpeta."
        )

        form.addRow("Servidor IMAP:", self.ed_server)
        form.addRow("Puerto:", self.ed_port)
        form.addRow("Correo:", self.ed_user)
        form.addRow("Contraseña (de aplicación):", self.ed_pass)
        form.addRow("Carpeta:", self.ed_folder)
        form.addRow("Descuento mínimo:", self.ed_percent)
        form.addRow("Umbral para deseados:", self.ed_wished)
        form.addRow("Intervalo de revisión:", self.ed_interval)
        form.addRow("Tamaño mínimo de lote:", self.ed_lot_books)
        form.addRow("Revisar al arrancar:", self.ed_startup)
        form.addRow("Hora del resumen diario:", self.ed_summary_time)
        form.addRow("Ruta de Tesseract:", self.ed_tesseract)
        form.addRow(self.cb_summary)
        form.addRow(self.cb_mark_read)
        form.addRow(self.cb_lots)
        form.addRow(self.cb_idle)
        form.addRow(self.cb_sound)
        form.addRow(self.cb_open)
        form.addRow(self.cb_autostart)
        self.body.addLayout(form)

        self.body.addSpacing(4)
        self.body.addWidget(_filete(1))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_ok = GlowButton("Guardar")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = GlowButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_ok)
        buttons.addWidget(btn_cancel)
        self.body.addLayout(buttons)

    def accept(self) -> None:  # noqa: A003 - API Qt
        """
        Captura y guarda ANTES de cerrar: con WA_DeleteOnClose los
        widgets dejan de existir tras exec(), así que el volcado no
        puede hacerse después.
        """
        self.apply_to_config()
        super().accept()

    def apply_to_config(self) -> None:
        """Vuelca los campos del formulario a la configuración y la guarda."""
        self.config.imap_server = self.ed_server.text().strip()
        self.config.imap_port = self.ed_port.value()
        self.config.email_user = self.ed_user.text().strip()
        self.config.email_password = self.ed_pass.text()
        self.config.mail_folder = self.ed_folder.text().strip() or "INBOX"
        self.config.min_discount_percent = self.ed_percent.value()
        self.config.wished_discount_percent = self.ed_wished.value()
        self.config.check_interval_seconds = self.ed_interval.value()
        self.config.min_lot_books = self.ed_lot_books.value()
        self.config.startup_check_count = self.ed_startup.value()
        self.config.daily_summary_time = self.ed_summary_time.time().toString("HH:mm")
        self.config.daily_summary_enabled = self.cb_summary.isChecked()
        self.config.mark_processed_as_read = self.cb_mark_read.isChecked()
        self.config.enable_lot_detection = self.cb_lots.isChecked()
        self.config.use_imap_idle = self.cb_idle.isChecked()
        self.config.enable_sound = self.cb_sound.isChecked()
        self.config.auto_open_link = self.cb_open.isChecked()
        self.config.start_with_windows = self.cb_autostart.isChecked()
        self.config.tesseract_path = self.ed_tesseract.text().strip()
        autostart.set_enabled(self.config.start_with_windows)
        self.config.save()


# ----------------------------------------------------------------------
# Diálogo de historial
# ----------------------------------------------------------------------
class _NumItem(QTableWidgetItem):
    """Celda con texto formateado pero ORDEN numérico real."""

    def __init__(self, text: str, value: Optional[float]) -> None:
        super().__init__(text)
        self._value = value if value is not None else float("-inf")

    def __lt__(self, other) -> bool:  # noqa: ANN001 - API Qt
        return self._value < getattr(other, "_value", float("-inf"))


class HistoryDialog(FramelessDialog):
    """
    Historial de alertas guardado en SQLite.

    `estado` (opcional) filtra la lista: con 'notificado' muestra SOLO
    las ofertas que cumplieron las condiciones y fueron notificadas.

    Diseño: sin números de fila ni rejilla; separadores de hilo dorado,
    estado coloreado, enlace como "Ver anuncio", cabeceras clicables
    para ordenar y selector de orden (mayor/menor descuento...).
    """

    HEADERS = ("Fecha", "Título", "Antes", "Ahora", "Dto.", "Estado", "Enlace")
    _COL_FECHA, _COL_TITULO, _COL_ANT, _COL_NEW, _COL_DTO, _COL_ESTADO, _COL_LINK = range(7)

    _ESTADO_STYLE = {
        "notificado": (ORO_CLARO, "Notificado ✦"),
        "ignorado": ("#8a7f5a", "Ignorado"),
        "lote": (ORO, "Lote 📦"),
        # Correo de TC analizado pero descartado por el filtro de
        # favoritos (boletín, vendido, puja...): consta en el Historial
        # pero jamás notifica.
        "descartado": ("#635b42", "Descartado"),
    }

    # Opciones del selector: (texto, columna, descendente)
    _SORTS = (
        ("Más recientes", 0, True),
        ("Mayor descuento", 4, True),
        ("Menor descuento", 4, False),
        ("Precio más bajo", 3, False),
        ("Precio más alto", 3, True),
    )

    def __init__(
        self,
        db: Database,
        parent: Optional[QWidget] = None,
        estado: Optional[str] = None,
        titulo: str = "Historial",
    ) -> None:
        super().__init__(titulo, parent)
        self.db = db
        self.resize(940, 520)
        self.setMinimumSize(760, 420)

        # --- Barra superior: recuento + buscador + selector de orden ------
        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_count = QLabel("")
        top.addWidget(self.lbl_count)
        top.addStretch(1)
        top.addWidget(QLabel("Buscar:"))
        self.ed_search = GlowLineEdit()
        self.ed_search.setPlaceholderText("título… (p. ej. lote, plutarco)")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setFixedWidth(240)
        self.ed_search.textChanged.connect(self._apply_search)
        top.addWidget(self.ed_search)
        top.addWidget(QLabel("Ordenar por:"))
        self.cmb_sort = QComboBox()
        for text, _col, _desc in self._SORTS:
            self.cmb_sort.addItem(text)
        self.cmb_sort.currentIndexChanged.connect(self._apply_sort)
        top.addWidget(self.cmb_sort)
        self.body.addLayout(top)

        # --- Tabla ---------------------------------------------------------
        self.table = GlowTable(0, len(self.HEADERS))
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setWordWrap(False)
        self.table.cellDoubleClicked.connect(self._open_link)
        self.body.addWidget(self.table)

        bottom = QHBoxLayout()
        self.lbl_hint = QLabel(
            "Doble clic en una fila para abrir el anuncio · clic en una "
            "cabecera para ordenar por esa columna."
        )
        self.lbl_hint.setObjectName("mensaje")
        bottom.addWidget(self.lbl_hint)
        bottom.addStretch(1)
        btn_clear = GlowButton("Limpiar historial")
        btn_clear.clicked.connect(self._clear_history)
        bottom.addWidget(btn_clear)
        self.body.addLayout(bottom)

        # --- Datos ---------------------------------------------------------
        rows = db.get_history(limit=1000, estado=estado)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            fecha = str(row["fecha"])[:16].replace("T", "  ")  # sin segundos
            item_fecha = QTableWidgetItem(fecha)
            item_fecha.setForeground(QColor("#9d8a55"))  # dato secundario
            self.table.setItem(i, self._COL_FECHA, item_fecha)

            item_titulo = QTableWidgetItem(row["titulo"])
            item_titulo.setToolTip(row["titulo"])
            self.table.setItem(i, self._COL_TITULO, item_titulo)

            def _precio(valor) -> str:
                return format_price(valor) if valor is not None else "—"

            self.table.setItem(
                i, self._COL_ANT,
                _NumItem(_precio(row["precio_ant"]), row["precio_ant"]),
            )
            self.table.setItem(
                i, self._COL_NEW,
                _NumItem(_precio(row["precio_new"]), row["precio_new"]),
            )

            dto = row["descuento"]
            item_dto = _NumItem(f"{dto:.0f} %" if dto is not None else "—", dto)
            if dto is not None and dto >= 70:
                item_dto.setForeground(QColor(ORO_CLARO))
            self.table.setItem(i, self._COL_DTO, item_dto)

            color, texto = self._ESTADO_STYLE.get(
                row["estado"], (ORO_TEXTO, row["estado"])
            )
            item_estado = QTableWidgetItem(texto)
            item_estado.setForeground(QColor(color))
            self.table.setItem(i, self._COL_ESTADO, item_estado)

            enlace = row["enlace"] or ""
            item_link = QTableWidgetItem("Ver anuncio ⧉" if enlace else "—")
            item_link.setData(Qt.ItemDataRole.UserRole, enlace)
            if enlace:
                item_link.setForeground(QColor(ORO))
                item_link.setToolTip(enlace)
            self.table.setItem(i, self._COL_LINK, item_link)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col, base in (
            (self._COL_FECHA, 150),
            (self._COL_ANT, 85),
            (self._COL_NEW, 85),
            (self._COL_DTO, 75),
            (self._COL_ESTADO, 115),
            (self._COL_LINK, 125),
        ):
            width = max(base, GlowHeader.required_width(self.HEADERS[col]))
            header.setSectionResizeMode(col, header.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(
            self._COL_TITULO, header.ResizeMode.Stretch
        )
        self.table.setSortingEnabled(True)  # tras poblar: no re-baraja el relleno
        self._estado = estado
        self._total = len(rows)
        self._apply_sort(0)
        self._update_count(self._total)

    def _update_count(self, visible: int) -> None:
        text = (
            f"{visible} entrada(s)"
            if visible == self._total
            else f"{visible} de {self._total} entrada(s)"
        )
        if self._estado:
            text += f" · filtro: {self._estado}"
        self.lbl_count.setText(text)

    def _apply_search(self, text: str) -> None:
        """Filtra las filas por título (sin tildes ni mayúsculas)."""
        query = normalize(text.strip())
        visible = 0
        for i in range(self.table.rowCount()):
            item = self.table.item(i, self._COL_TITULO)
            match = not query or (
                item is not None and query in normalize(item.text())
            )
            self.table.setRowHidden(i, not match)
            if match:
                visible += 1
        self._update_count(visible)

    def _apply_sort(self, index: int) -> None:
        _text, col, desc = self._SORTS[index]
        self.table.sortItems(
            col,
            Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder,
        )

    def _clear_history(self) -> None:
        """Borra (con confirmación) las entradas que muestra esta vista."""
        if self._total == 0:
            GredosMessageBox.show_info(
                self, "Limpiar historial", "No hay entradas que borrar."
            )
            return
        alcance = (
            f"las {self._total} entrada(s) de esta vista "
            f"(estado: {self._estado})"
            if self._estado
            else f"las {self._total} entrada(s) del historial completo"
        )
        if not GredosMessageBox.ask(
            self,
            "Limpiar historial",
            f"Se borrarán {alcance}.\n\n"
            "Los precios registrados (gráfica) y el control de "
            "duplicados NO se tocan: nada volverá a notificarse dos "
            "veces. ¿Continuar?",
            accept_text="Borrar",
        ):
            return
        deleted = self.db.clear_history(self._estado)
        self.table.setRowCount(0)
        self._total = 0
        self._update_count(0)
        logger.info("Historial limpiado desde la GUI (%d filas).", deleted)

    def _open_link(self, row: int, _col: int) -> None:
        """Doble clic en CUALQUIER celda de la fila: abre el anuncio."""
        item = self.table.item(row, self._COL_LINK)
        url = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if url and str(url).startswith("http"):
            webbrowser.open(str(url))
            return
        # Sin enlace (descartados, o anuncios cuyo botón "Ver" apuntaba
        # a tracking y quedó vetado): avisar en vez de no hacer nada.
        titulo_item = self.table.item(row, self._COL_TITULO)
        titulo = titulo_item.text() if titulo_item else "esta entrada"
        self.lbl_hint.setText(
            f"⚠ '{titulo[:50]}' no tiene enlace guardado — el correo no "
            "traía una URL válida del anuncio."
        )


# ----------------------------------------------------------------------
# Diálogo de estadísticas del dataset (dataset.py)
# ----------------------------------------------------------------------
class DatasetDialog(FramelessDialog):
    """
    Estadísticas del conjunto de pruebas y cobertura de estrategias.

    Muestra los datos de `dataset_stats.json` a través de DatasetManager
    y el tamaño de la cola de validación. Si la cobertura por
    heurísticas crece, Todocolección está cambiando el formato de sus
    correos y conviene revisar los extractores.
    """

    _ROWS = (
        ("total_processed", "Correos procesados"),
        ("archived", "Correos archivados"),
        ("validated", "Correos validados"),
        ("active_regression_cases", "Casos de regresión activos"),
        ("rotated_out", "Eliminados por rotación"),
        ("last_training_date", "Último entrenamiento"),
    )
    _COVERAGE = (
        ("html_especializado", "HTML especializado"),
        ("parser_semantico", "Parser semántico"),
        ("regex", "Regex"),
        ("heuristica", "Heurísticas"),
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Estadísticas del dataset", parent)
        self.setMinimumWidth(470)

        self._value_labels: dict[str, QLabel] = {}

        grid = QGridLayout()
        row = 0
        for key, label in self._ROWS:
            grid.addWidget(QLabel(label + ":"), row, 0)
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._value_labels[key] = value
            grid.addWidget(value, row, 1)
            row += 1
        self.body.addLayout(grid)

        self.body.addSpacing(6)
        self.body.addWidget(_filete())
        cov_title = QLabel("Cobertura de estrategias del parser")
        cov_title.setStyleSheet(f"color: {ORO};")
        self.body.addWidget(cov_title)

        cov_grid = QGridLayout()
        for i, (key, label) in enumerate(self._COVERAGE):
            cov_grid.addWidget(QLabel(label + ":"), i, 0)
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._value_labels["cov_" + key] = value
            cov_grid.addWidget(value, i, 1)
        self.body.addLayout(cov_grid)

        self.body.addSpacing(6)
        self.body.addWidget(_filete())
        self.lbl_pending = QLabel("—")
        self.body.addWidget(self.lbl_pending)

        buttons = QHBoxLayout()
        btn_refresh = GlowButton("Actualizar")
        btn_refresh.clicked.connect(self.refresh)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(btn_refresh)
        buttons.addWidget(btn_close)
        self.body.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        """Relee dataset_stats.json y la cola de validación."""
        if not _DATASET_OK:
            self.lbl_pending.setText("dataset.py no disponible.")
            return
        try:
            ds = DatasetManager()
            stats = ds.get_stats()
            pending = len(ds.pending_validation())
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudieron leer las estadísticas: %s", exc)
            self.lbl_pending.setText(f"Error leyendo estadísticas: {exc}")
            return

        for key, _label in self._ROWS:
            value = stats.get(key)
            if key == "last_training_date":
                value = (value or "—").replace("T", "  ")
            self._value_labels[key].setText(str(value if value is not None else "—"))

        total = stats.get("total_processed") or 0
        coverage = stats.get("coverage", {})
        for key, _label in self._COVERAGE:
            n = coverage.get(key, 0)
            pct = f" ({100 * n / total:.0f} %)" if total else ""
            self._value_labels["cov_" + key].setText(f"{n}{pct}")

        if pending:
            self.lbl_pending.setText(
                f"Cola de validación: {pending} correo(s) pendiente(s)  →  "
                "python generate_expected.py"
            )
        else:
            self.lbl_pending.setText("Cola de validación: al día ✓")


# ----------------------------------------------------------------------
# Evolución de precios por libro
# ----------------------------------------------------------------------
class PriceChart(QWidget):
    """
    Gráfica de líneas en oro sobre azul, sin dependencias, con el brillo
    característico: un resplandor dorado sigue la punta del ratón y el
    punto de datos más cercano se enciende mostrando su fecha y precio.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[str, float]] = []
        self._coords: list[QPointF] = []
        self._mouse: Optional[QPointF] = None
        self.setMinimumSize(420, 240)
        self.setMouseTracking(True)

    def set_points(self, points: list[tuple]) -> None:
        """Acepta `(fecha, precio)` o `(fecha, precio, url)` por punto."""
        self._points = [
            (p[0], p[1], p[2] if len(p) > 2 else None) for p in points
        ]
        self.update()

    def _nearest_index(self, pos: QPointF, max_dist: float = 8.0) -> Optional[int]:
        # max_dist = radio del punto pintado (3-3.5 px) + margen corto:
        # la zona de clic queda ceñida al botón del precio (petición
        # 2026-07-26: con 14 px se sentía desplazada).
        """Índice del punto de datos bajo el cursor (o None si lejos)."""
        if not self._coords:
            return None
        idx = min(
            range(len(self._coords)),
            key=lambda i: (self._coords[i].x() - pos.x()) ** 2
            + (self._coords[i].y() - pos.y()) ** 2,
        )
        c = self._coords[idx]
        dist = ((c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2) ** 0.5
        return idx if dist <= max_dist else None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Clic sobre un punto: abre la publicación de la que salió."""
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._nearest_index(event.position())
            if idx is not None:
                url = self._points[idx][2]
                if url:
                    webbrowser.open(url)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._mouse = event.position()
        # Mano sobre un punto con publicación asociada
        idx = self._nearest_index(event.position())
        if idx is not None and self._points[idx][2]:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._mouse = None
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)

        # Campo de fondo
        painter.setPen(QPen(QColor(ORO_APAGADO), 1))
        painter.setBrush(QColor(AZUL_CAMPO))
        painter.drawRoundedRect(rect, 6, 6)

        painter.setFont(fuente(9))

        if len(self._points) < 1:
            painter.setPen(QColor(ORO_VIEJO))
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignCenter, "Sin datos todavía"
            )
            painter.end()
            return

        prices = [p[1] for p in self._points]
        lo, hi = min(prices), max(prices)
        if hi == lo:
            hi = lo + 1  # evitar división por cero: línea plana centrada

        m_left, m_right, m_top, m_bottom = 58, 18, 18, 30
        plot = rect.adjusted(m_left, m_top, -m_right, -m_bottom)

        # Rejilla horizontal (mín, medio, máx) con etiquetas de precio
        painter.setPen(QPen(QColor(51, 69, 110), 1, Qt.PenStyle.DashLine))
        for frac in (0.0, 0.5, 1.0):
            y = plot.bottom() - frac * plot.height()
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
        painter.setPen(QColor(ORO_VIEJO))
        for frac in (0.0, 0.5, 1.0):
            y = plot.bottom() - frac * plot.height()
            value = lo + frac * (hi - lo)
            painter.drawText(
                6, int(y) + 4, format_price(round(value, 2))
            )

        # Posiciones de los puntos
        n = len(self._points)
        coords: list[QPointF] = []
        for i, (_fecha, price, _url) in enumerate(self._points):
            x = plot.left() + (plot.width() * (i / (n - 1))) if n > 1 else plot.center().x()
            y = plot.bottom() - (price - lo) / (hi - lo) * plot.height()
            coords.append(QPointF(x, y))

        # Relleno bajo la curva: veladura dorada que se desvanece
        if len(coords) > 1:
            area = QPainterPath()
            area.moveTo(coords[0].x(), plot.bottom())
            for c in coords:
                area.lineTo(c)
            area.lineTo(coords[-1].x(), plot.bottom())
            area.closeSubpath()
            fill = QLinearGradient(0, plot.top(), 0, plot.bottom())
            gold_soft = QColor(ORO)
            gold_soft.setAlpha(46)
            fill.setColorAt(0.0, gold_soft)
            fill.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillPath(area, fill)

        # Línea en oro + puntos en oro claro
        if len(coords) > 1:
            painter.setPen(QPen(QColor(ORO), 2))
            for a, b in zip(coords, coords[1:]):
                painter.drawLine(a, b)
        painter.setPen(QPen(QColor(ORO_CLARO), 1))
        painter.setBrush(QColor(ORO_CLARO))
        for c in coords:
            painter.drawEllipse(c, 3, 3)
        self._coords = coords

        # Brillo característico: resplandor que sigue la punta del ratón
        # sobre el campo + punto más cercano encendido con su dato
        if self._mouse is not None and plot.contains(self._mouse.toPoint()):
            sheen = QRadialGradient(self._mouse, 90.0)
            warm = QColor(ORO)
            warm.setAlpha(26)
            sheen.setColorAt(0.0, warm)
            sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(sheen)
            painter.drawRect(plot)

            if coords:
                idx = min(
                    range(len(coords)),
                    key=lambda i: abs(coords[i].x() - self._mouse.x()),
                )
                punto = coords[idx]
                # Halo de pan de oro sobre el punto
                halo = QRadialGradient(punto, 16.0)
                core = QColor("#fff6d0")
                core.setAlpha(220)
                mid_h = QColor(ORO_CLARO)
                mid_h.setAlpha(120)
                halo.setColorAt(0.0, core)
                halo.setColorAt(0.5, mid_h)
                halo.setColorAt(1.0, QColor(0, 0, 0, 0))
                painter.setBrush(halo)
                painter.drawEllipse(punto, 16, 16)
                painter.setBrush(QColor("#fff6d0"))
                painter.drawEllipse(punto, 3.5, 3.5)

                # Etiqueta con fecha · precio (letras estampadas)
                fecha, precio = self._points[idx][0], self._points[idx][1]
                texto = f"{fecha[:10]} · {format_price(precio)}"
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(texto)
                tx = min(max(punto.x() - tw / 2, plot.left() + 2),
                         plot.right() - tw - 2)
                ty = punto.y() - 14
                if ty < plot.top() + fm.height():
                    ty = punto.y() + 22
                painter.setPen(QColor(0, 0, 0, 150))
                painter.drawText(int(tx) + 1, int(ty) + 1, texto)
                painter.setPen(QColor(ORO_CLARO))
                painter.drawText(int(tx), int(ty), texto)

        # Fechas de primer y último punto
        painter.setPen(QColor(ORO_VIEJO))
        first_date = self._points[0][0][:10]
        last_date = self._points[-1][0][:10]
        painter.drawText(
            plot.left(), rect.bottom() - 8, first_date
        )
        if n > 1:
            fm = painter.fontMetrics()
            painter.drawText(
                plot.right() - fm.horizontalAdvance(last_date),
                rect.bottom() - 8,
                last_date,
            )
        painter.end()


class PriceHistoryDialog(FramelessDialog):
    """Historial de precios por libro: lista de títulos + gráfica."""

    # Personalizable por subclases (LotesDialog reutiliza toda la ventana)
    _TITLE = "Evolución de precios"
    _EMPTY_TEXT = "Sin series de precios todavía."
    _HINT_TEXT = "Selecciona un libro de la lista."
    _LOTES = False           # LotesDialog trabaja el espacio `lote::`
    # Orden de la lista por el ÚLTIMO precio de cada serie
    _PRECIOS = ("Sin ordenar", "Precio mayor", "Precio menor")

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(self._TITLE, parent)
        self.db = db
        self.resize(960, 500)
        self.setMinimumSize(780, 440)

        # Barra superior: contador + filtros (como el resto de tablas)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_count = QLabel("")
        top.addWidget(self.lbl_count)
        top.addStretch(1)
        top.addWidget(QLabel("Precio:"))
        self.cmb_price = QComboBox()
        self.cmb_price.addItems(self._PRECIOS)
        self.cmb_price.setToolTip(
            "Ordena la lista por el último precio de cada serie"
        )
        self.cmb_price.currentIndexChanged.connect(lambda _i: self._reload())
        top.addWidget(self.cmb_price)
        top.addWidget(QLabel("Buscar:"))
        self.ed_search = GlowLineEdit()
        self.ed_search.setPlaceholderText("título…")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setFixedWidth(220)
        self.ed_search.textChanged.connect(self._apply_search)
        top.addWidget(self.ed_search)
        self.body.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(12)

        # Tabla de títulos con la estética común (GlowTable + GlowHeader)
        self.table = GlowTable(0, 2)
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(("Título", "Precio"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setWordWrap(False)
        self.table.setFixedWidth(430)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        w_precio = max(96, GlowHeader.required_width("Precio"))
        header.setSectionResizeMode(1, header.ResizeMode.Fixed)
        self.table.setColumnWidth(1, w_precio)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        row.addWidget(self.table)

        right = QVBoxLayout()
        self.chart = PriceChart()
        right.addWidget(self.chart, 1)
        self.lbl_info = QLabel(self._HINT_TEXT)
        self.lbl_info.setWordWrap(True)
        right.addWidget(self.lbl_info)
        row.addLayout(right, 1)
        self.body.addLayout(row)

        # Sin botón "Limpiar": el histórico de precios se conserva
        # íntegro SIEMPRE (petición 2026-07-25).
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_close)
        self.body.addLayout(buttons)

        self._reload()

    def _series(self) -> list[tuple[str, str, int]]:
        """Series a listar `(clave, título, nº puntos)`; hook de subclases."""
        return self.db.price_history_titles()

    def _reload(self) -> None:
        series = self._series()
        # Un solo viaje a la base para saber de cada serie su último
        # precio y cuánto ha bajado: es lo que ordenan y filtran los
        # combos de arriba.
        # El último precio de cada serie, en UNA consulta: es lo que
        # ordena el filtro "Precio" (pedir la serie entera de cada
        # título para eso serían más de cien consultas).
        stats = self.db.price_history_stats(lotes=self._LOTES)
        criterio = self.cmb_price.currentText()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(series))
        for i, (clave, titulo, n) in enumerate(series):
            ultimo = (stats.get(clave) or {}).get("ultimo")
            item_t = QTableWidgetItem(titulo)
            item_t.setToolTip(titulo)
            item_t.setData(Qt.ItemDataRole.UserRole, clave)
            self.table.setItem(i, 0, item_t)
            celda = _NumItem(
                format_price(ultimo) if ultimo is not None else "—",
                ultimo if ultimo is not None else -1.0,
            )
            celda.setToolTip(f"{n} precio(s) registrados")
            self.table.setItem(i, 1, celda)
        self.table.setSortingEnabled(True)
        # El orden lo decide el combo. Sin criterio, manda el de la base
        # (lo más reciente arriba) y hay que RETIRAR el indicador de la
        # cabecera: si no, la tabla se reordena sola por la última
        # columna que se pulsó (mismo tropiezo que en Textos).
        cabecera = self.table.horizontalHeader()
        if criterio == self._PRECIOS[0]:
            cabecera.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        else:
            self.table.sortItems(
                1,
                Qt.SortOrder.DescendingOrder if criterio == "Precio mayor"
                else Qt.SortOrder.AscendingOrder,
            )
        self._total = len(series)
        self._apply_search(self.ed_search.text())
        if self._total:
            self.table.selectRow(0)
        else:
            self.chart.set_points([])
            self.lbl_info.setText(self._EMPTY_TEXT)

    def _apply_search(self, text: str) -> None:
        query = normalize(text.strip())
        visible = 0
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            match = not query or (
                item is not None and query in normalize(item.text())
            )
            self.table.setRowHidden(i, not match)
            if match:
                visible += 1
        self.lbl_count.setText(
            f"{visible} serie(s)" if visible == self._total
            else f"{visible} de {self._total} serie(s)"
        )

    def _on_select(self) -> None:
        fila = self.table.currentRow()
        item = self.table.item(fila, 0) if fila >= 0 else None
        if item is None:
            return
        clave = item.data(Qt.ItemDataRole.UserRole)
        points = self.db.price_history_for(clave)
        self.chart.set_points(points)
        if points:
            prices = [p[1] for p in points]
            self.lbl_info.setText(
                f"{len(points)} precio(s) registrados · "
                f"mínimo {format_price(min(prices))} · "
                f"máximo {format_price(max(prices))} · "
                f"último {format_price(prices[-1])}"
            )


# ----------------------------------------------------------------------
# Lotes: series de precios de lotes + publicaciones vigiladas
# ----------------------------------------------------------------------
def _titulo_desde_url(url: str) -> str:
    """Nombre legible desde el slug de la URL (respaldo sin cruce)."""
    import re
    from urllib.parse import unquote, urlparse

    path = unquote(urlparse(url).path or "")
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"~x\d+$", "", slug)
    slug = slug.replace("-", " ").replace("_", " ").strip()
    return (slug[:1].upper() + slug[1:]) if slug else url


class TomoPickerDialog(FramelessDialog):
    """
    Selector de tomos de la colección para componer el contenido de un
    LOTE a mano: arriba la LISTA de los títulos ya registrados en el
    lote (en vivo; doble clic los saca), abajo la colección completa
    con buscador único (nº de tomo, autor u obra) + casillas.
    La selección queda en `self.selected` (capturada en accept(): nunca
    tocar widgets tras exec(), regla WA_DeleteOnClose).
    """

    def __init__(
        self,
        tomos: list,
        preseleccion: set[int],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__("Títulos incluidos en el lote", parent)
        # Alto suficiente para la lista del lote (150) + la colección:
        # si el contenido desborda, los widgets se solapan (trampa
        # conocida del proyecto).
        self.resize(760, 660)
        self.setMinimumSize(640, 540)
        self._tomos = tomos
        # id(tomo) → índice: dos tomos distintos podrían ser iguales por
        # campos (dataclass), así que jamás usar list.index() aquí.
        self._indice = {id(t): i for i, t in enumerate(tomos)}
        self.selected: list = []

        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_count = QLabel("")
        top.addWidget(self.lbl_count)
        top.addStretch(1)
        top.addWidget(QLabel("Buscar:"))
        self.ed_search = GlowLineEdit()
        self.ed_search.setPlaceholderText("nº, autor u obra…")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setFixedWidth(240)
        self.ed_search.textChanged.connect(self._apply_search)
        top.addWidget(self.ed_search)
        self.body.addLayout(top)

        # --- Títulos YA registrados en el lote (en vivo) -------------------
        self.lot_table = GlowTable(0, 2)
        self.lot_table.setHorizontalHeader(GlowHeader(self.lot_table, compact=True))
        self.lot_table.setHorizontalHeaderLabels(("Nº", "En el lote"))
        self.lot_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.lot_table.setAlternatingRowColors(True)
        self.lot_table.setShowGrid(False)
        self.lot_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.lot_table.verticalHeader().setVisible(False)
        self.lot_table.verticalHeader().setDefaultSectionSize(28)
        self.lot_table.setWordWrap(False)
        self.lot_table.setMaximumHeight(150)
        self.lot_table.setToolTip(
            "Títulos registrados en este lote · doble clic para quitarlo"
        )
        self.lot_table.cellDoubleClicked.connect(self._remove_from_lot)
        lot_header = self.lot_table.horizontalHeader()
        lot_header.setStretchLastSection(False)
        lot_header.setSectionResizeMode(0, lot_header.ResizeMode.Fixed)
        self.lot_table.setColumnWidth(
            0, max(56, GlowHeader.required_width("Nº"))
        )
        lot_header.setSectionResizeMode(1, lot_header.ResizeMode.Stretch)
        self.body.addWidget(self.lot_table)
        self.body.addWidget(_filete(1))

        self.table = GlowTable(0, 4)
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(("✓", "Nº", "Autor(es)", "Título"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setWordWrap(False)
        self.table.itemChanged.connect(self._on_item_changed)
        # Doble clic en cualquier celda = alternar la casilla de la fila
        self.table.cellDoubleClicked.connect(self._toggle_row)

        self.table.setRowCount(len(tomos))
        for i, t in enumerate(tomos):
            marcado = t.orden in preseleccion
            item_chk = _NumItem("", 1 if marcado else 0)
            item_chk.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item_chk.setCheckState(
                Qt.CheckState.Checked if marcado else Qt.CheckState.Unchecked
            )
            item_chk.setData(Qt.ItemDataRole.UserRole, i)
            self.table.setItem(i, 0, item_chk)
            self.table.setItem(
                i, 1, _NumItem(str(t.orden or t.numero), t.orden or 0)
            )
            self.table.setItem(i, 2, QTableWidgetItem(t.autor))
            titulo = t.canonical_title()
            item_t = QTableWidgetItem(titulo)
            item_t.setToolTip(titulo)
            self.table.setItem(i, 3, item_t)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col, base in ((0, 44), (1, 56), (2, 170)):
            ancho = max(base, GlowHeader.required_width(
                self.table.horizontalHeaderItem(col).text()
            ))
            header.setSectionResizeMode(col, header.ResizeMode.Fixed)
            self.table.setColumnWidth(col, ancho)
        header.setSectionResizeMode(3, header.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.body.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_ok = GlowButton("Aceptar")
        btn_ok.clicked.connect(self.accept)
        buttons.addWidget(btn_ok)
        btn_cancel = GlowButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        self.body.addLayout(buttons)
        self._update_count()
        self._refresh_lot_table()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        # La casilla también ORDENA por la cabecera: _value al día
        if item.column() == 0:
            item._value = (
                1 if item.checkState() == Qt.CheckState.Checked else 0
            )
        self._update_count()
        self._refresh_lot_table()

    def _refresh_lot_table(self) -> None:
        """Vuelca la selección actual en la lista «En el lote»."""
        elegidos = self._checked_tomos()
        self.lot_table.setRowCount(len(elegidos))
        for fila, t in enumerate(elegidos):
            self.lot_table.setItem(
                fila, 0, _NumItem(str(t.orden or t.numero), t.orden or 0)
            )
            titulo = t.canonical_title()
            item = QTableWidgetItem(titulo)
            item.setToolTip(f"{titulo}\n(doble clic para quitarlo del lote)")
            item.setData(Qt.ItemDataRole.UserRole, self._indice[id(t)])
            self.lot_table.setItem(fila, 1, item)

    def _remove_from_lot(self, fila: int, _col: int) -> None:
        """Doble clic en la lista del lote: desmarca ese tomo."""
        item = self.lot_table.item(fila, 1)
        if item is None:
            return
        indice = item.data(Qt.ItemDataRole.UserRole)
        for i in range(self.table.rowCount()):
            chk = self.table.item(i, 0)
            if chk is not None and chk.data(Qt.ItemDataRole.UserRole) == indice:
                chk.setCheckState(Qt.CheckState.Unchecked)
                return

    def _toggle_row(self, fila: int, _col: int) -> None:
        item = self.table.item(fila, 0)
        if item is not None:
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )

    def _apply_search(self, text: str) -> None:
        query = normalize(text.strip())
        for i in range(self.table.rowCount()):
            chk = self.table.item(i, 0)
            t = self._tomos[chk.data(Qt.ItemDataRole.UserRole)]
            haystack = normalize(
                f"{t.orden} {t.numero} {t.autor} {t.obras} {t.sufijo}"
            )
            self.table.setRowHidden(i, bool(query) and query not in haystack)

    def _checked_tomos(self) -> list:
        elegidos = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                elegidos.append(self._tomos[item.data(Qt.ItemDataRole.UserRole)])
        elegidos.sort(key=lambda t: (t.orden is None, t.orden))
        return elegidos

    def _update_count(self) -> None:
        try:
            n = len(self._checked_tomos())
        except RuntimeError:
            return
        self.lbl_count.setText(f"{n} tomo(s) en el lote")

    def accept(self) -> None:  # captura ANTES de cerrar (WA_DeleteOnClose)
        self.selected = self._checked_tomos()
        super().accept()


class LotesDialog(PriceHistoryDialog):
    """
    Misma ventana que Precios pero SOLO con las series de LOTES
    (espacio de claves `lote::`): las detectadas por el monitor en los
    avisos de favoritos y las publicaciones que el usuario vigila a
    mano. "Añadir lote": se pega la URL, el Chromium embebido extrae
    precio + contenido, y el NOMBRE del lote se construye con los
    títulos CANÓNICOS de la colección reconocidos en el título y la
    descripción del anuncio (respaldo: el slug de la URL).
    """

    _TITLE = "Lotes — evolución de precios"
    _EMPTY_TEXT = "Sin lotes todavía: pega la URL de una publicación."
    _HINT_TEXT = "Selecciona un lote de la lista, o añade uno por URL."
    _LOTES = True

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(db, parent)
        self._fetcher = shared_price_fetcher()
        self._tomos: Optional[list] = None  # colección, carga perezosa

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.ed_url = GlowLineEdit()
        self.ed_url.setPlaceholderText("https://… (publicación del lote)")
        self.ed_url.setClearButtonEnabled(True)
        self.ed_url.returnPressed.connect(self._add_lot)
        bar.addWidget(self.ed_url, 1)
        btn_add = GlowButton("Añadir lote")
        btn_add.setToolTip(
            "Vigila la publicación: extrae su precio y nombra el lote\n"
            "con los tomos de la colección reconocidos en el anuncio."
        )
        btn_add.clicked.connect(self._add_lot)
        bar.addWidget(btn_add)
        self.btn_edit = GlowButton("Editar títulos")
        self.btn_edit.setToolTip(
            "Corrige a mano qué tomos de la colección contiene el lote\n"
            "seleccionado (buscador por nº, autor u obra)."
        )
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self._edit_titles)
        bar.addWidget(self.btn_edit)
        btn_del = GlowButton("Quitar")
        btn_del.clicked.connect(self._remove_lot)
        bar.addWidget(btn_del)
        btn_refresh = GlowButton("Actualizar precios")
        btn_refresh.clicked.connect(self._refresh_lots)
        bar.addWidget(btn_refresh)
        self.table.itemSelectionChanged.connect(
            lambda: self.btn_edit.setEnabled(self.table.currentRow() >= 0)
        )

        self.lbl_lots_info = QLabel("")
        self.lbl_lots_info.setObjectName("mensaje")
        self.lbl_lots_info.setWordWrap(True)

        # Cuerpo base: barra superior (0), tabla+gráfica (1), botones
        # (2) → la barra de lotes va justo antes de los botones.
        self.body.insertLayout(2, bar)
        self.body.insertWidget(3, self.lbl_lots_info)
        # Estado inicial: la clase base ya seleccionó la primera fila en
        # su _reload(), ANTES de existir este botón, y volver a
        # seleccionarla no cambia la selección → sin esto, "Editar
        # títulos" se quedaba apagado nada más abrir la ventana.
        self.btn_edit.setEnabled(self.table.currentRow() >= 0)

    def _set_lots_info(self, text: str, error: bool = False) -> None:
        """Línea de mensajes de la barra de lotes; en ROJO si es error."""
        # #c0392b = el rojo de estado ya usado por el icono de bandeja
        self.lbl_lots_info.setStyleSheet(
            "color: #c0392b; font-weight: bold;" if error else ""
        )
        self.lbl_lots_info.setText(text)

    # --- datos ---------------------------------------------------------
    def _series(self) -> list[tuple[str, str, int]]:
        series = list(self.db.lot_price_titles())
        claves = {s[0] for s in series}
        # Lotes vigilados aún sin puntos (fetch fallido): visibles igual
        for r in self.db.get_lotes():
            clave = self.db.lot_key(r["titulo"])
            if clave not in claves:
                claves.add(clave)
                series.append((clave, r["titulo"], 0))
        return series

    def _collection(self) -> list:
        if self._tomos is None:
            from app import collection as col

            self._tomos = col.tomos_from_rows(self.db.get_tomos())
        return self._tomos

    # --- acciones ------------------------------------------------------
    def _add_lot(self) -> None:
        url = self.ed_url.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self._set_lots_info("La URL debe empezar por http(s)://", error=True)
            return
        if any((r["url"] or "").strip() == url for r in self.db.get_lotes()):
            self._set_lots_info("Esa publicación ya está vigilada.")
            return
        self.ed_url.clear()
        self._set_lots_info(f"Consultando {url[:60]}…")
        db, tomos = self.db, self._collection()

        def listo(_url: str, precio: Optional[float], html: str) -> None:
            # BD PRIMERO (el fetcher compartido sobrevive al diálogo);
            # la UI solo se toca si la ventana sigue viva.
            from app import collection as col
            from app.utils import extract_listing_text

            reconocidos = col.match_tomos_multi(
                tomos, extract_listing_text(html)
            )
            if reconocidos:
                titulo = "[LOTE ×%d] %s" % (
                    len(reconocidos),
                    " + ".join(t.canonical_title() for t in reconocidos),
                )
            else:
                titulo = _titulo_desde_url(url)
            lote_id = db.add_lote(titulo, url)
            if precio is not None:
                db.update_lote_price(lote_id, precio)
                ultimo = db.last_lot_price(titulo)
                if ultimo is None or abs(precio - ultimo) >= 0.01:
                    db.add_lot_price_point(titulo, precio, url=url)
            self._reload()
            precio_txt = (
                "sin precio reconocible" if precio is None
                else format_price(precio)
            )
            if not reconocidos:
                self._set_lots_info(
                    "No se reconocieron títulos de la colección en el "
                    f"anuncio ({precio_txt}). Selecciona el lote y usa "
                    "«Editar títulos» para indicarlos a mano.",
                    error=True,
                )
            elif precio is None:
                self._set_lots_info(
                    f"Lote añadido SIN precio reconocible: {titulo[:90]}",
                    error=True,
                )
            else:
                self._set_lots_info(
                    f"Lote añadido: {titulo[:90]} · {format_price(precio)} ✓"
                )

        self._fetcher.fetch(url, listo, want_html=True)

    def _edit_titles(self) -> None:
        """Editor manual de los tomos que contiene el lote seleccionado."""
        fila = self.table.currentRow()
        item = self.table.item(fila, 0) if fila >= 0 else None
        if item is None:
            self._set_lots_info("Selecciona primero un lote.")
            return
        clave = item.data(Qt.ItemDataRole.UserRole)
        titulo_actual = item.text()
        tomos = self._collection()
        # Preselección = lo que YA está registrado en el lote: los
        # canónicos que componen su nombre. Si el nombre no los lleva
        # (lotes detectados por el monitor: el asunto del correo tal
        # cual), se intenta reconocerlos en ese texto para no abrir el
        # selector en blanco.
        por_canonico = {t.canonical_title(): t for t in tomos}
        cuerpo = titulo_actual.split("] ", 1)[-1]
        preseleccion = {
            por_canonico[p].orden
            for p in (s.strip() for s in cuerpo.split(" + "))
            if p in por_canonico
        }
        if not preseleccion:
            from app import collection as col

            preseleccion = {
                t.orden for t in col.match_tomos_multi(tomos, cuerpo)
            }
        dlg = TomoPickerDialog(tomos, preseleccion, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        elegidos = dlg.selected  # capturado en accept()
        if not elegidos:
            self._set_lots_info(
                "No se cambió el nombre: selecciona al menos un tomo.",
                error=True,
            )
            return
        nuevo = "[LOTE ×%d] %s" % (
            len(elegidos), " + ".join(t.canonical_title() for t in elegidos),
        )
        if nuevo == titulo_actual:
            return
        nueva_clave = self.db.rename_lot(clave, nuevo)
        self._reload()
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == nueva_clave:
                self.table.selectRow(i)
                break
        self._set_lots_info(f"Títulos actualizados: {nuevo[:90]} ✓")

    def _remove_lot(self) -> None:
        fila = self.table.currentRow()
        item = self.table.item(fila, 0) if fila >= 0 else None
        if item is None:
            self._set_lots_info("Selecciona primero un lote.")
            return
        clave = item.data(Qt.ItemDataRole.UserRole)
        # CUÁNTAS publicaciones se lleva: varias pueden compartir título
        # (y por tanto serie), y borrar una borraba todas sin avisar.
        afectadas = [
            r for r in self.db.get_lotes()
            if self.db.lot_key(r["titulo"]) == clave
        ]
        detalle = (
            f"\n\nSe quitarán {len(afectadas)} publicaciones vigiladas:\n"
            + "\n".join(f"·  {(r['url'] or '')[:64]}" for r in afectadas[:6])
            if len(afectadas) > 1 else ""
        )
        if not GredosMessageBox.ask(
            self, "Quitar lote",
            "¿Quitar este lote y su serie de precios?\n\n"
            f"{item.text()[:140]}{detalle}",
            accept_text="Quitar",
        ):
            return
        for r in afectadas:
            logger.info("Lote quitado a mano: %s · %s", r["titulo"], r["url"])
            self.db.remove_lote(r["id"])
        self.db.delete_lot_series(clave)
        self._reload()
        self._set_lots_info(f"Lote quitado ({len(afectadas)} publicación/es).")

    def _refresh_lots(self) -> None:
        """Reconsulta el precio de todas las publicaciones vigiladas."""
        rows = self.db.get_lotes()
        if not rows:
            self._set_lots_info("No hay publicaciones de lotes vigiladas.")
            return
        db = self.db
        # El aviso, ANTES de encolar: si la consulta responde al vuelo
        # (respaldo sin Chromium), su mensaje es el que debe quedar.
        self._set_lots_info(f"Consultando {len(rows)} publicación(es)…")
        for r in rows:
            def hecho(
                _url: str, precio: Optional[float], html: str = "",
                lote_id=r["id"], titulo=r["titulo"], url=r["url"],
            ) -> None:
                from app.utils import listing_sold

                # VENDIDO: fuera el precio, que ya no se puede pagar
                if listing_sold(html):
                    db.mark_link_sold("lotes", lote_id)
                    quitados = db.delete_price_points(db.lot_key(titulo), url)
                    self._reload()
                    self._set_lots_info(
                        f"Vendido: {titulo[:44]} — precio retirado"
                        + (f" ({quitados} punto/s)" if quitados else "")
                    )
                    return
                if precio is None:
                    self._set_lots_info(
                        f"Sin precio reconocible en {url[:60]}", error=True
                    )
                    return
                db.update_lote_price(lote_id, precio)
                ultimo = db.last_lot_price(titulo)
                if ultimo is None or abs(precio - ultimo) >= 0.01:
                    db.add_lot_price_point(titulo, precio, url=url)
                self._reload()
                self._set_lots_info(
                    f"Precio actualizado: {format_price(precio)} ✓"
                )

            # Con el HTML: sin él no se puede ver si está vendido
            self._fetcher.fetch(r["url"], hecho, want_html=True)


# ----------------------------------------------------------------------
# Umbrales de descuento por libro
# ----------------------------------------------------------------------
class ThresholdsDialog(FramelessDialog):
    """
    Umbrales por libro/patrón: si el título contiene el patrón, se usa
    su porcentaje en lugar del umbral global (gana el patrón más largo).
    """

    HEADERS = ("Patrón (contenido en el título)", "% mínimo")

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__("Umbrales por libro", parent)
        self.db = db
        self.resize(560, 440)
        self.setMinimumSize(520, 400)

        hint = QLabel(
            "Ejemplo: patrón «plutarco» con 30 % — cualquier tomo de "
            "Plutarco avisa ya con un 30 % de descuento aunque el umbral "
            "global sea mayor."
        )
        hint.setObjectName("mensaje")
        hint.setWordWrap(True)
        self.body.addWidget(hint)

        self.table = GlowTable(0, 2)
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setColumnWidth(0, 340)
        self.table.setColumnWidth(1, 110)
        self.body.addWidget(self.table)

        for patron, pct in db.get_thresholds():
            self._append_row(patron, f"{pct:g}")

        buttons = QHBoxLayout()
        btn_add = GlowButton("Añadir")
        btn_add.clicked.connect(lambda: self._append_row("", ""))
        btn_del = GlowButton("Eliminar fila")
        btn_del.clicked.connect(self._delete_row)
        btn_save = GlowButton("Guardar")
        btn_save.clicked.connect(self._save)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_add)
        buttons.addWidget(btn_del)
        buttons.addStretch(1)
        buttons.addWidget(btn_save)
        buttons.addWidget(btn_close)
        self.body.addLayout(buttons)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("mensaje")
        self.body.addWidget(self.lbl_status)

    def _append_row(self, patron: str, pct: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(patron))
        self.table.setItem(row, 1, QTableWidgetItem(pct))

    def _delete_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _save(self) -> None:
        rows: list[tuple[str, float]] = []
        for i in range(self.table.rowCount()):
            patron_item = self.table.item(i, 0)
            pct_item = self.table.item(i, 1)
            patron = (patron_item.text() if patron_item else "").strip()
            if not patron:
                continue
            try:
                pct = float((pct_item.text() if pct_item else "").replace(",", "."))
            except ValueError:
                self.lbl_status.setText(
                    f"Fila {i + 1}: porcentaje no válido — no se guardó nada."
                )
                return
            if not 0 <= pct <= 100:
                self.lbl_status.setText(
                    f"Fila {i + 1}: el porcentaje debe estar entre 0 y 100."
                )
                return
            rows.append((patron, pct))
        self.db.set_thresholds(rows)
        self.lbl_status.setText(
            f"Guardados {len(rows)} umbral(es). Se aplican de inmediato."
        )


# ----------------------------------------------------------------------
# Consulta de precios de publicaciones con Chromium embebido
# ----------------------------------------------------------------------
# Todocolección (y otros) bloquean urllib/curl por huella TLS (403,
# comprobado 2026-07-26). Un QWebEngineView oculto carga la página como
# un navegador real —pasa la protección y ejecuta el JS de Wallapop— y
# del HTML renderizado se extrae el precio con la cascada de utils.
try:
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class _PaginaSinRuido(QWebEnginePage):
        """Página web que NO reenvía la consola del sitio visitado."""

        def javaScriptConsoleMessage(self, nivel, mensaje, linea,  # noqa: N802
                                     origen) -> None:
            # Se traga todo a propósito: lo que escriben los anuncios de
            # la página ajena no dice nada del programa. Si algún día
            # hace falta depurar una página, quitar este método.
            return

    _WEBENGINE_OK = True
except ImportError:  # pragma: no cover - PySide6 sin Addons
    _WEBENGINE_OK = False


class ListingPriceFetcher(QWidget):
    """
    Consulta ASÍNCRONA del precio de una publicación.

    Carga la URL en un QWebEngineView oculto y extrae el precio del HTML
    renderizado con SONDEO ADAPTATIVO: captura inmediata al terminar la
    carga y, solo si aún no hay precio (páginas que lo pintan por JS,
    p. ej. Wallapop), reintentos cada 400 ms hasta 3 s. Llama a
    `callback(url, precio_o_None)` en el hilo de la GUI.

    Usar SIEMPRE la instancia de `shared_price_fetcher()`: vive lo que
    la aplicación, así las consultas siguen aunque el usuario cierre la
    ficha del tomo (el resultado se guarda en BD y aparece al reabrir).
    """

    _POLL_MS = 400
    _POLL_BUDGET_MS = 3000
    _TIMEOUT_MS = 25000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setVisible(False)
        self._busy = False
        self._queue: list[tuple[str, object]] = []
        if _WEBENGINE_OK:
            self._view = QWebEngineView(self)
            self._view.setVisible(False)
            # Las páginas que se visitan (Todocolección, Wallapop) traen
            # anuncios de Google, y sus scripts vuelcan decenas de
            # avisos por consulta —«[GPT] … is deprecated», «AdSense
            # head tag…», «FedCM get() rejects»— que Qt reenvía al log
            # de la aplicación como «js: …». No son fallos nuestros ni
            # afectan al precio: son de la web ajena. Se silencian aquí,
            # que si no tapan el registro de verdad (2026-08-08).
            self._view.setPage(_PaginaSinRuido(self._view))
            self._view.loadFinished.connect(self._on_loaded)
            # Sin imágenes la página carga mucho antes; el precio es texto
            from PySide6.QtWebEngineCore import QWebEngineSettings

            self._view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.AutoLoadImages, False
            )
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._current: Optional[tuple[str, object, bool]] = None
        # Nº de secuencia de la petición actual: QtWebEngine dispara
        # loadFinished VARIAS veces por página (redirecciones, JS) y sin
        # esto se capturaba HTML duplicado o cruzado entre peticiones en
        # cola → puntos repetidos en la gráfica (bug 2026-07-26).
        self._seq = 0
        self._settle_scheduled = False
        self._poll_left_ms = 0

    def fetch(self, url: str, callback, want_html: bool = False) -> None:
        """
        Encola la consulta; las peticiones se sirven de una en una.

        Con `want_html=True` el callback recibe `(url, precio, html)` —
        el HTML renderizado, para reconocer el CONTENIDO del anuncio
        (p. ej. qué tomos trae un lote) además del precio.
        """
        if not _WEBENGINE_OK:
            # Respaldo sin Chromium: urllib (sitios sin bloqueo anti-bot)
            from app.utils import fetch_listing_price

            precio = fetch_listing_price(url)
            callback(url, precio, "") if want_html else callback(url, precio)
            return
        self._queue.append((url, callback, want_html))
        self._next()

    def _next(self) -> None:
        if self._busy or not self._queue:
            return
        self._busy = True
        self._seq += 1
        self._settle_scheduled = False
        self._current = self._queue.pop(0)
        from PySide6.QtCore import QUrl

        self._timeout.start(self._TIMEOUT_MS)
        self._view.load(QUrl(self._current[0]))

    def _on_loaded(self, ok: bool) -> None:
        # Ignorar disparos extra de loadFinished para la misma petición
        if self._current is None or self._settle_scheduled:
            return
        if not ok:
            self._finish(None)
            return
        self._settle_scheduled = True
        # Captura INMEDIATA: la mayoría de sitios (TC, AbeBooks…) traen
        # el precio en el HTML servido; solo se reintenta si no aparece.
        self._poll_left_ms = self._POLL_BUDGET_MS
        self._grab(self._seq)

    def _grab(self, seq: int) -> None:
        if seq != self._seq or self._current is None:
            return  # llegó tarde: ya es otra petición
        self._view.page().toHtml(
            lambda html, seq=seq: self._on_html(html, seq)
        )

    def _on_html(self, html: str, seq: int) -> None:
        if seq != self._seq or self._current is None:
            return
        from app.utils import extract_price_from_listing_html

        price = extract_price_from_listing_html(html)
        if price is None and self._poll_left_ms > 0:
            # Aún sin precio: la página lo pinta por JS → reintento corto
            self._poll_left_ms -= self._POLL_MS
            QTimer.singleShot(
                self._POLL_MS,
                lambda seq=seq: self._grab(seq),
            )
            return
        self._finish(price, html)

    def _on_timeout(self) -> None:
        logger.warning("Consulta de publicación agotada: %s",
                       self._current[0] if self._current else "?")
        if _WEBENGINE_OK:
            self._view.stop()
        self._finish(None)

    def _finish(self, price: Optional[float], html: str = "") -> None:
        self._timeout.stop()
        current, self._current = self._current, None
        self._busy = False
        self._settle_scheduled = False
        if current is not None:
            url, callback, want_html = current
            try:
                callback(url, price, html) if want_html else callback(url, price)
            except RuntimeError:
                pass  # el diálogo ya no existe
        self._next()


_PRICE_FETCHER: Optional[ListingPriceFetcher] = None


def shared_price_fetcher() -> ListingPriceFetcher:
    """Fetcher único de la aplicación (requiere QApplication creada)."""
    global _PRICE_FETCHER
    if _PRICE_FETCHER is None:
        _PRICE_FETCHER = ListingPriceFetcher()
    return _PRICE_FETCHER


# ----------------------------------------------------------------------
# Ficha de un tomo: datos + gráfica + alertas + búsquedas externas
# ----------------------------------------------------------------------
class TomoDialog(FramelessDialog):
    """
    Ficha completa de un tomo (doble clic en la Colección): datos del
    Excel, etiquetas, rareza, gráfica de precios de ESE tomo, últimas
    alertas suyas y accesos a búsquedas externas.
    """

    def __init__(
        self, db: Database, clave, parent: Optional[QWidget] = None
    ) -> None:
        from app import collection as col
        from urllib.parse import quote_plus

        rows = db.get_tomos()
        # Por NÚMERO ("415.2" es Ovidio y "415[27]" Estrabón); por orden
        # solo como respaldo, que lo comparten tres pares de tomos.
        row = next((r for r in rows if str(r["numero"]) == str(clave)), None)
        if row is None:
            row = next((r for r in rows if r["orden"] == clave), None)
        orden = row["orden"] if row is not None else clave
        titulo_v = f"Tomo BCG nº {row['numero'] if row is not None else clave}"
        super().__init__(titulo_v, parent)
        # OJO: alto suficiente para TODO el contenido — si el layout
        # desborda, los widgets inferiores se superponen a la gráfica y
        # le roban los clics (bug 2026-07-26). Si se añade contenido,
        # compensar aquí.
        self.resize(780, 700)
        self.setMinimumSize(680, 660)
        if row is None:
            self.body.addWidget(QLabel("Tomo no encontrado."))
            return

        # Lista COMPLETA anotada: el sufijo de volumen (títulos
        # duplicados) debe coincidir con el que usa el monitor.
        todos = col.annotate_ambiguous([
            col.Tomo(
                numero=r["numero"], orden=r["orden"], autor=r["autor"],
                obras=r["obras"], paginas=r["paginas"],
                notas=r["notas"] or "", poseido=bool(r["poseido"]),
                deseado=bool(r["deseado"]),
                precio_objetivo=r["precio_objetivo"],
            )
            for r in rows
        ])
        tomo = next(t for t in todos if t.orden == orden)

        # --- Cabecera compacta (la ficha es densa) ------------------------
        self.body.setSpacing(6)
        lbl_autor = QLabel(tomo.autor.upper())
        lbl_autor.setFont(fuente(12, negrita=True, espaciado=1.6))
        lbl_autor.setObjectName("titulo")
        lbl_autor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _gold_glow(lbl_autor, blur=16, alpha=120)
        self.body.addWidget(lbl_autor)

        lbl_obras = QLabel(tomo.obras)
        lbl_obras.setFont(fuente(10, cursiva=True))
        lbl_obras.setObjectName("subtitulo")
        lbl_obras.setWordWrap(True)
        lbl_obras.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(lbl_obras)
        self.body.addWidget(_filete(1))

        # --- Datos + estado ------------------------------------------------
        datos = []
        datos.append(f"Número: {tomo.numero}")
        if tomo.paginas:
            datos.append(f"{tomo.paginas} páginas")
        if tomo.poseido:
            datos.append("ya obtenido ✔")
        elif tomo.deseado:
            datos.append("⭐ deseado")
        else:
            datos.append("te falta")
        if col.is_rare(tomo):
            datos.append("💎 RARO (rango 360-415)")
        elif col.is_appendix(tomo):
            datos.append("apéndice: no pertenece propiamente a la colección")
        lbl_datos = QLabel("   ·   ".join(datos))
        lbl_datos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(lbl_datos)
        if tomo.notas:
            lbl_notas = QLabel(tomo.notas)
            lbl_notas.setObjectName("mensaje")
            lbl_notas.setWordWrap(True)
            lbl_notas.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.body.addWidget(lbl_notas)

        self._db = db
        self._orden = orden
        self._numero = row["numero"] if row is not None else clave

        # --- De qué trata (descripción generada con IA) --------------------
        fila = next((r for r in rows if r["orden"] == orden), None)
        descripcion = (fila["descripcion"] or "") if fila is not None else ""
        temas = (fila["temas"] or "") if fila is not None else ""
        if descripcion:
            self.lbl_desc = QLabel(descripcion)
            self.lbl_desc.setWordWrap(True)
            self.lbl_desc.setToolTip(
                "Descripción generada con IA a partir de los datos de la "
                "colección: puede contener errores."
            )
            self.body.addWidget(self.lbl_desc)
            if temas:
                lbl_temas = QLabel(temas)
                lbl_temas.setObjectName("mensaje")
                lbl_temas.setWordWrap(True)
                lbl_temas.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.body.addWidget(lbl_temas)
            self.body.addWidget(_filete(1))

        # Precio objetivo: se edita AQUÍ (la columna de la Colección se
        # retiró a petición del usuario, 2026-07-26)
        row_obj = QHBoxLayout()
        row_obj.addStretch(1)
        row_obj.addWidget(QLabel("Precio objetivo:"))
        self.ed_target = GlowLineEdit()
        self.ed_target.setFixedWidth(90)
        self.ed_target.setPlaceholderText("sin objetivo")
        self.ed_target.setToolTip(
            "Avisar si este tomo baja de este precio (€), ignore o no el "
            "% de descuento. Vacío = sin objetivo."
        )
        if tomo.precio_objetivo is not None:
            self.ed_target.setText(f"{tomo.precio_objetivo:g}")
        row_obj.addWidget(self.ed_target)
        btn_target = GlowButton("Guardar")
        btn_target.clicked.connect(self._save_target)
        row_obj.addWidget(btn_target)
        self.lbl_target_info = QLabel("")
        self.lbl_target_info.setObjectName("mensaje")
        row_obj.addWidget(self.lbl_target_info)
        row_obj.addStretch(1)
        self.body.addLayout(row_obj)

        # --- Publicaciones vigiladas: enlaces cuyo precio extrae la app --
        self._canonical = tomo.canonical_title()
        self._clave = Database._title_key(self._canonical)

        lbl_links = QLabel("Publicaciones vigiladas")
        lbl_links.setStyleSheet(f"color: {ORO};")
        self.body.addWidget(lbl_links)

        self.links_table = GlowTable(0, 2)
        self.links_table.setHorizontalHeader(
            GlowHeader(self.links_table, compact=True)
        )
        self.links_table.setHorizontalHeaderLabels(("Enlace", "Precio"))
        self.links_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.links_table.setShowGrid(False)
        self.links_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.links_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.links_table.verticalHeader().setVisible(False)
        self.links_table.verticalHeader().setDefaultSectionSize(30)
        self.links_table.setWordWrap(False)
        self.links_table.setFixedHeight(124)  # cabecera compacta: más filas
        hdr = self.links_table.horizontalHeader()
        hdr.setStretchLastSection(False)
        w_precio = max(90, GlowHeader.required_width("Precio"))
        hdr.setSectionResizeMode(1, hdr.ResizeMode.Fixed)
        self.links_table.setColumnWidth(1, w_precio)
        hdr.setSectionResizeMode(0, hdr.ResizeMode.Stretch)
        self._fetcher = shared_price_fetcher()
        self.links_table.cellDoubleClicked.connect(self._open_link_row)
        self.body.addWidget(self.links_table)

        row_links = QHBoxLayout()
        self.ed_link = GlowLineEdit()
        self.ed_link.setPlaceholderText("https://… (anuncio de este tomo)")
        row_links.addWidget(self.ed_link, 1)
        btn_add = GlowButton("Añadir")
        btn_add.clicked.connect(self._add_link)
        row_links.addWidget(btn_add)
        btn_del = GlowButton("Quitar")
        btn_del.clicked.connect(self._remove_link)
        row_links.addWidget(btn_del)
        btn_refresh = GlowButton("Actualizar precios")
        btn_refresh.clicked.connect(self._refresh_links)
        row_links.addWidget(btn_refresh)
        self.body.addLayout(row_links)

        self.lbl_links_info = QLabel(
            "Doble clic abre la publicación · los precios extraídos "
            "alimentan la gráfica."
        )
        self.lbl_links_info.setObjectName("mensaje")
        self.body.addWidget(self.lbl_links_info)

        # --- Gráfica de precios de ESTE tomo ------------------------------
        self.chart = PriceChart()
        self.chart.setMinimumSize(380, 185)  # compacta: la ficha es densa
        self.chart.set_points(db.price_history_for(self._clave))
        self.body.addWidget(self.chart, 1)
        self._reload_links()

        # --- Últimas alertas del tomo --------------------------------------
        alerts = [
            r for r in db.get_history(limit=500)
            if r["titulo"] == self._canonical
        ][:3]
        if alerts:
            lines = []
            for r in alerts:
                fecha = str(r["fecha"])[:16].replace("T", " ")
                dto = (
                    f"{r['descuento']:.0f} %" if r["descuento"] is not None else "—"
                )
                lines.append(
                    f"{fecha} · {format_price(r['precio_new'])} · {dto} · {r['estado']}"
                )
            lbl_alerts = QLabel("Últimas alertas:\n" + "\n".join(lines))
        else:
            lbl_alerts = QLabel("Sin alertas registradas de este tomo todavía.")
        lbl_alerts.setObjectName("mensaje")
        self.body.addWidget(lbl_alerts)

        # --- Búsquedas externas + cerrar -----------------------------------
        # Autor colectivo o anónimo ("VVAA", "VVAA (sofistas)",
        # "Anónimo"…) NO va a la consulta: solo mete ruido en los
        # buscadores. Regla ÚNICA en collection.author_for_search.
        autor_util = col.author_for_search(tomo.autor)
        # SIN "Gredos" al final: estrechaba las búsquedas y ocultaba
        # resultados legítimos (petición del usuario, 2026-07-26).
        consulta = quote_plus(" ".join(
            parte for parte in (autor_util, tomo.obras) if parte
        ))
        buttons = QHBoxLayout()
        self._search_urls = (
            # Formato real del buscador de TC: /buscador?bu=...
            ("Todocolección",
             f"https://www.todocoleccion.net/buscador?bu={consulta}"),
            ("Iberlibro",
             f"https://www.iberlibro.com/servlet/SearchResults?kn={consulta}"),
            ("AbeBooks",
             f"https://www.abebooks.com/servlet/SearchResults?kn={consulta}"),
            # Wallapop España: la web SOLO aplica los filtros de la URL
            # si llevan `filters_source=quick_filters` (sin él ignora
            # lat/long y muestra "Sin ubicación"). Ancla en el centro de
            # España + radio de 10 000 km (= toda España, Canarias
            # incluidas) + orden por relevancia, no por cercanía.
            ("Wallapop",
             f"https://es.wallapop.com/search?keywords={consulta}"
             "&latitude=40.4168&longitude=-3.7038&distance=10000000"
             "&filters_source=quick_filters&order_by=most_relevance"),
        )
        for label, url in self._search_urls:
            btn = GlowButton(label)
            btn.setToolTip(f"Buscar este tomo en {label}")
            btn.clicked.connect(lambda _c=False, u=url: webbrowser.open(u))
            buttons.addWidget(btn)
        buttons.addStretch(1)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_close)
        lbl_buscar = QLabel("Buscar en:")
        row_search = QHBoxLayout()
        row_search.addWidget(lbl_buscar)
        row_search.addLayout(buttons)
        self.body.addLayout(row_search)

    # --- publicaciones vigiladas --------------------------------------
    def _reload_links(self) -> None:
        rows = self._db.get_tomo_links(self._numero)
        self.links_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            item_url = QTableWidgetItem(r["url"])
            item_url.setToolTip(r["url"])
            item_url.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.links_table.setItem(i, 0, item_url)
            precio = r["ultimo_precio"]
            vendido = "vendido" in r.keys() and r["vendido"]
            if vendido:
                # Sin precio y dicho con todas las letras: el anuncio
                # sigue en la lista, pero ya no se puede comprar.
                celda = _NumItem("vendido", -1.0)
                celda.setForeground(QColor(ORO_APAGADO))
                celda.setToolTip("Anuncio vendido: se le retiró el precio")
            else:
                celda = _NumItem(
                    format_price(precio) if precio is not None else "—", precio
                )
            self.links_table.setItem(i, 1, celda)

    def _open_link_row(self, row: int, _col: int) -> None:
        item = self.links_table.item(row, 0)
        if item is not None:
            webbrowser.open(item.text())

    def _add_link(self) -> None:
        url = self.ed_link.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.lbl_links_info.setText("La URL debe empezar por http(s)://")
            return
        link_id = self._db.add_tomo_link(self._numero, url)
        self.ed_link.clear()
        self._reload_links()
        self._fetch_and_store(link_id, url)

    def _remove_link(self) -> None:
        fila = self.links_table.currentRow()
        item = self.links_table.item(fila, 0) if fila >= 0 else None
        if item is None:
            self.lbl_links_info.setText("Selecciona primero una publicación.")
            return
        self._db.remove_tomo_link(int(item.data(Qt.ItemDataRole.UserRole)))
        self._reload_links()

    def _refresh_links(self) -> None:
        """Reconsulta el precio de todas las publicaciones del tomo."""
        rows = self._db.get_tomo_links(self._numero)
        if not rows:
            self.lbl_links_info.setText("No hay publicaciones que consultar.")
            return
        for r in rows:
            self._fetch_and_store(r["id"], r["url"], anterior=r["ultimo_precio"])

    def _fetch_and_store(
        self, link_id: int, url: str, anterior: Optional[float] = None
    ) -> None:
        """
        Consulta ASÍNCRONA (Chromium embebido: pasa la protección
        anti-bot de Todocolección y renderiza Wallapop): al llegar el
        precio se guarda y, si CAMBIÓ, se añade el punto a la serie del
        tomo para las gráficas del botón Precios.

        El fetcher es el COMPARTIDO de la aplicación: la BD se actualiza
        aunque el diálogo ya esté cerrado (la UI solo se toca si sigue
        vivo), así se pueden encadenar consultas en varios tomos sin
        esperar en cada ficha.
        """
        self.lbl_links_info.setText(f"Consultando {url[:60]}…")
        db, canonical, clave = self._db, self._canonical, self._clave

        def listo(_url: str, precio: Optional[float], html: str = "") -> None:
            from app.utils import listing_sold

            # VENDIDO: el anuncio conserva su precio en la página, pero
            # ese precio ya no se puede pagar. Se retira de la serie para
            # que no falsee la gráfica (2026-07-31).
            if listing_sold(html):
                db.mark_link_sold("tomo_links", link_id)
                quitados = db.delete_price_points(clave, url)
                self.chart.set_points(db.price_history_for(clave))
                self.lbl_links_info.setText(
                    f"Vendido: {url[:50]} — precio retirado"
                    + (f" ({quitados} punto/s)" if quitados else "")
                )
                self._reload_links()
                return
            if precio is None:
                self.lbl_links_info.setText(
                    f"Sin precio reconocible en {url[:60]}"
                )
                return
            db.update_tomo_link_price(link_id, precio)
            # La serie es ÚNICA (la misma que muestra el botón Precios):
            # solo se añade punto si difiere del ÚLTIMO de la serie —
            # nada de duplicados por re-consultas.
            ultimo = db.last_price(canonical)
            if ultimo is None or abs(precio - ultimo) >= 0.01:
                db.add_price_point(canonical, precio, url=url)
                self.chart.set_points(db.price_history_for(clave))
            self.lbl_links_info.setText(
                f"Precio extraído: {format_price(precio)} ✓"
            )
            self._reload_links()

        # Con el HTML: hace falta para saber si el anuncio está vendido
        self._fetcher.fetch(url, listo, want_html=True)

    def _save_target(self) -> None:
        """Guarda (o borra si está vacío) el precio objetivo del tomo."""
        raw = (
            self.ed_target.text().strip()
            .replace("€", "").replace(",", ".").strip()
        )
        if raw in ("", "—", "-"):
            self._db.set_tomo_target(self._numero, None)
            self.lbl_target_info.setText("Sin objetivo.")
            return
        try:
            precio = round(float(raw), 2)
        except ValueError:
            self.lbl_target_info.setText(f"Valor no válido: {raw!r}")
            return
        self._db.set_tomo_target(self._numero, precio)
        self.lbl_target_info.setText(f"Guardado: {format_price(precio)} ✓")


# ----------------------------------------------------------------------
# Textos de los tomos: analizar el PDF de cada uno (sin guardarlo)
# ----------------------------------------------------------------------
CANCELADO = "__cancelado__"      # marca de "lo paró el usuario"

# Hilos que no pararon a tiempo al cerrar su ventana. Se guardan aquí
# para que Python no los recoja mientras siguen corriendo (destruir un
# QThread vivo tumba la aplicación).
_HILOS_SUELTOS: list = []


class _AnalizarWorker(QThread):
    """Analiza un PDF en su propio hilo (tarda segundos por tomo)."""

    listo = Signal(object, str)          # Analisis | None, error
    progreso = Signal(str, int, int)     # fase, hechas, total (0 = sin medir)

    def __init__(self, ruta, tomos: list, tomo, parent=None) -> None:
        super().__init__(parent)
        self._ruta = ruta
        self._tomos = tomos
        self._tomo = tomo
        self._cancelar = False

    def cancelar(self) -> None:
        self._cancelar = True

    def run(self) -> None:  # noqa: D102 - punto de entrada del QThread
        from app import pdftext

        try:
            res = pdftext.analizar(
                self._ruta, self._tomos, self._tomo,
                progreso=lambda f, h, t: self.progreso.emit(f, h, t),
                cancelado=lambda: self._cancelar,
            )
        except pdftext.AnalisisCancelado:
            self.listo.emit(None, CANCELADO)
            return
        except Exception as exc:  # noqa: BLE001 - un PDF roto no tumba la app
            self.listo.emit(None, str(exc))
            return
        self.listo.emit(res, "")


class _OcrWorker(QThread):
    """Reconoce SOLO las páginas sin texto, en su propio hilo."""

    progreso = Signal(int, int, int)     # hechas, total, nº de página
    terminado = Signal(object, str)      # Analisis | None, error

    def __init__(self, ruta, analisis, ruta_tesseract: str,
                 parent=None) -> None:
        super().__init__(parent)
        self._ruta = ruta
        self._analisis = analisis
        self._tesseract = ruta_tesseract
        self._cancelar = False

    def cancelar(self) -> None:
        self._cancelar = True

    def run(self) -> None:  # noqa: D102 - punto de entrada del QThread
        from app import pdftext

        try:
            res = pdftext.completar_con_ocr(
                self._ruta, self._analisis,
                ruta_tesseract=self._tesseract,
                progreso=lambda h, t, p: self.progreso.emit(h, t, p),
                cancelado=lambda: self._cancelar,
            )
        except Exception as exc:  # noqa: BLE001 - nunca tumbar el hilo
            self.terminado.emit(None, str(exc))
            return
        self.terminado.emit(res, "")


class TextosDialog(FramelessDialog):
    """
    Seguimiento del contenido de la colección: qué tomos tienen ya su
    texto extraído y cuáles faltan.

    Doble clic en un tomo → eliges su PDF descargado. El PDF **no se
    guarda ni se copia**: se analiza donde esté y solo se conserva el
    texto extraído, que pesa unas diez veces menos; así puedes borrar el
    PDF en cuanto el análisis salga bien. Si el análisis encuentra
    pegas, se te cuentan una a una y decides: guardarlo así o probar con
    otra copia.
    """

    HEADERS = ("Nº", "Autor(es)", "Título", "Texto", "Páginas", "Palabras",
               "Calidad")
    _COL_CHECK = 3

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__("Textos de los tomos", parent)
        self.db = db
        self.resize(1000, 620)
        self.setMinimumSize(820, 460)
        from app import collection as col

        self._worker: Optional[_AnalizarWorker] = None
        self._tomos = (
            col.tomos_from_rows(db.get_tomos()) if db.tomos_count()
            else col.load_excel()
        )

        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_count = QLabel("")
        top.addWidget(self.lbl_count)
        top.addStretch(1)
        top.addWidget(QLabel("Buscar:"))
        self.ed_search = GlowLineEdit()
        self.ed_search.setPlaceholderText("autor, obra, número…")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setFixedWidth(220)
        self.ed_search.textChanged.connect(self._apply_search)
        top.addWidget(self.ed_search)
        btn_add = GlowButton("Analizar PDF…")
        btn_add.setToolTip(
            "Analiza el PDF del tomo seleccionado.\n"
            "El PDF no se copia a ninguna parte: solo se guarda su texto."
        )
        btn_add.clicked.connect(self._analizar_seleccionado)
        top.addWidget(btn_add)
        btn_check = GlowButton("Revisar")
        btn_check.setToolTip(
            "Repasa los textos ya guardados: rehace lo que el análisis\n"
            "ha aprendido después (índices de nombres, secciones) y dice\n"
            "cuáles conviene volver a analizar. No hace falta el PDF."
        )
        btn_check.clicked.connect(self._revisar)
        top.addWidget(btn_check)
        self.body.addLayout(top)

        self.table = GlowTable(0, len(self.HEADERS))
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setWordWrap(False)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for columna, base in ((0, 62), (1, 180), (3, 70), (4, 84),
                              (5, 92), (6, 104)):
            ancho = max(base, GlowHeader.required_width(self.HEADERS[columna]))
            header.setSectionResizeMode(columna, header.ResizeMode.Fixed)
            self.table.setColumnWidth(columna, ancho)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        self.body.addWidget(self.table, 1)

        self.progress = GlowProgress()
        self.progress.hide()          # solo mientras se analiza un PDF
        self.body.addWidget(self.progress)

        self.lbl_status = QLabel(
            "Doble clic en un tomo para analizar su PDF. El PDF no se "
            "guarda: solo su texto."
        )
        self.lbl_status.setObjectName("mensaje")
        self.lbl_status.setWordWrap(True)
        self.body.addWidget(self.lbl_status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_close)
        self.body.addLayout(buttons)

        self._populate()

    # --- lista ---------------------------------------------------------
    def _populate(self) -> None:
        from app import pdftext

        hecho = pdftext.estado_de_los_tomos()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._tomos))
        for i, tomo in enumerate(self._tomos):
            # Por TÍTULO CANÓNICO, que es único: tres pares de tomos
            # comparten el número de orden y el ✔ de uno aparecía
            # también en el otro, con sus páginas y sus palabras
            # (Estrabón/Ovidio, 2026-07-29).
            cab = pdftext.estado_del_tomo(hecho, tomo)
            self.table.setItem(
                i, 0, _NumItem(tomo.numero, tomo.orden or 0)
            )
            self.table.setItem(i, 1, QTableWidgetItem(tomo.autor))
            titulo = QTableWidgetItem(tomo.canonical_title())
            titulo.setToolTip(tomo.canonical_title())
            titulo.setData(Qt.ItemDataRole.UserRole, i)
            self.table.setItem(i, 2, titulo)

            marca = _NumItem("✔" if cab else "—", 1 if cab else 0)
            marca.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not cab:
                marca.setForeground(QColor(ORO_APAGADO))
            self.table.setItem(i, self._COL_CHECK, marca)
            # Páginas: las que llevan TEXTO. En las ediciones digitales
            # las notas van a hoja por nota y el total del PDF (1.436
            # en Aristófanes II, un tomo de 528) no dice nada.
            hojas = (cab.get("hojas_texto") or cab["paginas_pdf"]) if cab else 0
            celda = _NumItem(str(hojas) if cab else "", hojas)
            if cab and hojas != cab["paginas_pdf"]:
                celda.setToolTip(
                    f"{hojas} hojas de texto de {cab['paginas_pdf']} del PDF; "
                    f"las otras {cab['paginas_pdf'] - hojas} son notas "
                    "finales o índices"
                )
            self.table.setItem(i, 4, celda)
            palabras = cab.get("palabras", 0) if cab else 0
            self.table.setItem(
                i, 5, _NumItem(f"{palabras:,}".replace(",", ".") if cab else "",
                               palabras)
            )
            calidad = QTableWidgetItem(
                (cab["estado"].replace("_", " ") if cab else "")
            )
            if cab and cab["estado"] != "nativo":
                calidad.setForeground(QColor(ORO_VIEJO))
            if cab and cab.get("dificultades"):
                calidad.setToolTip("\n".join(
                    f"· {d}" for d in cab["dificultades"]
                ))
            self.table.setItem(i, 6, calidad)
        self.table.setSortingEnabled(True)
        # Del tomo 1 al 423: sin fijarlo, la tabla hereda el indicador
        # descendente de la cabecera y la lista sale del revés.
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self._total = len(self._tomos)
        self._hechos = len(hecho)
        self._apply_search(self.ed_search.text())

    def _apply_search(self, text: str) -> None:
        query = normalize(text.strip())
        visibles = 0
        for i in range(self.table.rowCount()):
            fila = " ".join(
                self.table.item(i, c).text() for c in (0, 1, 2)
                if self.table.item(i, c)
            )
            visible = not query or query in normalize(fila)
            self.table.setRowHidden(i, not visible)
            visibles += visible
        pendientes = self._total - self._hechos
        self.lbl_count.setText(
            f"{self._hechos} de {self._total} tomos con texto · "
            f"faltan {pendientes}"
            + (f" · {visibles} visibles" if query else "")
        )

    # --- repasar lo ya guardado -----------------------------------------
    def _revisar(self) -> None:
        """
        Repasa los textos guardados y arregla lo que se pueda sin el PDF.

        El analizador mejora con cada tomo raro que aparece, y los
        textos viejos se quedan con los defectos de su día; casi todo se
        puede rehacer del propio texto guardado.
        """
        from app import pdftext

        self.progress.arrancar("Repasando los textos guardados")
        QApplication.processEvents()
        try:
            informes = pdftext.revisar_textos(reparar=True)
        finally:
            self.progress.parar()
        arreglados = [i for i in informes if i["reparado"]]
        pendientes = [
            i for i in informes
            if any(("equivocado" in a or "poco texto" in a or "ilegible" in a)
                   for a in i["avisos"])
        ]
        self._populate()

        lineas = [f"Textos revisados: {len(informes)}"]
        if arreglados:
            lineas.append(f"\nArreglados ({len(arreglados)}):")
            lineas += [
                f"·  {i['canonico'][:44] or i['archivo'][:44]}: "
                f"{' · '.join(i['reparado'])}"
                for i in arreglados[:12]
            ]
            if len(arreglados) > 12:
                lineas.append(f"…y {len(arreglados) - 12} más")
        if pendientes:
            lineas.append(
                f"\nConviene volver a analizar el PDF ({len(pendientes)}):"
            )
            lineas += [
                f"·  {i['canonico'][:44] or i['archivo'][:44]}"
                for i in pendientes[:10]
            ]
        if not arreglados and not pendientes:
            lineas.append("\nTodo en orden: no hay nada que rehacer.")
        GredosMessageBox.show_info(self, "Revisión de los textos",
                                   "\n".join(lineas))
        self.lbl_status.setText(
            f"Revisión: {len(arreglados)} textos arreglados, "
            f"{len(pendientes)} conviene rehacerlos."
        )

    # --- analizar un PDF ------------------------------------------------
    def _on_double_click(self, fila: int, _col: int) -> None:
        self._analizar(fila)

    def _analizar_seleccionado(self) -> None:
        fila = self.table.currentRow()
        if fila < 0:
            self.lbl_status.setText("Selecciona antes un tomo de la lista.")
            return
        self._analizar(fila)

    def _tomo_de_fila(self, fila: int):
        """El tomo de esa fila, por su POSICIÓN en la lista.

        Ni por orden ni por título: el orden lo comparten tres pares de
        tomos y por ahí se abría la ficha del otro (2026-07-29).
        """
        item = self.table.item(fila, 2)
        if item is None:
            return None
        indice = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(indice, int) or not 0 <= indice < len(self._tomos):
            return None
        return self._tomos[indice]

    def _analizar(self, fila: int) -> None:
        from PySide6.QtWidgets import QFileDialog

        if self._worker is not None:
            self.lbl_status.setText("Espera: hay un análisis en marcha.")
            return
        tomo = self._tomo_de_fila(fila)
        if tomo is None:
            return
        ruta, _filtro = QFileDialog.getOpenFileName(
            self, f"PDF de: {tomo.canonical_title()[:60]}", "",
            "Documentos PDF (*.pdf)",
        )
        if not ruta:
            return
        self._tomo_actual = tomo
        self._fila_actual = fila
        self._ruta_actual = ruta
        self.lbl_status.setText(
            f"Analizando {Path(ruta).name[:60]}… (el PDF no se copia)"
        )
        self.progress.arrancar("Abriendo el PDF")
        self._worker = _AnalizarWorker(ruta, self._tomos, tomo, self)
        self._worker.progreso.connect(self.progress.avanzar)
        self._worker.listo.connect(self._analisis_listo)
        self._worker.start()

    def _analisis_listo(self, res, error: str) -> None:
        from app import pdftext

        self._worker = None
        self.progress.parar()
        tomo = self._tomo_actual
        if error == CANCELADO:
            self.lbl_status.setText("Análisis cancelado.")
            return
        if error or res is None:
            GredosMessageBox.show_info(
                self, "No se pudo leer el PDF",
                f"El archivo no se ha podido analizar:\n\n{error}",
            )
            self.lbl_status.setText("Análisis fallido.")
            return

        titulo = tomo.canonical_title()
        if not res.utilizable:
            # Sin texto no hay nada que guardar: solo cabe otra copia
            otra = GredosMessageBox.ask(
                self, "Este PDF no sirve",
                f"{titulo}\n\n{res.resumen()}",
                accept_text="Probar con otro PDF", cancel_text="Dejarlo",
            )
            self.lbl_status.setText(f"{titulo}: PDF sin texto, no guardado.")
            if otra:
                self._analizar(self._fila_actual)
            return

        # ¿Le faltan páginas sueltas? Se pueden rescatar sin reconocer
        # el tomo entero (en el Plutarco, dos de las diecisiete
        # ilegibles eran justo las de su índice de nombres). Se mira
        # `ocr_intentado`, NO las páginas rescatadas: un reconocimiento
        # que no saca nada volvía a ofrecerse en bucle (2026-07-28).
        if res.completable_con_ocr and not res.ocr_intentado:
            salida = self._ofrecer_ocr(res)
            if salida == "ocr":
                return                      # sigue al terminar el OCR
            if salida == "abortar":
                self.lbl_status.setText(
                    f"{titulo}: análisis cancelado, no se ha guardado nada."
                )
                return

        if res.dificultades:
            pegas = "\n".join(f"·  {d}" for d in res.dificultades)
            salida = GredosMessageBox.ask_ex(
                self, "Analizado, con reparos",
                f"{titulo}\n\n{res.resumen()}\n\n"
                f"Dificultades encontradas:\n{pegas}\n\n"
                "Puedes guardarlo así o probar con otra copia del PDF.",
                accept_text="Guardar así", cancel_text="Probar con otro PDF",
            )
            if salida == "cancelar":
                self._analizar(self._fila_actual)
                return
            if salida != "aceptar":         # ✕ o Esc: dejarlo estar
                self.lbl_status.setText(
                    f"{titulo}: análisis descartado, no se ha guardado nada."
                )
                return
        else:
            GredosMessageBox.show_info(
                self, "Analizado sin problemas",
                f"{titulo}\n\n{res.resumen()}\n\n"
                "Todo correcto: ya puedes borrar el PDF si quieres.",
            )

        destino = pdftext.guardar(res, tomo)
        self._populate()
        self.lbl_status.setText(
            f"✔ {titulo} · {res.palabras:,} palabras guardadas en "
            f"{destino.name}".replace(",", ".")
            + "  ·  el PDF no se ha copiado: puedes borrarlo"
        )

    # --- rescatar las páginas sin texto con OCR -------------------------
    def _ofrecer_ocr(self, res) -> str:
        """
        Propone reconocer las páginas que no traen texto.

        Devuelve `"ocr"` si el reconocimiento arranca (el flujo sigue en
        `_ocr_terminado`), `"seguir"` para continuar sin esas páginas y
        `"abortar"` si el usuario cerró el aviso con la ✕.
        """
        from app.config import Config
        from app import pdftext

        faltan = len(res.paginas_sin_texto)
        config = Config.load()
        disponible, detalle = pdftext.ocr_disponible(config.tesseract_path)
        if not disponible:
            GredosMessageBox.show_info(
                self, f"Faltan {faltan} páginas por reconocer",
                f"A este PDF le faltan {faltan} páginas de "
                f"{res.paginas_pdf} ({res.cobertura:.0f} % reconocido).\n\n"
                f"{detalle}",
            )
            return "seguir"
        # Tesseract se instala por defecto SOLO con inglés: avisarlo
        # antes evita el "le doy y no hace nada" (2026-07-28).
        idiomas = pdftext.idiomas_ocr()
        aviso_idioma = (
            "\n\nAviso: Tesseract no tiene instalado el español, así que "
            "se reconocerá en inglés y las tildes pueden salir mal. Para "
            "arreglarlo, ejecuta otra vez su instalador y marca «Spanish»."
            if idiomas and "spa" not in idiomas else ""
        )
        salida = GredosMessageBox.ask_ex(
            self, f"Faltan {faltan} páginas por reconocer",
            f"El PDF está reconocido al {res.cobertura:.0f} %, pero "
            f"{faltan} páginas son solo imagen y su contenido se pierde.\n\n"
            "Puedo reconocerlas ahora (tarda unos segundos por página) y "
            "añadirlas al texto. El PDF no se modifica." + aviso_idioma,
            accept_text=f"Reconocer {faltan} páginas",
            cancel_text="Seguir sin ellas",
        )
        if salida != "aceptar":
            # La ✕ corta el análisis entero; "Seguir sin ellas" continúa
            res.ocr_intentado = True        # ni se vuelve a preguntar
            return "seguir" if salida == "cancelar" else "abortar"

        self._analisis_pendiente = res
        self.lbl_status.setText(f"Reconociendo {faltan} páginas…")
        self.progress.arrancar("Reconociendo páginas", faltan)
        self._worker = _OcrWorker(
            self._ruta_actual, res, config.tesseract_path, self
        )
        self._worker.progreso.connect(self._ocr_progreso)
        self._worker.terminado.connect(self._ocr_terminado)
        self._worker.start()
        return "ocr"

    def _ocr_progreso(self, hechas: int, total: int, pagina: int) -> None:
        self.progress.avanzar(f"Reconociendo la página {pagina}", hechas, total)
        self.lbl_status.setText(f"Reconociendo página {pagina} … [{hechas}/{total}]")

    def _ocr_terminado(self, res, error: str) -> None:
        self._worker = None
        self.progress.parar()
        if error or res is None:
            GredosMessageBox.show_info(
                self, "No se pudo reconocer",
                f"El reconocimiento no se completó:\n\n{error}",
            )
            res = self._analisis_pendiente
            res.ocr_intentado = True        # no volver a ofrecer lo mismo
        elif not res.paginas_ocr:
            # Reconoció, pero no salió nada: hay que DECIR por qué, o
            # parece que el botón no hace nada (2026-07-28).
            GredosMessageBox.show_info(
                self, "No se ha rescatado ninguna página",
                res.ocr_fallo or
                "El reconocimiento terminó sin sacar texto de esas páginas.",
            )
        self._analisis_listo(res, "")

    # --- cerrar la ventana corta lo que esté en marcha ------------------
    def _cortar_trabajo(self) -> None:
        """Un análisis de 600 páginas no debe seguir tras cerrar."""
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker.cancelar()
        if worker.wait(3000):
            return
        # No paró (una página enorme de OCR puede tardar): se le suelta
        # del padre y se guarda aparte hasta que termine solo.
        worker.setParent(None)
        _HILOS_SUELTOS.append(worker)
        worker.finished.connect(
            lambda w=worker: w in _HILOS_SUELTOS and _HILOS_SUELTOS.remove(w)
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._cortar_trabajo()
        super().closeEvent(event)

    def reject(self) -> None:  # noqa: D102 - API Qt (Esc y botón Cerrar)
        self._cortar_trabajo()
        super().reject()

    def accept(self) -> None:  # noqa: D102 - API Qt
        self._cortar_trabajo()
        super().accept()


# ----------------------------------------------------------------------
# Buscador dentro del TEXTO de los tomos (RAG-1/2/3)
# ----------------------------------------------------------------------
class _IndexarWorker(QThread):
    """
    Mete en el índice los textos nuevos, en su propio hilo.

    Los 172 tomos tardan unos 40 s la primera vez; en la interfaz eso
    sería la ventana congelada. Después es incremental y va en segundos.
    """

    progreso = Signal(str, int, int)      # fase, hechas, total
    listo = Signal(object, str)           # Progreso | None, error

    def __init__(self, indice, forzar: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._indice = indice
        self._forzar = forzar
        self._cancelar = False

    def cancelar(self) -> None:
        self._cancelar = True

    def run(self) -> None:  # noqa: D102 - punto de entrada del QThread
        try:
            res = self._indice.indexar(
                progreso=lambda f, h, t: self.progreso.emit(f, h, t),
                cancelado=lambda: self._cancelar,
                forzar=self._forzar,
            )
        except Exception as exc:  # noqa: BLE001 - nunca tumbar el hilo
            self.listo.emit(None, str(exc))
            return
        self.listo.emit(res, "")


# ----------------------------------------------------------------------
# Composición de una página del tomo
# ----------------------------------------------------------------------
# La lógica vive en `app/formato.py`, que sabe cómo está compuesto un
# tomo de la Biblioteca Clásica Gredos (margen, cuerpo, notas al pie).
# Aquí solo se le da nombre local y se pinta lo que devuelve: si se
# duplicara, la interfaz y la revisión de textos acabarían componiendo
# la misma página de dos maneras distintas.
from app.formato import (                                    # noqa: E402
    acaba_en_frase as _acaba_en_frase,
    acaba_en_parrafo as _acaba_en_parrafo,
    empieza_en_frase as _empieza_en_frase,
)
from app import formato as _formato                          # noqa: E402

_CUERPO_PAGINA = 11.5
_CUERPO_MARCA = 11.7
ORO_MARCA = "#ffe9a8"       # oro claro, casi blanco: el punto de luz


def localizacion_de_hoja(hoja: dict) -> str:
    """Dónde está esto en el libro, dicho para poder citarlo."""
    partes = []
    if hoja.get("numero"):
        partes.append(f"Tomo {hoja['numero']}")
    if hoja.get("obra"):
        partes.append(hoja["obra"])
    if hoja.get("impresa"):
        partes.append(f"página {hoja['impresa']}")
    elif hoja.get("versos"):
        # La referencia del margen va sola, sin el signo §: es como se
        # cita un clásico y el símbolo quedaba poco profesional.
        partes.append(hoja["versos"].split()[0])
    if hoja.get("hoja"):
        partes.append(f"hoja {hoja['hoja']} del PDF")
    return " · ".join(partes)


def componer_pagina(texto: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Los bloques y las marcas del margen (ver `app.formato`)."""
    pagina = _formato.componer(texto)
    return pagina.bloques, pagina.marcas


def _partir_notas(texto: str) -> list[tuple[str, str]]:
    """Las notas al pie, como `(número, texto)`."""
    return _formato._partir_notas(texto)


def html_de_hoja(
    hoja: dict,
    consulta: str = "",
    desde_frase: bool = False,
    hasta_frase: bool = False,
    hasta_parrafo: bool = False,
    desde_texto: str = "",
) -> str:
    """
    La hoja compuesta para leerla: párrafos, referencias al margen y
    notas al pie, con lo buscado resaltado.

    `desde_frase` y `hasta_frase` recortan el rabo de oración con el
    que empieza o acaba la página, para que el pliego se lea entero
    sin quedarse colgado a mitad de frase. `hasta_parrafo` es más
    exigente: cierra en PUNTO Y APARTE, sin partir ningún párrafo.
    `desde_texto` hace que la página empiece donde empieza ese pasaje.
    """
    from app import rag

    # Georgia no tiene griego POLITÓNICO, así que cada racha griega se
    # envuelve entera en una fuente que sí lo tenga. Sin esto, Qt lo
    # resolvía letra a letra y partía la palabra entre dos tipografías
    # (la ἀ de una, «λλήλων» de otra).
    familia_griega = (
        f"font-family:'{FUENTE_GRIEGA}','"
        + "','".join(FUENTES_GRIEGAS_RESERVA) + "'"
    )

    def escapa(txt: str) -> str:
        return (
            txt.replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;")
        )

    def estilo(marca: bool, griego: bool) -> str:
        """
        El estilo del trozo, con UN SOLO `font-size`.

        Antes se apilaban dos —el del griego y el del realce— y ganaba
        el último: el griego marcado perdía su compensación de tamaño y
        Palatino se salía de la línea, comiéndose los acentos
        (2026-08-08). El cuerpo se calcula una vez y ya.
        """
        cuerpo = _CUERPO_MARCA if marca else _CUERPO_PAGINA
        if griego:
            cuerpo *= _ESCALA_GRIEGA
        partes = [f"font-size:{round(cuerpo, 1)}pt"]
        if griego:
            partes.append(familia_griega)
        if marca:
            partes.append(
                f"color:{ORO_MARCA};font-weight:bold;"
                "background:rgba(212,175,55,0.16)"
            )
        return ";".join(partes)

    def pinta(texto: str) -> str:
        trozos = []
        for fragmento, marca in rag.marcar(texto, consulta):
            for parte, grieg in partir_por_griego(fragmento):
                escapado = escapa(parte).replace(chr(10), "<br>")
                if marca or grieg:
                    trozos.append(
                        f'<span style="{estilo(marca, grieg)}">'
                        f"{escapado}</span>"
                    )
                else:
                    trozos.append(escapado)
        return "".join(trozos)

    bloques, marcas = componer_pagina(hoja.get("cuerpo", ""))
    if desde_texto:
        # La página arranca donde arranca ESE pasaje: una hoja da dos o
        # tres, y enseñándola desde arriba el resumen citaba algo de más
        # abajo y no cuadraba con lo primero que se leía.
        bloques = _formato.desde_el_pasaje(bloques, desde_texto)
    if desde_frase:
        bloques = _empieza_en_frase(bloques)
    if hasta_parrafo:
        bloques = _acaba_en_parrafo(bloques)
    elif hasta_frase:
        bloques = _acaba_en_frase(bloques)
    partes = []
    if marcas:
        # Al margen, como en el libro: son la numeración del editor
        # (Estéfano, la carta, el parágrafo), no texto del autor.
        partes.append(
            f'<div style="color:{ORO_APAGADO};font-size:8.5pt;'
            f'margin-bottom:10px">{escapa(" · ".join(marcas))}</div>'
        )
    for tipo, texto in bloques:
        if tipo == "titulo":
            partes.append(
                f'<p style="color:{ORO_CLARO};font-weight:bold;'
                f'margin:14px 0 8px 0">{pinta(texto)}</p>'
            )
        elif tipo == "verso":
            # El renglón ES el verso: ni se junta ni se justifica.
            partes.append(
                f'<div style="color:{ORO_TEXTO};line-height:150%;'
                f'margin-left:14px">{pinta(texto)}</div>'
            )
        else:
            partes.append(
                # 175 % y no 165: el realce va un punto más alto y con
                # la línea justa Qt le recortaba la tilde por arriba.
                f'<p style="color:{ORO_TEXTO};line-height:175%;'
                f'text-align:justify;margin:0 0 11px 0">{pinta(texto)}</p>'
            )
    notas = _partir_notas(hoja.get("notas", ""))
    if notas:
        pie = [
            f'<div style="color:{ORO_VIEJO};font-size:9pt;line-height:145%">'
            f'<div style="color:{ORO_APAGADO};font-size:8pt;'
            f'letter-spacing:1px;margin-bottom:4px">NOTAS</div>'
        ]
        for numero, texto in notas:
            cabeza = (
                f'<sup style="color:{ORO_TEXTO}">{escapa(numero)}</sup> '
                if numero else ""
            )
            pie.append(
                f'<p style="margin:0 0 5px 0">{cabeza}{pinta(texto)}</p>'
            )
        pie.append("</div>")
        partes.append(
            f'<div style="border-top:1px solid {ORO_APAGADO};'
            f'margin-top:14px;padding-top:8px">{"".join(pie)}</div>'
        )
    return "".join(partes)


class VisorDePagina(QTextEdit):
    """
    La página del tomo, con lo buscado encendido en oro.

    La animación se PINTA ENCIMA, no se mete en el documento: sobre
    cada coincidencia respira un halo dorado y por él cruzan pequeñas
    ESTRELLAS que aparecen y se apagan, como los destellos del pan de
    oro de una encuadernación cuando la mueves a la luz.

    Pintar encima —y no ir cambiando el formato del texto, que fue el
    primer intento— tiene tres ventajas: se pueden dibujar cosas que el
    texto enriquecido no sabe hacer (las estrellas), el documento no se
    toca en ningún momento, y solo se repinta el trocito de cada
    coincidencia, no la página.

    Antes de esto se probaron un foco que recorría las letras y un
    filete subrayado; el usuario descartó los dos (2026-08-08).
    """

    _MS = 40                  # 25 fotogramas por segundo
    _RESPIRO = 3000           # lo que tarda el halo en abrirse y cerrarse
    _VIDA_ESTRELLA = 1700     # lo que vive cada destello, en ms
    _TOPE_MARCAS = 40
    _MARGEN = 9               # holgura para que quepan halo y estrellas
    _RADIO = 2.2              # tamaño del destello (la mitad del primero)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(fuente(_CUERPO_PAGINA))
        self._marcas: list[tuple[int, int]] = []
        self._estrellas: list[dict] = []
        self._fase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self._MS)
        self._timer.timeout.connect(self._latir)

    # --- contenido ------------------------------------------------------
    def mostrar(self, html: str, consulta: str = "") -> None:
        """Pone la página y prepara el destello de lo buscado."""
        from app import rag

        self.setHtml(html)
        self.document().setUndoRedoEnabled(False)
        self._marcas = []
        self._timer.stop()
        if not consulta:
            self.viewport().update()
            return
        # Las posiciones se buscan sobre el texto LLANO del documento,
        # que va carácter a carácter con lo que se ve; el HTML no mete
        # nada visible por medio.
        llano = self.toPlainText()
        plano = rag._plano(llano)
        for palabra in sorted(
            {p for p in rag.normaliza(consulta).split() if len(p) > 2},
            key=len, reverse=True,
        ):
            desde = 0
            while True:
                donde = plano.find(palabra, desde)
                if donde < 0:
                    break
                self._marcas.append((donde, len(palabra)))
                desde = donde + len(palabra)
        self._sembrar_estrellas()
        if self._marcas and len(self._marcas) <= self._TOPE_MARCAS \
                and self.isVisible():
            self._timer.start()
        self.viewport().update()

    # --- animación ------------------------------------------------------
    def brillo(self) -> float:
        """Cuánto luce el halo ahora mismo (0 apagado, 1 encendido)."""
        return (1 - math.cos(2 * math.pi * self._fase)) / 2

    def _latir(self) -> None:
        self._fase = (self._fase + self._MS / self._RESPIRO) % 1.0
        paso = self._MS / self._VIDA_ESTRELLA
        for estrella in self._estrellas:
            antes = estrella["fase"]
            estrella["fase"] = (antes + paso) % 1.0
            if estrella["fase"] < antes:        # se apagó y vuelve a nacer
                estrella.update(self._sitio())
        # Se repinta SOLO lo que ocupa cada coincidencia, no la página.
        for rect in self._rectangulos().values():
            self.viewport().update(
                rect.adjusted(
                    -self._MARGEN, -self._MARGEN, self._MARGEN, self._MARGEN
                ).toRect()
            )

    def _rectangulos(self) -> dict[int, QRectF]:
        """Dónde cae cada coincidencia en la ventana, ahora mismo."""
        rects: dict[int, QRectF] = {}
        alto = self.viewport().height()
        for i, (inicio, largo) in enumerate(self._marcas):
            cursor = QTextCursor(self.document())
            cursor.setPosition(inicio)
            uno = QRectF(self.cursorRect(cursor))
            cursor.setPosition(inicio + largo)
            otro = QRectF(self.cursorRect(cursor))
            if abs(uno.top() - otro.top()) > 1:
                continue            # partida entre dos renglones: se salta
            if otro.bottom() < 0 or uno.top() > alto:
                continue            # fuera de la parte visible
            rects[i] = QRectF(
                uno.left(), uno.top(),
                max(2.0, otro.right() - uno.left()), uno.height()
            )
        return rects

    # --- estrellas ------------------------------------------------------
    def _sembrar_estrellas(self) -> None:
        """
        Reparte los destellos por las coincidencias.

        Cada uno guarda su sitio y su desfase; el sitio se sortea de
        nuevo SOLO cuando la estrella se apaga y vuelve a nacer. Si se
        sorteara en cada fotograma, en vez de brillar temblarían.
        """
        self._estrellas = []
        for i, (_inicio, largo) in enumerate(self._marcas):
            for _ in range(max(2, min(5, largo // 3))):
                self._estrellas.append(
                    {"marca": i, "fase": random.random(), **self._sitio()}
                )

    @staticmethod
    def _sitio() -> dict:
        """
        Dónde nace una estrella, en coordenadas de la palabra (0 a 1).

        Cuatro de cada cinco salen en los BORDES: ahí enmarcan lo
        resaltado sin ponerse encima de las letras, que es donde
        estorbarían para leer.
        """
        if random.random() < 0.8:
            x = (
                random.uniform(-0.04, 0.15) if random.random() < 0.5
                else random.uniform(0.85, 1.04)
            )
        else:
            x = random.uniform(0.15, 0.85)
        return {"x": x, "y": random.uniform(-0.05, 1.05)}

    # --- pintado --------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().paintEvent(event)
        if not self._marcas:
            return
        rects = self._rectangulos()
        if not rects:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Suma de luz, no pintura encima: así el oro ilumina la letra en
        # vez de taparla.
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Plus
        )
        luz = self.brillo()
        for rect in rects.values():
            self._pintar_halo(painter, rect, luz)
        for estrella in self._estrellas:
            rect = rects.get(estrella["marca"])
            if rect is None:
                continue
            # Nace, brilla y se apaga: una campana, no un interruptor
            fuerza = math.sin(math.pi * estrella["fase"]) ** 2
            if fuerza < 0.05:
                continue
            pintar_estrella(
                painter,
                QPointF(
                    rect.left() + rect.width() * estrella["x"],
                    rect.top() + rect.height() * estrella["y"],
                ),
                self._RADIO * (0.45 + 0.55 * fuerza),
                fuerza,
            )
        painter.end()

    def _pintar_halo(self, painter: QPainter, rect: QRectF,
                     luz: float) -> None:
        """Veladura de oro que respira sobre la palabra."""
        halo = QRadialGradient(rect.center(), max(rect.width(), 18.0) * 0.62)
        centro = QColor(ORO_CLARO)
        centro.setAlpha(int(24 + 40 * luz))
        medio = QColor(ORO)
        medio.setAlpha(int(10 + 22 * luz))
        halo.setColorAt(0.0, centro)
        halo.setColorAt(0.55, medio)
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawRoundedRect(
            rect.adjusted(-5, -3, 5, 3), rect.height() / 2, rect.height() / 2
        )

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._marcas:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._timer.stop()          # una página oculta no se anima
        super().hideEvent(event)


def texto_de_hoja(hoja: dict) -> str:
    """La hoja en texto llano, ya compuesta (para copiar la cita)."""
    bloques, _marcas = componer_pagina(hoja.get("cuerpo", ""))
    return (chr(10) * 2).join(texto for _tipo, texto in bloques)


class PasajesDeTomoDialog(FramelessDialog):
    """
    Los pasajes de UN tomo, con sitio para leerlos.

    Arriba, la lista de coincidencias con su localización; abajo, la
    PÁGINA ENTERA de la que sale la que esté seleccionada, con lo
    buscado resaltado. Se cambia de pasaje con las flechas y el texto
    de abajo sigue.

    Antes esto se desplegaba dentro de la ventana de búsqueda, en la
    misma tabla: la localización y el pasaje quedaban cortados en una
    sola línea y no se podía consultar nada (2026-08-05).
    """

    HEADERS = ("Localización", "Pasaje")
    _MAX_PASAJES = 300      # tope de coincidencias que se traen del tomo

    def __init__(self, indice, canonico: str, consulta: str,
                 incluir_notas: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(canonico[:70], parent)
        self._indice = indice
        self._canonico = canonico
        self._consulta = consulta
        self._hallazgos: list = []
        self.resize(1320, 800)
        # Ancho mínimo pensado para el PLIEGO: con menos, cada página se
        # queda en una columna estrecha y se lee peor que en una sola.
        self.setMinimumSize(980, 560)

        cabecera = QLabel(canonico)
        cabecera.setObjectName("titulo")
        cabecera.setWordWrap(True)
        self.body.addWidget(cabecera)

        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("mensaje")
        self.lbl_info.setWordWrap(True)
        self.body.addWidget(self.lbl_info)
        self.body.addWidget(_filete(1))

        self.table = GlowTable(0, len(self.HEADERS))
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)
        cab = self.table.horizontalHeader()
        ancho = max(320, GlowHeader.required_width(self.HEADERS[0]))
        cab.setSectionResizeMode(0, cab.ResizeMode.Fixed)
        self.table.setColumnWidth(0, ancho)
        cab.setStretchLastSection(True)
        # Basta con SELECCIONAR para leer: pedir doble clic para cada
        # pasaje era el clic de más que sobraba, y abrir encima OTRA
        # ventana no aportaba nada (2026-08-08).
        self.table.itemSelectionChanged.connect(self._mostrar_seleccionado)
        # La lista es para ELEGIR; lo que se lee es el pliego de abajo,
        # así que la lista no crece más de lo que hace falta y todo lo
        # que sobra se lo queda el texto (2026-08-09).
        self.table.setMaximumHeight(230)
        self.body.addWidget(self.table, 0)

        self.lbl_donde = QLabel("")
        self.lbl_donde.setObjectName("subtitulo")
        self.lbl_donde.setWordWrap(True)
        self.body.addWidget(self.lbl_donde)

        # DOS páginas, como un libro abierto: la par a la izquierda y la
        # impar a la derecha, que es como cae un pliego de verdad. Se
        # enseñan siempre las dos aunque el pasaje esté solo en una.
        pliego = QHBoxLayout()
        pliego.setSpacing(10)
        self.visor_izq = VisorDePagina()
        self.visor_der = VisorDePagina()
        for visor in (self.visor_izq, self.visor_der):
            pliego.addWidget(visor, 1)
        self.body.addLayout(pliego, 1)

        botones = QHBoxLayout()
        botones.addStretch(1)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        botones.addWidget(btn_close)
        self.body.addLayout(botones)

        self._cargar(incluir_notas)

    def _cargar(self, incluir_notas: bool) -> None:
        from app import rag

        try:
            self._hallazgos = self._indice.buscar(
                self._consulta, limite=self._MAX_PASAJES,
                tomo=self._canonico, incluir_notas=incluir_notas,
                por_tomo=0,
            )
        except rag.RagError as exc:
            self.lbl_info.setText(str(exc))
            return
        self.table.setRowCount(len(self._hallazgos))
        for i, h in enumerate(self._hallazgos):
            donde = QTableWidgetItem(h.cita())
            donde.setToolTip(h.cita())
            donde.setData(Qt.ItemDataRole.UserRole, i)
            if h.clase == "notas":
                donde.setForeground(QColor(ORO_APAGADO))
            self.table.setItem(i, 0, donde)
            recorte = rag.resaltar(
                h.texto, self._consulta, ancho=420
            ).replace("\n", " ")
            pasaje = QTableWidgetItem(recorte)
            pasaje.setToolTip(h.texto[:600])
            # En una celda no cabe HTML: si el pasaje es griego, va
            # ENTERO con la fuente que tiene politónico.
            if es_griego(recorte):
                pasaje.setFont(fuente_griega(10.5))
            self.table.setItem(i, 1, pasaje)
        self.lbl_info.setText(
            f"{len(self._hallazgos)} pasaje(s) para «{self._consulta}» · "
            "pincha uno para leer su página entera"
            if self._hallazgos else
            f"Ningún pasaje de este tomo para «{self._consulta}»."
        )
        if self._hallazgos:
            self.table.selectRow(0)

    def _actual(self):
        fila = self.table.currentRow()
        if 0 <= fila < len(self._hallazgos):
            return self._hallazgos[fila]
        return None

    def _hoja_actual(self) -> dict:
        h = self._actual()
        if h is None:
            return {}
        return self._indice.hoja_completa(h.canonico, h.hoja) or {}

    # Si la coincidencia cae en el primer tercio de su página, el pliego
    # se abre por la ANTERIOR: así se lee de dónde venía. Si cae más
    # abajo, ya trae su propio contexto delante y se empareja con la
    # siguiente. Manda esto y no la paridad par/impar del libro real:
    # perder el contexto se nota al leer, y que la página caiga a un
    # lado o al otro, no (2026-08-08).
    _ARRIBA_DEL_TODO = 0.35

    def _donde_cae(self, hoja: dict) -> float:
        """En qué punto de la página (0 arriba, 1 abajo) está lo buscado."""
        from app import rag

        cuerpo = hoja.get("cuerpo") or ""
        if not cuerpo or not self._consulta:
            return 1.0
        plano = rag._plano(cuerpo)
        for palabra in sorted(
            (p for p in rag.normaliza(self._consulta).split() if len(p) > 2),
            key=len, reverse=True,
        ):
            donde = plano.find(palabra)
            if donde >= 0:
                return donde / max(1, len(plano))
        return 1.0

    def _pliego(self, hoja: dict) -> tuple[int, int]:
        """
        Las DOS hojas que se ven al abrir el libro por esta.

        Se elige el lado para no perder el contexto: con la coincidencia
        arriba del todo, la página va a la DERECHA y a la izquierda
        queda la anterior; si no, va a la izquierda y se ve la siguiente.
        """
        actual = int(hoja.get("hoja") or 0)
        if actual > 1 and self._donde_cae(hoja) < self._ARRIBA_DEL_TODO:
            return actual - 1, actual
        return actual, actual + 1

    @staticmethod
    def _asomar(visor: QTextEdit, fraccion: float) -> None:
        """
        Deja la coincidencia a la vista, con texto por delante.

        Se coloca a un tercio de la altura y no arriba del todo: leer un
        pasaje empezando justo en la palabra buscada es quedarse sin
        saber de qué venía.
        """
        barra = visor.verticalScrollBar()
        alto = barra.maximum()
        if alto <= 0:
            return
        destino = fraccion * alto - visor.viewport().height() / 3
        barra.setValue(max(0, min(alto, int(destino))))

    def _mostrar_seleccionado(self) -> None:
        hoja = self._hoja_actual()
        if not hoja:
            self.visor_izq.clear()
            self.visor_der.clear()
            self.lbl_donde.setText("")
            return
        izq, der = self._pliego(hoja)
        rotulos = []
        for numero, visor in ((izq, self.visor_izq), (der, self.visor_der)):
            otra = (
                hoja if numero == hoja.get("hoja")
                else (self._indice.hoja_completa(self._canonico, numero) or {})
            )
            if otra:
                # Lo buscado solo se resalta en la página donde está.
                suya = numero == hoja.get("hoja")
                # El pliego se lee como un texto seguido: la página de
                # la izquierda arranca en frase entera y la de la
                # derecha acaba en punto, para no dejar al lector
                # colgado en ninguno de los dos bordes.
                visor.mostrar(
                    html_de_hoja(
                        otra, self._consulta if suya else "",
                        desde_frase=(visor is self.visor_izq),
                        hasta_frase=(visor is self.visor_der),
                    ),
                    self._consulta if suya else "",
                )
                rotulos.append(localizacion_de_hoja(otra))
                if suya:
                    self._asomar(visor, self._donde_cae(otra))
                else:
                    visor.verticalScrollBar().setValue(0)
            else:
                # Página en blanco: el pliego se enseña entero igual.
                visor.mostrar(
                    f'<div style="color:{ORO_APAGADO};font-size:9pt">'
                    "(no hay más páginas por este lado)</div>"
                )
        self.lbl_donde.setText("   ·   ".join(rotulos))

class PasajeDelDiaDialog(FramelessDialog):
    """
    El pasaje del día: un trozo de un tomo, elegido al azar, el mismo
    hasta las doce de la noche.

    No son «veinticuatro horas» contadas desde que se abre: el pasaje va
    por FECHA, así que cambia a medianoche aunque se haya leído a las
    once. Y como esta ventana puede quedarse abierta —la aplicación vive
    en la bandeja—, lleva su propio reloj para cambiarlo en el sitio.

    Arriba, un TÍTULO que dice de qué va y dos líneas de resumen;
    debajo, la página entera donde está, para leerlo en su sitio.

    El título y el resumen NO los escribe ninguna IA —en esta
    aplicación no hay ninguna— y por eso no se inventan: el título sale
    de los nombres que el traductor puso en el índice del propio tomo, y
    el resumen es la frase más significativa ENTRESACADA del pasaje. Un
    resumen inventado en una biblioteca es peor que ninguno.
    """

    def __init__(self, ficha: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__("Pasaje del día", parent)
        self.resize(980, 760)
        self.setMinimumSize(720, 520)
        self._ficha = ficha

        self.lbl_titulo = QLabel()
        self.lbl_titulo.setObjectName("titulo")
        self.lbl_titulo.setFont(fuente(15, negrita=True, espaciado=1.6))
        self.lbl_titulo.setWordWrap(True)
        self.body.addWidget(self.lbl_titulo)

        self.lbl_donde = QLabel()
        self.lbl_donde.setObjectName("subtitulo")
        self.lbl_donde.setWordWrap(True)
        self.body.addWidget(self.lbl_donde)

        self.lbl_obra = QLabel()
        self.lbl_obra.setObjectName("mensaje")
        self.lbl_obra.setWordWrap(True)
        self.body.addWidget(self.lbl_obra)

        # NO hay línea de resumen (retirada el 2026-08-09). Era una
        # frase ENTRESACADA del pasaje, no un resumen, y aparecía otra
        # vez unos renglones más abajo dentro de la propia página: se
        # leía como una promesa incumplida —anunciaba algo que no era lo
        # primero que se leía— y no añadía nada que no dijeran ya el
        # título y la línea de nombres. Se probó antes a hacer que la
        # página empezara justo en el pasaje del que sale la frase
        # (`desde_el_pasaje`), y aun así la disonancia seguía.

        # Se crea siempre y se esconde si el tomo no trae índice de
        # nombres: al cambiar de pasaje a medianoche, el nuevo puede
        # tenerlo o no, y un widget que aparece y desaparece del layout
        # descoloca todo lo de debajo.
        self.lbl_nombres = QLabel()
        self.lbl_nombres.setObjectName("mensaje")
        self.lbl_nombres.setWordWrap(True)
        self.body.addWidget(self.lbl_nombres)

        self.body.addSpacing(6)
        self.body.addWidget(_filete(1))

        self.visor = VisorDePagina()
        self.body.addWidget(self.visor, 1)

        botones = QHBoxLayout()
        btn_tomo = GlowButton("Buscar en este tomo")
        btn_tomo.setToolTip("Abre el buscador dentro de los textos.")
        btn_tomo.clicked.connect(self._abrir_tomo)
        botones.addWidget(btn_tomo)
        botones.addStretch(1)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        botones.addWidget(btn_close)
        self.body.addLayout(botones)

        self._pintar(ficha)

        # A las doce de la noche, otro pasaje —aunque la ventana lleve
        # abierta desde ayer—. Se apunta al INSTANTE del cambio en vez
        # de sondear cada minuto: es exacto y no gasta nada. El disparo
        # se vuelve a armar solo, para las ventanas que se quedan
        # abiertas varios días.
        self._reloj = QTimer(self)
        self._reloj.setSingleShot(True)
        self._reloj.timeout.connect(self._cambia_el_dia)
        self._armar_para_medianoche()

    # -- el cambio de día ------------------------------------------------
    def _armar_para_medianoche(self) -> None:
        """Programa el disparo para el primer segundo del día siguiente."""
        from datetime import datetime, timedelta

        ahora = datetime.now()
        manana = (ahora + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        # Un segundo de propina: si se dispara clavado a las 00:00:00,
        # `date.today()` puede devolver todavía el día de ayer.
        faltan = int((manana - ahora).total_seconds() * 1000) + 1000
        self._reloj.start(max(1000, faltan))

    def _cambia_el_dia(self) -> None:
        """
        Trae el pasaje de hoy y vuelve a armar el reloj.

        Si el equipo estuvo suspendido, el aviso llega tarde y puede
        haber pasado más de un día: da igual, se pide el de HOY.
        """
        from datetime import date

        from app import rag

        try:
            ficha = rag.indice_compartido().pasaje_del_dia(
                date.today().isoformat())
        except rag.RagError:
            ficha = {}
        if ficha:
            self._ficha = ficha
            self._pintar(ficha)
        self._armar_para_medianoche()

    def _pintar(self, ficha: dict) -> None:
        """Vuelca la ficha en la ventana (al abrir y al cambiar el día)."""
        self.lbl_titulo.setText(ficha.get("titulo", "") or "Pasaje del día")
        self.lbl_donde.setText(localizacion_de_hoja(ficha.get("pagina", {}))
                               or ficha.get("canonico", ""))
        self.lbl_obra.setText(ficha.get("canonico", ""))
        nombres = ficha.get("nombres") or []
        self.lbl_nombres.setText(
            "Sale en el índice del tomo: " + " · ".join(nombres[:6])
            if nombres else ""
        )
        self.lbl_nombres.setVisible(bool(nombres))
        # Empieza tras un punto y cierra en PUNTO Y APARTE: aquí solo
        # se enseña una página, así que el final del texto es el final
        # de la lectura y conviene que sea el de un párrafo entero.
        self.visor.mostrar(
            html_de_hoja(
                ficha.get("pagina", {}), "",
                desde_frase=True, hasta_parrafo=True,
                desde_texto=ficha.get("texto", ""),
            ),
            "",
        )

    def _abrir_tomo(self) -> None:
        from app import rag

        try:
            indice = rag.indice_compartido()
        except rag.RagError:
            return
        nombres = self._ficha.get("nombres") or []
        consulta = nombres[0] if nombres else ""
        PasajesDeTomoDialog(
            indice, self._ficha.get("canonico", ""), consulta,
            incluir_notas=False, parent=self,
        ).exec()


class BuscarTextosDialog(FramelessDialog):
    """
    Busca una frase dentro del TEXTO de los tomos ya extraídos.

    Dos respuestas distintas, y las dos hacen falta:

    - **El índice de nombres del traductor** (arriba): si lo buscado es
      un nombre propio, el propio tomo ya dice dónde sale y con qué
      localización canónica. Es la respuesta más fiable y no ha hecho
      falta leer una línea de texto.
    - **Los pasajes** (abajo): el texto literal donde aparece lo
      buscado, con su tomo, su obra y su página impresa.

    Todo es local: ni sube nada a ningún sitio ni necesita clave de API.
    """

    HEADERS = ("Tomo", "Localización", "Pasaje")
    HEADERS_NOMBRES = ("Tomo", "Nombre", "Veces", "Se cita en")
    # Alfabético primero: el canónico empieza por el autor, así que la
    # lista queda como la estantería. El orden por coincidencias sigue
    # a mano, que para saber DÓNDE está mejor tratado un asunto es el
    # que vale.
    _ORDENES = ("Alfabético", "Más coincidencias")

    def __init__(self, consulta: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("Buscar en los textos", parent)
        self.resize(1360, 860)
        self.setMinimumSize(900, 560)
        self._indice = None
        self._worker: Optional[_IndexarWorker] = None
        self._tomos_hit: list = []      # todos los tomos con coincidencia
        self._consulta = ""

        top = QHBoxLayout()
        top.setSpacing(8)
        self.ed_search = GlowLineEdit(consulta)
        self.ed_search.setPlaceholderText(
            'palabras sueltas, o "una frase exacta" entre comillas'
        )
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setToolTip(
            "Varias palabras: tienen que aparecer todas.\n"
            'Entre comillas: la frase exacta, en ese orden.\n'
            "Terminando en *: empieza por."
        )
        self.ed_search.returnPressed.connect(self._buscar)
        top.addWidget(self.ed_search, 1)
        btn = GlowButton("Buscar")
        btn.clicked.connect(self._buscar)
        top.addWidget(btn)
        self.cb_notas = QCheckBox("Con notas")
        self.cb_notas.setToolTip(
            "Busca también en las notas del traductor.\n"
            "Son dos tercios del corpus: sin marcar, solo el texto del autor."
        )
        self.cb_notas.stateChanged.connect(lambda _: self._buscar())
        top.addWidget(self.cb_notas)
        top.addWidget(QLabel("Ordenar:"))
        self.cmb_orden = QComboBox()
        for etiqueta in self._ORDENES:
            self.cmb_orden.addItem(etiqueta)
        self.cmb_orden.setToolTip(
            "Alfabético: por autor y obra, como en la estantería.\n"
            "Más coincidencias: primero los tomos donde más sale."
        )
        self.cmb_orden.currentIndexChanged.connect(lambda _i: self._buscar())
        top.addWidget(self.cmb_orden)
        self.btn_index = GlowButton("Actualizar índice")
        self.btn_index.setToolTip(
            "Mete en el buscador los tomos analizados desde la última vez.\n"
            "El índice es un archivo aparte y reconstruible."
        )
        self.btn_index.clicked.connect(self._indexar)
        top.addWidget(self.btn_index)
        self.body.addLayout(top)

        # --- Respuesta del índice de nombres (solo si la hay) -------------
        self.lbl_nombres = QLabel("")
        self.lbl_nombres.setObjectName("titulo")
        self.lbl_nombres.setWordWrap(True)
        self.body.addWidget(self.lbl_nombres)
        self.tbl_nombres = GlowTable(0, len(self.HEADERS_NOMBRES))
        self.tbl_nombres.setHorizontalHeader(GlowHeader(self.tbl_nombres))
        self.tbl_nombres.setHorizontalHeaderLabels(self.HEADERS_NOMBRES)
        self._preparar_tabla(self.tbl_nombres)
        self.tbl_nombres.setMaximumHeight(150)
        self.tbl_nombres.cellDoubleClicked.connect(self._buscar_ese_tomo)
        cab = self.tbl_nombres.horizontalHeader()
        for columna, base in ((1, 200), (2, 70)):
            ancho = max(base,
                        GlowHeader.required_width(self.HEADERS_NOMBRES[columna]))
            cab.setSectionResizeMode(columna, cab.ResizeMode.Fixed)
            self.tbl_nombres.setColumnWidth(columna, ancho)
        cab.setSectionResizeMode(0, cab.ResizeMode.Stretch)
        cab.setStretchLastSection(True)
        self.body.addWidget(self.tbl_nombres)
        self.lbl_nombres.hide()
        self.tbl_nombres.hide()

        # --- Tomos donde aparece ------------------------------------------
        # Con su propio título: son DOS listas y sin rótulo no se sabía
        # cuál era cuál (arriba lo que dice el índice del traductor;
        # aquí, dónde aparece de verdad en el texto).
        self.lbl_tomos = QLabel("")
        self.lbl_tomos.setObjectName("titulo")
        self.lbl_tomos.setWordWrap(True)
        self.lbl_tomos.hide()
        self.body.addWidget(self.lbl_tomos)

        self.table = GlowTable(0, len(self.HEADERS))
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self._preparar_tabla(self.table)
        # Sin ordenar por columna: las filas van en dos niveles (el tomo
        # y sus pasajes debajo), y reordenarlas los separaría.
        self.table.setSortingEnabled(False)
        self.table.cellDoubleClicked.connect(self._doble_clic)
        header = self.table.horizontalHeader()
        for columna, base in ((0, 300), (1, 260)):
            ancho = max(base, GlowHeader.required_width(self.HEADERS[columna]))
            header.setSectionResizeMode(columna, header.ResizeMode.Fixed)
            self.table.setColumnWidth(columna, ancho)
        header.setStretchLastSection(True)
        self.body.addWidget(self.table, 1)

        self.progress = GlowProgress()
        self.progress.hide()
        self.body.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("mensaje")
        self.lbl_status.setWordWrap(True)
        self.body.addWidget(self.lbl_status)

        botones = QHBoxLayout()
        botones.addStretch(1)
        btn_close = GlowButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        botones.addWidget(btn_close)
        self.body.addLayout(botones)

        QTimer.singleShot(0, self._arrancar)

    @staticmethod
    def _preparar_tabla(tabla: QTableWidget) -> None:
        tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tabla.setAlternatingRowColors(True)
        tabla.setShowGrid(False)
        tabla.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tabla.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.verticalHeader().setDefaultSectionSize(32)
        tabla.setWordWrap(False)

    # --- índice ---------------------------------------------------------
    def _arrancar(self) -> None:
        """Abre el índice y, si está vacío o le faltan tomos, lo dice."""
        from app import rag

        try:
            self._indice = rag.indice_compartido()
        except rag.RagError as exc:
            self.lbl_status.setText(str(exc))
            self.btn_index.setEnabled(False)
            return
        datos = self._indice.resumen()
        if not datos["tomos"]:
            self.lbl_status.setText(
                "El buscador aún no tiene el texto de ningún tomo. "
                "Pulsa «Actualizar índice» para prepararlo (una vez, "
                "menos de un minuto)."
            )
            return
        self._contar(datos)
        if self.ed_search.text().strip():
            self._buscar()

    def _contar(self, datos: Optional[dict] = None) -> None:
        if self._indice is None:
            return
        datos = datos or self._indice.resumen()
        aviso = (
            f" · {datos['pendientes']} tomo(s) analizados después: pulsa "
            "«Actualizar índice»" if datos["pendientes"] else ""
        )
        self.lbl_status.setText(
            f"{datos['tomos']} tomos · {datos['pasajes']:,} pasajes · "
            f"{datos['nombres']:,} nombres del índice del traductor{aviso}"
            .replace(",", ".")
        )

    def _indexar(self) -> None:
        if self._indice is None or self._worker is not None:
            return
        self.btn_index.setEnabled(False)
        self.progress.arrancar("Preparando el buscador…", 0)
        self.progress.show()
        self._worker = _IndexarWorker(self._indice, parent=self)
        self._worker.progreso.connect(
            lambda fase, hechas, total: self.progress.avanzar(fase, hechas, total)
        )
        self._worker.listo.connect(self._indexado)
        self._worker.start()

    def _indexado(self, res, error: str) -> None:
        self._worker = None
        self.progress.parar()
        self.progress.hide()
        self.btn_index.setEnabled(True)
        if error:
            GredosMessageBox.show_info(
                self, "Buscador de textos",
                f"No se pudo preparar el índice:\n\n{error}",
            )
            return
        self._contar()
        if res is not None and res.errores:
            logger.warning("Textos ilegibles al indexar: %s", res.errores)
        if self.ed_search.text().strip():
            self._buscar()

    # --- búsqueda -------------------------------------------------------
    # La lista de resultados son TODOS los tomos donde aparece lo
    # buscado —completa, sin recortes—; el doble clic abre los pasajes
    # de ESE tomo en su propia ventana. Antes se pedían los pasajes
    # directamente y había que cortar por algún sitio: buscando
    # «lacedemonios» (2.510 pasajes) faltaban tomos enteros, como las
    # obras menores de Jenofonte. Y así solo se carga el texto del tomo
    # que se abre.
    def _buscar(self) -> None:
        from app import rag

        if self._indice is None:
            return
        consulta = self.ed_search.text().strip()
        self._consulta = consulta
        if not consulta:
            self.table.setRowCount(0)
            self.lbl_tomos.hide()
            self._ocultar_nombres()
            self._contar()
            return
        # El modo se anota en un dict de FUERA: la ventana necesita saber
        # si hizo falta aflojar la búsqueda para poder decirlo.
        estado: dict = {}
        try:
            tomos = self._indice.tomos_con(
                consulta, incluir_notas=self.cb_notas.isChecked(),
                estado=estado,
            )
        except rag.RagError as exc:
            self.lbl_status.setText(str(exc))
            return
        # `tomos_con` los da por número de coincidencias; el alfabético
        # se ordena aquí, sin tildes (si no, «Ésquilo» iría detrás de
        # «Zenón») y por el canónico, que empieza por el autor.
        if self.cmb_orden.currentText() == self._ORDENES[0]:
            tomos.sort(key=lambda t: rag.normaliza(t["canonico"]))
        self._tomos_hit = tomos
        self._pintar_nombres(consulta)

        self.table.setRowCount(len(tomos))
        for i, t in enumerate(tomos):
            self._pintar_tomo(i, t, abierto=False)
        if tomos:
            self.lbl_tomos.setText(
                f"EN EL TEXTO DE LOS TOMOS · «{consulta}» aparece en "
                f"{len(tomos)} tomo(s)"
            )
            self.lbl_tomos.show()
        else:
            self.lbl_tomos.hide()

        total = sum(t["pasajes"] for t in tomos)
        datos = self._indice.resumen()
        if not tomos:
            falta = (
                f" · quedan {datos['pendientes']} tomos por indexar"
                if datos["pendientes"] else ""
            )
            self.lbl_status.setText(
                f"Sin resultados para «{consulta}» en los {datos['tomos']} "
                f"tomos indexados{falta}."
            )
            return
        if estado.get("modo") == "algunas":
            usadas = ", ".join(estado.get("palabras", []))
            aviso = (
                f"Ningún pasaje lleva todas esas palabras; se buscó por "
                f"«{usadas}». "
            )
        elif estado.get("modo") == "solo_vacias":
            aviso = (
                "Eso son solo palabras de enlace (artículos, "
                "preposiciones), que salen en casi todas las páginas. "
            )
        else:
            aviso = ""
        self.lbl_status.setText(
            f"{aviso}{len(tomos)} tomo(s) · {total} pasaje(s) · doble clic "
            "en un tomo para leer los suyos"
        )

    def _pintar_tomo(self, fila: int, tomo: dict, abierto: bool = False) -> None:
        """Fila de un tomo: el título y cuántas veces sale."""
        titulo = QTableWidgetItem(tomo["canonico"])
        titulo.setToolTip(tomo["canonico"])
        titulo.setForeground(QColor(ORO_CLARO))
        titulo.setData(Qt.ItemDataRole.UserRole, ("tomo", tomo["canonico"]))
        self.table.setItem(fila, 0, titulo)
        numero = f"Tomo {tomo['numero']} · " if tomo["numero"] else ""
        cuantas = QTableWidgetItem(
            f"{numero}{tomo['hojas']} página(s) · {tomo['pasajes']} pasaje(s)"
        )
        cuantas.setForeground(QColor(ORO_VIEJO))
        self.table.setItem(fila, 1, cuantas)
        pista = QTableWidgetItem("doble clic para leerlos")
        pista.setForeground(QColor(ORO_APAGADO))
        self.table.setItem(fila, 2, pista)

    def _fila_del_tomo(self, canonico: str) -> Optional[int]:
        for fila in range(self.table.rowCount()):
            item = self.table.item(fila, 0)
            dato = item.data(Qt.ItemDataRole.UserRole) if item else None
            if dato and dato[0] == "tomo" and dato[1] == canonico:
                return fila
        return None

    def _doble_clic(self, fila: int, _col: int) -> None:
        """Abre los pasajes de ese tomo en su propia ventana."""
        item = self.table.item(fila, 0)
        dato = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not dato or dato[0] != "tomo" or self._indice is None:
            return
        self.abrir_tomo(dato[1])

    def abrir_tomo(self, canonico: str) -> Optional[QWidget]:
        """
        Ventana con los pasajes de un tomo, para leerlos con sitio.

        Antes se desplegaban debajo, en la misma tabla: la localización
        y el pasaje quedaban cortados en una línea y no había manera de
        leer nada (2026-08-05).
        """
        if self._indice is None:
            return None
        dialogo = PasajesDeTomoDialog(
            self._indice, canonico, self._consulta,
            incluir_notas=self.cb_notas.isChecked(), parent=self,
        )
        dialogo.exec()
        return dialogo

    def _ocultar_nombres(self) -> None:
        self.lbl_nombres.hide()
        self.tbl_nombres.hide()
        self.tbl_nombres.setRowCount(0)

    def _pintar_nombres(self, consulta: str) -> None:
        """
        La respuesta directa: lo que el traductor dejó dicho en el
        índice de nombres del propio tomo.
        """
        from app import rag

        if self._indice is None:
            return
        # Sobre lo que queda de la consulta DESPUÉS de quitar el andamio
        # de la pregunta: «¿qué tomo habla de los lacedemonios?» se
        # reduce a «lacedemonios», que es justo un nombre del índice.
        # Solo tiene sentido con una o dos palabras: nadie indexa una
        # frase entera como nombre propio.
        piezas = [p.strip("*").strip('"') for p in rag.piezas_fts(consulta)]
        limpio = " ".join(piezas).strip()
        if not limpio or len(limpio.split()) > 3:
            self._ocultar_nombres()
            return
        filas = self._indice.buscar_nombres(limpio, limite=40)
        if not filas:
            self._ocultar_nombres()
            return
        self.lbl_nombres.setText(
            f"ÍNDICE DE NOMBRES DEL TRADUCTOR · cita «{limpio}» en "
            f"{len({f['canonico'] for f in filas})} tomo(s)"
        )
        self.tbl_nombres.setRowCount(len(filas))
        for i, f in enumerate(filas):
            tomo = QTableWidgetItem(f["canonico"])
            tomo.setToolTip(f["canonico"])
            tomo.setData(Qt.ItemDataRole.UserRole, f["canonico"])
            self.tbl_nombres.setItem(i, 0, tomo)
            self.tbl_nombres.setItem(i, 1, QTableWidgetItem(f["nombre"]))
            self.tbl_nombres.setItem(
                i, 2, _NumItem(str(f["cuantas"]), f["cuantas"])
            )
            refs = QTableWidgetItem(f["refs"])
            refs.setToolTip(f["refs"])
            self.tbl_nombres.setItem(i, 3, refs)
        self.lbl_nombres.show()
        self.tbl_nombres.show()

    def _por_que_no_esta(self, canonico: str, refs: str = "") -> str:
        """
        Por qué un tomo que cita el nombre no está en la lista de abajo.

        Son DOS cosas distintas y antes se decían igual («la palabra no
        aparece en el texto indexado del tomo»), que además podía ser
        falso: la palabra estaba, escondida por el filtro de notas.

        Y cuando de verdad no está, lo que hay que decir es lo otro: el
        índice de nombres de la BCG suele ser COMÚN a toda la obra, así
        que una cita puede corresponder a un volumen distinto —y quizá
        sin analizar—. Caso real: «mirmidones» en «Ovidio —
        Metamorfosis · Libros XI-XV», cuyo índice cita VII 654, un verso
        que se imprime en los tomos 365 y 400 (2026-08-14).
        """
        from app import rag

        if not self.cb_notas.isChecked() and self._indice is not None:
            try:
                con_notas = self._indice.tomos_con(
                    self._consulta, incluir_notas=True
                )
            except rag.RagError:
                con_notas = []
            if any(t["canonico"] == canonico for t in con_notas):
                return (
                    f"En «{canonico}» eso solo sale en sus notas: marca "
                    f"«Con notas» para verlo."
                )
        cita = f" ({refs.split(';')[0].strip()})" if refs else ""
        return (
            f"«{canonico}» cita ese nombre en su índice{cita}, pero el "
            f"pasaje no está en ESTE tomo: el índice de la BCG es común a "
            f"toda la obra, así que la cita puede ser de otro volumen."
        )

    def _buscar_ese_tomo(self, fila: int, _col: int) -> None:
        """
        Doble clic en un nombre: se despliega ESE tomo en la lista de
        abajo y se lleva la vista hasta él.
        """
        item = self.tbl_nombres.item(fila, 0)
        if item is None or self._indice is None:
            return
        canonico = item.data(Qt.ItemDataRole.UserRole)
        destino = self._fila_del_tomo(canonico)
        if destino is None:
            refs = self.tbl_nombres.item(fila, 3)
            self.lbl_status.setText(
                self._por_que_no_esta(canonico, refs.text() if refs else "")
            )
            return
        self.table.selectRow(destino)
        self.table.scrollToItem(self.table.item(destino, 0))
        self.abrir_tomo(canonico)

    # --- cierre ---------------------------------------------------------
    def _cortar_trabajo(self) -> None:
        """
        Corta el indexado al cerrar. Misma regla que en Textos: destruir
        un QThread vivo tumba la aplicación.
        """
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker.cancelar()
        if not worker.wait(3000):
            worker.setParent(None)
            _HILOS_SUELTOS.append(worker)
            worker.finished.connect(
                lambda w=worker: w in _HILOS_SUELTOS and _HILOS_SUELTOS.remove(w)
            )

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._cortar_trabajo()
        super().closeEvent(event)

    def reject(self) -> None:  # noqa: D102 - API Qt (Esc y botón Cerrar)
        self._cortar_trabajo()
        super().reject()

    def accept(self) -> None:  # noqa: D102 - API Qt
        self._cortar_trabajo()
        super().accept()


# ----------------------------------------------------------------------
# Colección BCG (BDtomos/titulosBCG.xlsx)
# ----------------------------------------------------------------------
class CollectionDialog(FramelessDialog):
    """
    Visor de la colección Biblioteca Clásica Gredos del usuario.

    Los tomos viven en SQLite (tabla `tomos`); el botón "Reimportar
    Excel" los recarga desde BDtomos/titulosBCG.xlsx. Si la tabla está
    vacía y el Excel existe, se importa automáticamente al abrir.
    """

    HEADERS = (
        "Nº", "Autor(es)", "Título", "Páginas", "Notas",
        "Obtenido", "Deseado",
    )
    _COL_OWNED = 5
    _COL_WISHED = 6
    _FLAG_COLS = {5: "poseido", 6: "deseado"}

    _FILTERS = ("Todos", "Obtenidos", "Deseados", "Me faltan", "Raros (360-415)")

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__("Colección — Biblioteca Clásica Gredos", parent)
        self.db = db
        self._loading = False  # evita reaccionar a itemChanged al poblar
        self.resize(1280, 560)
        self.setMinimumSize(1100, 460)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_count = QLabel("")
        top.addWidget(self.lbl_count)
        top.addStretch(1)
        top.addWidget(QLabel("Ver:"))
        self.cmb_filter = QComboBox()
        for f in self._FILTERS:
            self.cmb_filter.addItem(f)
        self.cmb_filter.currentIndexChanged.connect(
            lambda _i: self._apply_search(self.ed_search.text())
        )
        top.addWidget(self.cmb_filter)
        top.addWidget(QLabel("Buscar:"))
        self.ed_search = GlowLineEdit()
        self.ed_search.setPlaceholderText("autor, obra, número…")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setFixedWidth(220)
        self.ed_search.textChanged.connect(self._apply_search)
        top.addWidget(self.ed_search)
        btn_import = GlowButton("Reimportar Excel")
        btn_import.clicked.connect(lambda: self._reimport(auto=False))
        top.addWidget(btn_import)
        self.body.addLayout(top)

        self.table = GlowTable(0, len(self.HEADERS))
        self.table.setHorizontalHeader(GlowHeader(self.table))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setWordWrap(False)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.body.addWidget(self.table)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("mensaje")
        self.lbl_status.setWordWrap(True)
        self.body.addWidget(self.lbl_status)

        # Primera vez: importar automáticamente si hay Excel y tabla vacía
        if self.db.tomos_count() == 0:
            self._reimport(auto=True)
        else:
            self._populate()

    def _reimport(self, auto: bool = False) -> None:
        from app import collection
        try:
            tomos = collection.load_excel()
        except collection.CollectionError as exc:
            self.lbl_status.setText(str(exc))
            self._populate()
            return
        self.db.replace_tomos([
            (t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
            for t in tomos
        ])
        self.lbl_status.setText(
            f"{'Importación automática' if auto else 'Reimportado'}: "
            f"{len(tomos)} tomo(s) desde {collection.DEFAULT_XLSX.name}."
        )
        self._populate()

    def _populate(self) -> None:
        rows = self.db.get_tomos()
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            orden = row["orden"]
            rare = orden is not None and 360 <= orden <= 415
            appendix = orden is not None and 416 <= orden <= 420

            num_text = row["numero"] + (" 💎" if rare else "")
            item_num = _NumItem(num_text, orden)
            # Identidad del tomo = su NÚMERO: el orden lo comparten tres
            # pares de la colección (2026-07-29).
            item_num.setData(Qt.ItemDataRole.UserRole, row["numero"])
            if rare:
                item_num.setForeground(QColor(ORO_CLARO))
                item_num.setToolTip("Rango 360-415: los tomos más raros de conseguir")
            elif appendix:
                item_num.setForeground(QColor("#7a6f4d"))
                item_num.setToolTip(
                    "Rango 416-420: no pertenece propiamente a la colección"
                )
            self.table.setItem(i, 0, item_num)

            item_autor = QTableWidgetItem(row["autor"])
            item_autor.setToolTip(row["autor"])
            if appendix:
                item_autor.setForeground(QColor("#9d8a55"))
            self.table.setItem(i, 1, item_autor)
            item_obras = QTableWidgetItem(row["obras"])
            item_obras.setToolTip(row["obras"])
            self.table.setItem(i, 2, item_obras)
            paginas = row["paginas"]
            self.table.setItem(
                i, 3, _NumItem(str(paginas) if paginas else "—", paginas)
            )
            notas = row["notas"] or ""
            item_notas = QTableWidgetItem(notas)
            item_notas.setForeground(QColor("#9d8a55"))  # dato secundario
            if notas:
                item_notas.setToolTip(notas)
            self.table.setItem(i, 4, item_notas)

            # Casillas "Obtenido" y "Deseado": _NumItem para que el clic
            # en la CABECERA ordene marcados primero; el nº de colección
            # viaja en UserRole para persistir el cambio en SQLite.
            for col, field, hint in (
                (self._COL_OWNED, "poseido", "Marca los tomos que ya obtuviste"),
                (self._COL_WISHED, "deseado", "Marca los tomos que deseas"),
            ):
                marcado = bool(row[field])
                item_flag = _NumItem("", 1 if marcado else 0)
                item_flag.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item_flag.setCheckState(
                    Qt.CheckState.Checked if marcado
                    else Qt.CheckState.Unchecked
                )
                item_flag.setData(Qt.ItemDataRole.UserRole, row["numero"])
                item_flag.setToolTip(hint)
                self.table.setItem(i, col, item_flag)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        # Ancho = lo que exige el rótulo (métrica real de la fuente del
        # GlowHeader) o el mínimo por contenido — nunca rótulos cortados.
        for col, base in (
            (0, 82), (1, 175), (3, 60), (4, 165),
            (self._COL_OWNED, 60), (self._COL_WISHED, 60),
        ):
            width = max(base, GlowHeader.required_width(self.HEADERS[col]))
            header.setSectionResizeMode(col, header.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self._loading = False

        self._total = len(rows)
        self._apply_search(self.ed_search.text())

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Persiste las casillas Obtenido/Deseado al pulsarlas."""
        if self._loading:
            return
        field = self._FLAG_COLS.get(item.column())
        clave = item.data(Qt.ItemDataRole.UserRole)
        if field is None or clave is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        item._value = 1 if checked else 0  # mantener el ORDEN por casilla
        # Por NÚMERO: tres pares de tomos comparten el orden y marcar
        # uno marcaba también al otro (2026-07-29).
        self.db.set_tomo_flag(clave, field, checked)
        self._update_count_label()

    def _on_double_click(self, row: int, col: int) -> None:
        """Doble clic (fuera de las casillas): abrir la ficha del tomo."""
        if col in self._FLAG_COLS:
            return  # las casillas se gestionan con un clic
        item_num = self.table.item(row, 0)
        clave = item_num.data(Qt.ItemDataRole.UserRole) if item_num else None
        if clave is not None:
            TomoDialog(self.db, clave, self).exec()

    def _row_passes_filter(self, i: int) -> bool:
        """¿La fila pasa el filtro del combo "Ver"?"""
        mode = self.cmb_filter.currentIndex()
        if mode == 0:  # Todos
            return True
        owned_item = self.table.item(i, self._COL_OWNED)
        wished_item = self.table.item(i, self._COL_WISHED)
        owned = (
            owned_item is not None
            and owned_item.checkState() == Qt.CheckState.Checked
        )
        wished = (
            wished_item is not None
            and wished_item.checkState() == Qt.CheckState.Checked
        )
        if mode == 1:  # Obtenidos
            return owned
        if mode == 2:  # Deseados
            return wished
        if mode == 3:  # Me faltan
            return not owned
        # Raros (360-415). El dato de la fila es el NÚMERO del tomo
        # ("1[2]", "415.2"), así que el rango se mira sobre su parte
        # entera — con int() a secas reventaba el filtro (2026-08-01).
        from app import collection as col

        item_num = self.table.item(i, 0)
        numero = item_num.data(Qt.ItemDataRole.UserRole) if item_num else None
        orden = col._first_int(numero)
        return orden is not None and 360 <= orden <= 415

    def _apply_search(self, text: str) -> None:
        """Filtro combinado: combo "Ver" + búsqueda por número/autor/título."""
        query = normalize(text.strip())
        visible = 0
        for i in range(self.table.rowCount()):
            haystack = " ".join(
                self.table.item(i, col).text()
                for col in (0, 1, 2)
                if self.table.item(i, col) is not None
            )
            match = (not query or query in normalize(haystack)) and \
                self._row_passes_filter(i)
            self.table.setRowHidden(i, not match)
            if match:
                visible += 1
        self._visible = visible
        self._update_count_label()

    def _update_count_label(self) -> None:
        visible = getattr(self, "_visible", self._total)
        text = (
            f"{visible} tomo(s)" if visible == self._total
            else f"{visible} de {self._total} tomo(s)"
        )
        text += (
            f" · obtenidos: {self.db.flag_count('poseido')}"
            f" · deseados: {self.db.flag_count('deseado')}"
        )
        self.lbl_count.setText(text)


# ----------------------------------------------------------------------
# Ventana principal
# ----------------------------------------------------------------------
class MainWindow(_EdgeResizeMixin, _ShadowFrameMixin, QMainWindow):
    """Ventana principal sin marco + icono de bandeja del sistema."""

    # Puente thread-safe hacia el globo de la bandeja: el monitor emite
    # desde su hilo y Qt entrega en el hilo de la GUI (conexión en cola).
    tray_message = Signal(str, str)

    def __init__(self, config: Config, db: Database) -> None:
        super().__init__()
        self.config = config
        self.db = db
        self.monitor: Optional[ImapMonitor] = None
        self._really_quit = False

        self.setWindowTitle("Monitor BCG — Biblioteca Clásica Gredos")
        self.setWindowIcon(_make_icon())
        # MEDIDO, no a ojo (2026-08-08): por debajo de 534 px de ancho
        # los botones bajan de su `minimumSizeHint` y por debajo de 570
        # de alto se recorta el contenido. Se deja un margen corto sobre
        # esos dos suelos y nada más.
        self.resize(555, 590)
        self.setMinimumSize(545, 578)

        # Identidad propia en Windows: sin esto, la barra de tareas agrupa
        # la ventana bajo "python.exe" (o el host genérico) y no usa nuestro
        # icono. Debe fijarse antes de mostrar la ventana. El mismo AUMID
        # se usa en notification.py para los toasts.
        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    notification.APP_ID
                )
            except Exception as exc:  # noqa: BLE001 - nunca impedir el arranque
                logger.debug("No se pudo fijar el AppUserModelID: %s", exc)

        # Encuadernación: la hoja de estilos se aplica a toda la app para
        # que los diálogos hereden el mismo diseño.
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(GREDOS_QSS)
            # La fuente de la aplicación, con su suavizado. La hoja de
            # estilos fija la FAMILIA, pero no el alisado ni el ajuste a
            # rejilla: eso solo viaja en un QFont de verdad.
            app.setFont(fuente(10.5))

        self._build_ui()
        self._build_tray()
        self.tray_message.connect(self._show_tray_message)
        self._run_health_check()
        self._update_tray_icon(active=False)

        # Resumen diario: comprobación cada minuto (barata); el envío
        # real solo ocurre una vez al día pasada la hora configurada.
        self._summary_timer = QTimer(self)
        self._summary_timer.setInterval(60_000)
        self._summary_timer.timeout.connect(self._maybe_daily_summary)
        self._summary_timer.start()

        # Recolector de ciclos SIEMPRE en el hilo de la GUI: si salta en
        # el hilo del monitor puede destruir objetos Qt con
        # temporizadores vivos desde el hilo equivocado
        # ("QBasicTimer::stop ... different thread" → cierre, 2026-07-25).
        import gc

        self._gc_timer = QTimer(self)
        self._gc_timer.setInterval(30_000)
        self._gc_timer.timeout.connect(lambda: gc.collect())
        self._gc_timer.start()

        # El redimensionado por bordes lo aporta _EdgeResizeMixin (el
        # filtro global se instala al mostrarse y se retira al ocultarse).

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer, root = _wrap_chrome(
            self, "Monitor BCG", minimizable=True, maximizable=True
        )
        central.setLayout(outer)

        # --- Portada: doble filete + título dorado con halo ---------------
        root.addWidget(_filete(2))
        root.addSpacing(2)
        root.addWidget(_filete(1))

        titulo = QLabel("BIBLIOTECA CLÁSICA GREDOS")
        titulo.setObjectName("titulo")
        titulo.setFont(fuente(18, negrita=True, espaciado=3.0))
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _gold_glow(titulo, blur=26, alpha=150)  # halo de pan de oro
        root.addWidget(titulo)

        root.addWidget(_filete(1))
        root.addSpacing(4)

        # --- Panel de estado: marco dorado + pespunte de "bordado" --------
        panel = QFrame()
        panel.setObjectName("panel")
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(5, 5, 5, 5)

        stitch = QFrame()
        stitch.setObjectName("stitch")
        grid = QGridLayout(stitch)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setVerticalSpacing(8)
        panel_lay.addWidget(stitch)

        font_v = fuente(12)

        def stat_row(row: int, label: str) -> QLabel:
            lbl = QLabel(label)
            value = QLabel("—")
            value.setFont(font_v)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(lbl, row, 0)
            grid.addWidget(value, row, 1)
            return value

        self.lbl_status = stat_row(0, "Estado:")
        self.lbl_last = stat_row(1, "Última revisión:")
        self.lbl_checked = stat_row(2, "Correos revisados:")
        self.lbl_alerts = stat_row(3, "Alertas enviadas:")
        self.lbl_lots = stat_row(4, "Lotes detectados:")

        self.lbl_status.setText("🔴 Monitor detenido")
        self.lbl_last.setText("—")
        self.lbl_checked.setText("0")
        self.lbl_alerts.setText("0")
        self.lbl_lots.setText("0")
        root.addWidget(panel)

        root.addStretch(1)

        # --- Pasaje del día -----------------------------------------------
        # ENCIMA de la botonera y con aire arriba y abajo: no es una
        # acción más de la lista, es una invitación a leer.
        root.addSpacing(8)
        self.btn_dia = BotonDelDia("PASAJE DEL DÍA")
        self.btn_dia.setToolTip(
            "Un pasaje de la colección, elegido al azar.\n"
            "El mismo todo el día; a las doce de la noche, otro."
        )
        self.btn_dia.clicked.connect(self.open_pasaje_del_dia)
        root.addWidget(self.btn_dia)
        root.addSpacing(10)

        # --- Botonera: rejilla 3×3, etiquetas cortas que siempre caben ----
        self.btn_start = GlowButton("Iniciar")
        self.btn_stop = GlowButton("Detener")
        self.btn_stop.setEnabled(False)
        btn_config = GlowButton("Configuración")
        btn_history = GlowButton("Historial")
        btn_notified = GlowButton("Notificaciones")
        btn_prices = GlowButton("Precios")
        btn_lotes = GlowButton("Lotes")
        btn_collection = GlowButton("Colección")
        btn_textos = GlowButton("Textos")

        self.btn_start.clicked.connect(self.start_monitor)
        self.btn_stop.clicked.connect(self.stop_monitor)
        btn_config.clicked.connect(self.open_config)
        btn_history.clicked.connect(self.open_history)
        btn_notified.clicked.connect(self.open_notified)
        btn_prices.clicked.connect(self.open_price_history)
        btn_lotes.clicked.connect(self.open_lotes)
        btn_collection.clicked.connect(self.open_collection)
        btn_textos.clicked.connect(self.open_textos)

        grid_btns = QGridLayout()
        grid_btns.setHorizontalSpacing(8)
        grid_btns.setVerticalSpacing(8)
        ordered = (
            self.btn_start, self.btn_stop, btn_config,
            btn_history, btn_notified, btn_prices,
        )
        for i, b in enumerate(ordered):
            grid_btns.addWidget(b, i // 3, i % 3)
        grid_btns.addWidget(btn_collection, 2, 0)
        grid_btns.addWidget(btn_lotes, 2, 1)
        grid_btns.addWidget(btn_textos, 2, 2)
        root.addLayout(grid_btns)

        # --- Buscador dentro del TEXTO de los tomos -----------------------
        # Para buscar un tomo por autor o título está el filtro de la
        # propia ventana de Colección; aquí se busca dentro de lo que
        # DICE el libro.
        root.addSpacing(6)
        en_textos = QHBoxLayout()
        en_textos.setSpacing(8)
        en_textos.addWidget(QLabel("Buscar en los textos:"))
        self.ed_textos = GlowLineEdit()
        self.ed_textos.setPlaceholderText(
            'una frase del libro, o "entre comillas" la frase exacta'
        )
        self.ed_textos.setClearButtonEnabled(True)
        self.ed_textos.setToolTip(
            "Busca dentro del texto de los tomos ya analizados y\n"
            "devuelve el pasaje con su tomo, su obra y su página.\n"
            "Todo local: no sale nada de este equipo."
        )
        self.ed_textos.returnPressed.connect(self.open_buscar_textos)
        en_textos.addWidget(self.ed_textos, 1)
        btn_textos_buscar = GlowButton("Buscar")
        btn_textos_buscar.clicked.connect(self.open_buscar_textos)
        en_textos.addWidget(btn_textos_buscar)
        root.addLayout(en_textos)

        root.addSpacing(2)
        root.addWidget(_filete(1))

        # Línea de mensajes (sustituye a la QStatusBar del marco antiguo)
        self.lbl_message = QLabel("")
        self.lbl_message.setObjectName("mensaje")
        self.lbl_message.setWordWrap(True)
        root.addWidget(self.lbl_message)

    def _build_tray(self) -> None:
        """Icono de bandeja: la app vive aquí aunque se cierre la ventana."""
        self.tray = QSystemTrayIcon(_make_icon(), self)
        self.tray.setToolTip("Monitor BCG")

        menu = QMenu()
        act_show = QAction("Abrir ventana", self)
        act_show.triggered.connect(self.show_window)
        act_quit = QAction("Salir", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _show_tray_message(self, title: str, message: str) -> None:
        """Globo de la bandeja (último recurso de las notificaciones)."""
        self.tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 10000
        )

    def _show_status(self, message: str) -> None:
        self.lbl_message.setText(message)

    # ------------------------------------------------------------------
    # Diagnóstico de arranque e icono de bandeja con estado
    # ------------------------------------------------------------------
    def _run_health_check(self) -> None:
        """Verificación visible al arrancar; detalle en la ventana y el log."""
        from app import collection as col
        cred_ok = bool(
            "@" in self.config.email_user
            and self.config.email_password
            and self.config.email_password != "contraseña_de_aplicacion"
        )
        # Importarlo de verdad, no mirar si el archivo está: lo que
        # interesa es si el parser de nivel 1 va a poder usarse.
        try:
            bs4_ok = bool(importlib.import_module("bs4"))
        except ImportError:
            bs4_ok = False
        # Arranque con Windows: no basta con que ESTÉ puesto, tiene que
        # apuntar a ESTA carpeta. Al mover el proyecto, la clave Run se
        # queda con la ruta vieja y Windows lanza un archivo que ya no
        # existe sin decir nada (2026-08-13). Si el usuario no lo quiere
        # activado, no hay nada que comprobar y el diagnóstico va bien.
        quiere_arranque = self.config.start_with_windows or autostart.is_enabled()
        arranque_ok = not quiere_arranque or autostart.apunta_aqui()
        checks = (
            ("credenciales", cred_ok),
            ("Excel colección", col.DEFAULT_XLSX.exists()),
            ("colección importada", self.db.tomos_count() > 0),
            ("avisos Windows", toast_available()),
            ("parser HTML", bs4_ok),
            ("arranque con Windows", arranque_ok),
        )
        texto = "Diagnóstico: " + " · ".join(
            f"{'✔' if ok else '✖'} {nombre}" for nombre, ok in checks
        )
        self._show_status(texto)
        fallos = [nombre for nombre, ok in checks if not ok]
        if fallos:
            logger.warning("Diagnóstico con fallos: %s", ", ".join(fallos))
        else:
            logger.info("Diagnóstico de arranque: todo correcto.")

    def _update_tray_icon(self, active: Optional[bool] = None) -> None:
        """
        Icono de bandeja con estado: SOLO el punto verde (monitor
        activo) o rojo (detenido) — sin contador de alertas (se probó y
        el usuario lo quitó, 2026-07-26).
        """
        if active is None:
            active = self.monitor is not None and self.monitor.isRunning()

        pm = QPixmap(_make_icon().pixmap(64, 64))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setBrush(QColor("#2ecc71") if active else QColor("#c0392b"))
        painter.setPen(QPen(QColor(AZUL_OXFORD_OSCURO), 2))
        painter.drawEllipse(42, 42, 20, 20)
        painter.end()
        self.tray.setIcon(QIcon(pm))
        self.tray.setToolTip(
            f"Monitor BCG — {'activo' if active else 'detenido'}"
        )

    # ------------------------------------------------------------------
    # Resumen diario
    # ------------------------------------------------------------------
    def _maybe_daily_summary(self) -> None:
        """
        Envía el resumen del día una sola vez, pasada la hora configurada.

        La fecha del último resumen se persiste en la tabla `meta`, así
        que reiniciar el programa no lo duplica. Días sin actividad no
        generan notificación (pero sí se marcan como resueltos).
        """
        if not self.config.daily_summary_enabled:
            return
        target = QTime.fromString(self.config.daily_summary_time, "HH:mm")
        if not target.isValid() or QTime.currentTime() < target:
            return
        from datetime import date

        today = date.today().isoformat()
        if self.db.get_meta("last_summary_date") == today:
            return
        self.db.set_meta("last_summary_date", today)

        s = self.db.summary_for_day(today)
        total = s["notificados"] + s["ignorados"] + s["lotes"]
        if total == 0:
            logger.info("Resumen diario: sin actividad hoy; no se notifica.")
            return

        lines = [
            f"Alertas notificadas: {s['notificados']}",
            f"Ignoradas por umbral: {s['ignorados']}",
            f"Lotes detectados: {s['lotes']}",
        ]
        if s["mejor_titulo"]:
            lines.append(
                f"Mejor descuento: {s['mejor_descuento']:.0f} % — {s['mejor_titulo']}"
            )
        lines.append("Pulsa para ver el historial")
        from app import deeplink

        Notifier(
            enable_sound=self.config.enable_sound,
            auto_open_link=False,
            fallback=self.tray_message.emit,
        ).notify_info(
            "📚 Resumen del día — Monitor BCG",
            "\n".join(lines),
            # Al pulsar el aviso, la aplicación sale de la bandeja y
            # abre el Historial (2026-08-02).
            link=deeplink.enlace("historial"),
        )
        logger.info("Resumen diario enviado.")

    # ------------------------------------------------------------------
    # Control del monitor
    # ------------------------------------------------------------------
    def start_monitor(self) -> None:
        """Arranca el hilo de vigilancia IMAP."""
        if self.monitor is not None and self.monitor.isRunning():
            return

        notifier = Notifier(
            enable_sound=self.config.enable_sound,
            auto_open_link=self.config.auto_open_link,
            fallback=self.tray_message.emit,  # globo de bandeja si todo falla
        )
        self.monitor = ImapMonitor(self.config, self.db, notifier)
        self.monitor.status_changed.connect(self.lbl_status.setText)
        self.monitor.checked.connect(self.lbl_last.setText)
        self.monitor.mail_processed.connect(
            lambda n: self.lbl_checked.setText(str(n))
        )
        self.monitor.alert_sent.connect(lambda n: self.lbl_alerts.setText(str(n)))
        self.monitor.lot_detected.connect(lambda n: self.lbl_lots.setText(str(n)))
        self.monitor.error_occurred.connect(self._on_error)
        self.monitor.finished.connect(self._on_monitor_finished)
        self.monitor.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._update_tray_icon(active=True)
        logger.info("Monitor arrancado desde la GUI.")

    def stop_monitor(self) -> None:
        """
        Detiene el hilo de vigilancia SIN bloquear la interfaz.

        Se solicita la parada (que aborta el socket al instante), se
        muestra "Deteniendo…" y los botones se restauran cuando el hilo
        emite `finished`.
        """
        if self.monitor is None or not self.monitor.isRunning():
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.lbl_status.setText("🔴 Monitor detenido")
            return
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText("🟠 Deteniendo…")
        self.monitor.stop()
        logger.info("Parada del monitor solicitada desde la GUI.")

    def _on_monitor_finished(self) -> None:
        """El hilo del monitor ha terminado: restaurar la botonera."""
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._update_tray_icon(active=False)
        logger.info("Monitor detenido desde la GUI.")

    def _on_error(self, message: str) -> None:
        """Muestra los errores del monitor en la línea de mensajes."""
        self._show_status(message)

    # ------------------------------------------------------------------
    # Diálogos
    # ------------------------------------------------------------------
    def open_config(self) -> None:
        # El guardado ocurre en ConfigDialog.accept() (WA_DeleteOnClose:
        # los widgets ya no existen tras exec()).
        dialog = ConfigDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.monitor is not None and self.monitor.isRunning():
                GredosMessageBox.show_info(
                    self,
                    "Configuración",
                    "La nueva configuración se aplicará al reiniciar el monitor "
                    "(Detener → Iniciar).",
                )

    def abrir_seccion(self, seccion: str) -> None:
        """
        Abre la ventana por una sección concreta.

        Lo usan los enlaces `bcgmonitor://…` de las notificaciones: al
        pulsar el resumen del día, la aplicación sale de la bandeja y
        muestra el Historial (2026-08-02).
        """
        self.show_window()
        acciones = {
            "historial": self.open_history,
            "notificadas": self.open_notified,
            "precios": self.open_price_history,
            "lotes": self.open_lotes,
            "coleccion": self.open_collection,
            "textos": self.open_textos,
            "buscar": self.open_buscar_textos,
        }
        accion = acciones.get(seccion)
        if accion is not None:
            # Con un respiro: primero se ve la ventana y luego el
            # diálogo, que es modal y se comería el repintado.
            QTimer.singleShot(120, accion)

    def open_history(self) -> None:
        HistoryDialog(self.db, self).exec()

    def open_notified(self) -> None:
        """Lista SOLO las ofertas que cumplieron condiciones y se notificaron."""
        HistoryDialog(
            self.db, self, estado="notificado", titulo="Ofertas notificadas"
        ).exec()

    def open_price_history(self) -> None:
        PriceHistoryDialog(self.db, self).exec()

    def open_lotes(self) -> None:
        LotesDialog(self.db, self).exec()

    def open_thresholds(self) -> None:
        ThresholdsDialog(self.db, self).exec()

    def open_dataset_stats(self) -> None:
        DatasetDialog(self).exec()

    def open_collection(self) -> None:
        CollectionDialog(self.db, self).exec()

    def open_textos(self) -> None:
        """Seguimiento del texto extraído de cada tomo."""
        TextosDialog(self.db, self).exec()

    def open_buscar_textos(self) -> None:
        """Busca dentro del texto de los tomos ya analizados."""
        BuscarTextosDialog(self.ed_textos.text().strip(), self).exec()

    def open_pasaje_del_dia(self) -> None:
        """
        El pasaje del día: el mismo hasta las doce de la noche.

        La fecha se lee AQUÍ y se le pasa al índice, que guarda la
        elección: así el pasaje va por FECHA y cambia solo al cambiar el
        día. El único reloj está dentro del diálogo, y es para la
        ventana que se queda abierta pasada la medianoche.
        """
        from datetime import date

        from app import rag

        try:
            indice = rag.indice_compartido()
        except rag.RagError as exc:
            self._show_status(str(exc))
            return
        ficha = indice.pasaje_del_dia(date.today().isoformat())
        if not ficha:
            self._show_status(
                "Aún no hay textos indexados: abre «Buscar en los textos» "
                "y pulsa «Actualizar índice»."
            )
            return
        PasajeDelDiaDialog(ficha, self).exec()

    def open_debug_panel(self) -> None:
        """
        Abre debug_panel.py (Tkinter) en un proceso aparte, sobre el
        último correo procesado (last_email.eml, guardado por el monitor).
        """
        if getattr(sys, "frozen", False):
            GredosMessageBox.show_info(
                self,
                "Panel de depuración",
                "En la versión empaquetada, ejecuta el panel desde el código "
                "fuente:\n\n    python tools/debug_panel.py",
            )
            return
        script = app_dir() / "tools" / "debug_panel.py"
        if not script.exists():
            GredosMessageBox.show_info(
                self, "Panel de depuración", f"No se encontró {script}."
            )
            return
        eml = app_dir() / LAST_EMAIL_FILENAME
        ok = QProcess.startDetached(sys.executable, [str(script), str(eml)])
        if not ok:
            GredosMessageBox.show_info(
                self, "Panel de depuración", "No se pudo lanzar el panel."
            )

    # ------------------------------------------------------------------
    # Bandeja del sistema y ciclo de vida
    # ------------------------------------------------------------------
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - API Qt
        """
        Cerrar la ventana la oculta a la bandeja; el monitor sigue vivo.

        Sin globo de aviso: el usuario ya sabe que queda en segundo
        plano (petición 2026-07-25 — no reintroducir el showMessage).
        """
        if self._really_quit:
            event.accept()
            return
        event.ignore()
        self.hide()

    def quit_app(self) -> None:
        """Salida real de la aplicación (desde el menú de la bandeja)."""
        self._really_quit = True
        if self.monitor is not None and self.monitor.isRunning():
            self.monitor.stop()
            # El stop() aborta el socket, así que el hilo muere en <1 s;
            # espera breve y acotada para cerrar la BD sin carreras.
            self.monitor.wait(3000)
        self.db.close()
        self.tray.hide()
        QApplication.quit()
