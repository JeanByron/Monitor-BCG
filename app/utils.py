"""
utils.py
========
Utilidades de análisis (parsing) de correos de Todocolección.

Aquí vive toda la lógica "sucia" de extraer información de un correo:

- Decodificar asuntos y cuerpos (texto plano y HTML).
- Detectar si un correo es de Todocolección y si habla de precios/favoritos.
- Extraer título del libro, precio anterior, precio nuevo y enlace al anuncio.
- Extraer la URL de la imagen de portada del anuncio.
- Calcular el porcentaje de descuento cuando el correo no lo incluye.

Como el formato exacto de los correos puede variar, el parseo es
deliberadamente tolerante: usa una CASCADA DE CONFIANZA con varias
estrategias independientes:

    1. Parser HTML especializado (BeautifulSoup): localiza los elementos
       característicos del correo de Todocolección (botón "Ver", precio
       tachado, precio grande, encabezado "Descuento del XX%", imagen de
       portada, bloque de título junto a la imagen).
    2. Parser semántico sobre el texto extraído del HTML (frases como
       "Descuento del 90%" o "¡90% de descuento!").
    3. Expresiones regulares genéricas (precios, porcentajes, URLs).
    4. Heurísticas originales (mayor importe = precio antiguo, etc.).

Cada campo se rellena con la primera estrategia que tenga éxito, de modo
que si Todocolección cambia ligeramente el diseño del correo, los niveles
inferiores siguen funcionando sin tocar el código.

Todos los libros del usuario pertenecen a la Biblioteca Clásica Gredos,
así que NO clasificamos: cualquier bajada de precio detectada se
considera un tomo de la colección.

Dependencia opcional
--------------------
El nivel 1 de la cascada usa BeautifulSoup4 (`pip install beautifulsoup4`).
Si bs4 no está instalado, el módulo sigue funcionando: simplemente se
salta el parser especializado y cae a los niveles 2-4 (el comportamiento
anterior), dejando constancia en el log.
"""

from __future__ import annotations

import email
import email.header
import email.message
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# BeautifulSoup es opcional: sin él, la cascada empieza en el nivel 2.
try:  # pragma: no cover - depende del entorno
    from bs4 import BeautifulSoup, Tag

    _BS4_AVAILABLE = True
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]
    Tag = None  # type: ignore[assignment]
    _BS4_AVAILABLE = False


# ----------------------------------------------------------------------
# Estructura resultado del parseo
# ----------------------------------------------------------------------
@dataclass
class PriceAlert:
    """Información extraída de un correo de bajada de precio."""

    title: str                       # Título del libro/anuncio
    old_price: Optional[float]      # Precio anterior en euros (None si no se pudo extraer)
    new_price: Optional[float]      # Precio nuevo en euros
    discount_percent: Optional[float]  # % de descuento (calculado si es necesario)
    link: Optional[str]             # Enlace al anuncio
    cover_image_url: Optional[str] = None  # URL de la portada (para la notificación)
    confidence: float = 0.0         # Confianza del parseo, entre 0 y 1
    # Estrategia utilizada para cada campo (para depuración e informes):
    # p. ej. {"link": "HTML especializado (botón 'Ver')", "title": "heurística"...}
    sources: dict = field(default_factory=dict)

    # Cuánto pueden discrepar el descuento anunciado y el que sale de los
    # precios. Todocolección redondea, así que un par de puntos es
    # normal; más que eso significa que los datos NO son del mismo
    # anuncio.
    MARGEN_COHERENCIA = 3.0

    def es_coherente(self) -> bool:
        """
        ¿Cuadran entre sí los dos precios y el porcentaje?

        Si el correo dice «−35 %» pero los precios extraídos son 40 € →
        8,91 € (que es un −77,7 %), es que cada dato viene de un anuncio
        distinto y NO se puede notificar nada. Pasó de verdad el
        2026-08-07 con el carrusel de «Te puede interesar».
        """
        if (
            self.old_price is None
            or self.new_price is None
            or self.discount_percent is None
            or self.old_price <= 0
        ):
            return True                      # sin los tres, nada que cotejar
        real = (self.old_price - self.new_price) / self.old_price * 100
        return abs(real - self.discount_percent) <= self.MARGEN_COHERENCIA

    def is_reliable(self) -> bool:
        """
        True si los datos bastan para NOTIFICAR sin riesgo de basura.

        Fiable = ambos precios extraídos, o porcentaje procedente de una
        frase semántica de Todocolección ("Descuento del X%"). Un
        porcentaje pescado por la regex genérica SIN precios reales
        ("100 % seguro", "60 % de coleccionistas...") NO es una oferta:
        así se notificaron falsos "100 %" desde boletines (2026-07-24).

        Y dos guardas más, del 2026-08-07:

        - Si el correo menciona VARIOS anuncios, no hay un aviso que
          valga: los importes pueden ser de productos distintos. Todos
          los avisos reales guardados traen uno solo.
        - Los tres números han de CUADRAR entre sí, venga cada uno de
          donde venga.
        """
        if self.sources.get("varios_anuncios"):
            logger.info(
                "Aviso descartado: %s, así que los importes pueden ser de "
                "anuncios distintos.", self.sources["varios_anuncios"],
            )
            return False
        if not self.es_coherente():
            logger.info(
                "Aviso descartado por incoherencia: %s € → %s € no es un "
                "%s %%.", self.old_price, self.new_price,
                self.discount_percent,
            )
            return False
        if self.old_price is not None and self.new_price is not None:
            return True
        src = self.sources.get("discount_percent", "")
        return src.startswith("parser semántico") or src == "calculado desde precios"

    def compute_discount(self) -> None:
        """Calcula el descuento a partir de los precios si aún no se conoce."""
        if self.discount_percent is None and self.old_price and self.new_price is not None:
            if self.old_price > 0:
                self.discount_percent = round(
                    (self.old_price - self.new_price) / self.old_price * 100, 1
                )


# ----------------------------------------------------------------------
# Normalización de texto
# ----------------------------------------------------------------------
def normalize(text: str) -> str:
    """Pasa a minúsculas y elimina tildes para comparar palabras clave."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def decode_header_value(value: Optional[str]) -> str:
    """Decodifica una cabecera MIME (asunto, remitente...) a texto plano."""
    if not value:
        return ""
    parts: list[str] = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                # Codificación que Python no conoce ("unknown-8bit" y
                # compañía): mejor leerlo como utf-8 tolerante que dejar
                # que reviente el procesado del correo (2026-08-02).
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


# ----------------------------------------------------------------------
# Extracción del cuerpo del mensaje
# ----------------------------------------------------------------------
class _HTMLTextExtractor(HTMLParser):
    """Convierte HTML en texto plano y recopila los enlaces (<a href>)."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0  # dentro de <script>/<style>

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val:
                    self.links.append(val)
        if tag in ("br", "p", "div", "tr", "li", "td"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self.text_parts)
        # Compactar espacios manteniendo los saltos de línea
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


# Rótulos que abren el CARRUSEL de otros anuncios. Todo lo que va
# detrás pertenece a productos DISTINTOS del que trata el correo.
#
# Sin acentos a propósito: se busca sobre el HTML crudo, donde no se
# puede normalizar sin descolocar las posiciones. Las variantes con
# tilde («También te puede interesar») contienen igualmente el núcleo.
_MARCAS_CARRUSEL = (
    "te puede interesar",
    "te pueden interesar",
    "te podria interesar",
    "quiza te interese",
    "productos relacionados",
    "otros lotes del vendedor",
    "otros lotes de este vendedor",
)
_CARRUSEL_RE = re.compile("|".join(_MARCAS_CARRUSEL), re.IGNORECASE)
# Por debajo de esto, cortar dejaría el correo sin su propio anuncio:
# más vale no tocar nada que quedarse sin datos.
_MINIMO_ANTES_DEL_CARRUSEL = 400


def recorta_carrusel(texto: str) -> str:
    """
    Corta el correo donde empieza el carrusel de «Te puede interesar».

    Es la misma trampa que ya estaba documentada para los lotes, pero en
    los PRECIOS, y ahí no había guarda (2026-08-07): el correo «Haz una
    oferta al vendedor» del Heródoto acabó notificando «40 € → 8,91 €,
    35 %» con tres datos de TRES anuncios distintos — 40 € del lote de
    verdad, y 8,91 € y el −35 % de un «HISTORIA DE ESPAÑA» del carrusel.
    Recortado el correo, antes del rótulo solo quedan los importes de
    los anuncios que el correo trata de verdad.
    """
    if not texto:
        return texto
    m = _CARRUSEL_RE.search(texto)
    if m is None or m.start() < _MINIMO_ANTES_DEL_CARRUSEL:
        return texto
    logger.debug(
        "Carrusel recortado: se descartan %d caracteres tras %r.",
        len(texto) - m.start(), m.group(0),
    )
    return texto[:m.start()]


def _extract_payloads(msg: email.message.Message) -> tuple[str, str]:
    """
    Devuelve `(texto_plano, html_crudo)` de un mensaje.

    Recorre todas las partes MIME; ignora adjuntos. Es la base tanto de
    `extract_bodies()` (compatibilidad) como del parser especializado,
    que necesita el HTML sin aplanar.

    Aquí se RECORTA el carrusel de «Te puede interesar»: es el único
    sitio por el que pasan todos los caminos (el filtro de favoritos, el
    parser de precios y el de títulos), así que basta con hacerlo una
    vez.
    """
    plain, html = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001 - tolerante ante correos raros
            logger.debug("No se pudo decodificar una parte MIME: %s", exc)
            continue
        if ctype == "text/plain":
            plain += decoded + "\n"
        elif ctype == "text/html":
            html += decoded + "\n"
    return recorta_carrusel(plain), recorta_carrusel(html)


def extract_bodies(msg: email.message.Message) -> tuple[str, str, list[str]]:
    """
    Devuelve `(texto_plano, texto_desde_html, enlaces_html)` de un mensaje.

    (API pública sin cambios; internamente delega en `_extract_payloads`.)
    """
    plain, html = _extract_payloads(msg)

    links: list[str] = []
    html_text = ""
    if html:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(html)
            html_text = parser.text
            links = parser.links
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error parseando HTML: %s", exc)
    return plain, html_text, links


# ----------------------------------------------------------------------
# Detección de correos relevantes
# ----------------------------------------------------------------------
def is_from_todocoleccion(msg: email.message.Message, sender_filter: str) -> bool:
    """
    Comprueba si el correo procede de Todocolección.

    El usuario confirma que TODOS los correos de ofertas vienen de la
    propia página de Todocolección, así que basta con mirar el remitente.
    """
    sender = normalize(decode_header_value(msg.get("From", "")))
    return normalize(sender_filter) in sender


def is_price_alert(
    subject: str,
    body: str,
    keywords: list[str],
    exclude_keywords: Optional[list[str]] = None,
) -> bool:
    """
    Decide si el correo es un aviso de precio/favoritos DEL USUARIO.

    Orden de decisión:
    1. Exclusiones por ASUNTO (boletines, vendidos, pujas...) → fuera.
    2. Palabra clave en el ASUNTO → dentro (formatos antiguos).
    3. Frase SEMÁNTICA de bajada en el CUERPO ("Descuento del X%",
       "¡X% de descuento!", "ha bajado de precio") → dentro. Es el
       formato real actual: el asunto es solo el título del anuncio.

    El substring suelto "descuento" en el cuerpo NO basta (todos los
    boletines lo llevan — avalancha del 2026-07-24).
    """
    subject_n = normalize(subject)
    if exclude_keywords:
        for kw in exclude_keywords:
            if normalize(kw) in subject_n:
                logger.debug("Correo descartado por exclusión de asunto: %r", kw)
                return False

    for kw in keywords:
        if normalize(kw) in subject_n:
            return True

    # REALIDAD DEL FORMATO (2026-07-25): los avisos de favoritos de
    # Todocolección llevan como asunto SOLO el título del anuncio
    # ("Diálogos II (Gorgias...)"), sin ninguna palabra clave — exigirla
    # en el asunto dejó de detectar TODOS los avisos reales. Rescate: se
    # acepta el correo si el CUERPO contiene una frase SEMÁNTICA fuerte
    # de bajada de precio ("Descuento del 40%", "¡40% de descuento!",
    # "ha bajado de precio"). NUNCA el substring suelto "descuento": esa
    # laxitud dejaba pasar los boletines (bug de 2026-07-24); los tipos
    # de boletín conocidos ya cayeron arriba por exclusión de asunto.
    if body:
        body_n = normalize(body)
        if (
            _DISCOUNT_HEADER_RE.search(body_n)
            or _DISCOUNT_PHRASE_RE.search(body_n)
            or "ha bajado de precio" in body_n
            or "bajada de precio" in body_n
            # Subastas de artículos EN SEGUIMIENTO del usuario: traen
            # precio actual y merecen registro (quedarán "ignorado" si
            # no hay descuento, pero con precio y enlace capturados).
            or "finaliza hoy a las" in body_n
            or "sale a subasta" in body_n
            or "salio a subasta" in body_n
            or "comienza la cuenta atras" in body_n
        ):
            logger.debug(
                "Aviso aceptado por frase semántica en el cuerpo."
            )
            return True

    logger.debug("Correo descartado: sin señal de aviso de favoritos.")
    return False


# ----------------------------------------------------------------------
# Expresiones regulares base (niveles 2-3 de la cascada)
# ----------------------------------------------------------------------
# Precio en formato español: "40 €", "40,50 €", "1.250,00 EUR", "€ 4"
_PRICE_RE = re.compile(
    r"(?:€\s*)?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|eur(?:os?)?)",
    re.IGNORECASE,
)

# Porcentaje explícito: "90 %", "-90%", "descuento del 90%"
_PERCENT_RE = re.compile(r"(?:-\s*)?(\d{1,3}(?:[.,]\d{1,2})?)\s*%")

# Frases semánticas de descuento de Todocolección, por orden de prioridad:
#   1. Encabezado: "Descuento del 90%"
#   2. Cuerpo:     "¡90% de descuento!"
_DISCOUNT_HEADER_RE = re.compile(
    r"descuento\s+del\s+(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.IGNORECASE
)
_DISCOUNT_PHRASE_RE = re.compile(
    r"[¡!]?\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*%\s+de\s+descuento", re.IGNORECASE
)

# Enlace a un anuncio de Todocolección
_TC_LINK_RE = re.compile(r"https?://[^\s\"'<>]*todocoleccion\.[^\s\"'<>]+", re.IGNORECASE)

# Palabras que delatan enlaces/imágenes genéricos (no el anuncio en sí).
# Incluye alojamientos y rutas de FOTOS: un clic en la notificación debe
# abrir el ANUNCIO, nunca la página/visor de la imagen (bug reportado:
# el toast llevaba a la página de la foto en lugar de a la oferta).
_BAD_LINK_WORDS = (
    "unsubscribe", "baja", "ayuda", "facebook", "twitter", "instagram",
    "privacidad", "condiciones", "logo", "app-store", "play.google",
    "soporte", "contacto", "aviso-legal", "cookies",
    "/foto", "/imagen", "/img/", "images.", "cloud10", "static.",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    # Tracking y páginas de cuenta/preferencias (llegaron a abrirse
    # desde notificaciones el 2026-07-24 — jamás son el anuncio):
    "/api/", "track", "idmopen", "randid", "/mitc/", "comunicaciones",
    "notificaciones", "/login", "/registro", "/cuenta",
)

# Forma de URL de un anuncio de Todocolección: identificador ~x########,
# ruta /lote/... o slug terminado en -######. Cualquier otra URL del
# dominio (portada, categorías, tracking) NO es un anuncio.
_AD_URL_RE = re.compile(r"~x\d{6,}|/lote/|-\d{6,}")
_BAD_IMG_WORDS = (
    "logo", "pixel", "spacer", "track", "beacon", "icon", "facebook",
    "twitter", "instagram", "youtube", "whatsapp", "boton", "button",
    "flecha", "arrow", "estrella", "star",
)

# Textos típicos del botón de llamada a la acción (normalizados, sin tildes)
_CTA_TEXTS = (
    "ver", "ver anuncio", "ver articulo", "ver lote", "ver oferta",
    "ver ahora", "ir al anuncio", "ver el anuncio",
)


def _parse_es_number(num: str) -> float:
    """Convierte '1.250,50' o '40,5' o '40.5' a float."""
    num = num.strip()
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    return float(num)


def extract_prices(text: str) -> list[float]:
    """Extrae todos los importes en euros que aparecen en el texto, en orden."""
    prices: list[float] = []
    for m in _PRICE_RE.finditer(text):
        try:
            prices.append(_parse_es_number(m.group(1)))
        except ValueError:
            continue
    return prices


# "Precio actual: 120,00 €" — usado en avisos de pujas y similares
_CURRENT_PRICE_RE = re.compile(
    r"precio\s+actual[^0-9€]{0,12}(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*€",
    re.IGNORECASE,
)


def extract_current_price(text: str) -> Optional[float]:
    """
    Precio "actual" de un correo, para registrarlo aunque se descarte.

    1. La cifra que sigue a "Precio actual" (pujas: la primera cifra
       suele ser la puja ajena, no el precio del artículo).
    2. Si no existe esa frase, el primer importe del texto.
    """
    m = _CURRENT_PRICE_RE.search(normalize(text))
    if m:
        try:
            return _parse_es_number(m.group(1))
        except ValueError:
            pass
    prices = extract_prices(text)
    return prices[0] if prices else None




def extract_link(text: str, html_links: list[str]) -> Optional[str]:
    """
    Devuelve el mejor enlace al anuncio (heurística genérica, nivel 3-4).

    REGLA DURA: solo se aceptan URLs con FORMA de anuncio (`_AD_URL_RE`:
    ~x########, /lote/..., slug-######). Antes se devolvía "el enlace
    más largo de todocoleccion.net" y salían enlaces de tracking
    (/api/sistema/track?...) o de preferencias (/mitc/comunicaciones):
    mejor SIN enlace que un enlace que no lleva al producto.
    """
    candidates: list[str] = []
    for href in html_links:
        if "todocoleccion" in href.lower():
            candidates.append(href)
    # También buscar URLs en el texto plano
    candidates.extend(_TC_LINK_RE.findall(text))

    good = [
        c for c in candidates
        if _AD_URL_RE.search(c.lower()) and not _is_generic_link(c)
    ]
    if good:
        # Entre anuncios válidos, el más largo suele ser el directo
        return max(good, key=len)
    return None


def extract_title(subject: str, body: str) -> str:
    """
    Intenta extraer el título del libro (heurística genérica, nivel 2-4).

    Estrategias en orden:
    1. Texto entre comillas en el asunto o el cuerpo ("Vidas Paralelas II").
       (Se mantiene por compatibilidad con formatos antiguos, aunque los
       correos actuales no usan comillas.)
    2. Asunto sin las coletillas típicas de Todocolección.
    3. Primera línea "significativa" del cuerpo.
    """
    # 1) Texto entre comillas (las comillas suelen envolver el título)
    for source in (subject, body):
        m = re.search(r"[\"“«']([^\"”»']{6,120})[\"”»']", source)
        if m:
            return m.group(1).strip()

    # 2) Limpiar el asunto de frases habituales de notificación
    cleaned = subject
    for pat in (
        r"(?i)todocolecci[oó]n[:\-\s]*",
        r"(?i)bajada de precio( en)?( tu)?( art[ií]culo)?( favorito)?[:\-\s]*",
        r"(?i)cambio de precio( en)?[:\-\s]*",
        r"(?i)uno de tus favoritos[:\-\s]*",
        r"(?i)ha bajado de precio[.!]?",
        r"(?i)nuevo precio[:\-\s]*",
        r"(?i)descuento del \d{1,3}\s*%[:\-\s]*",
    ):
        cleaned = re.sub(pat, "", cleaned)
    cleaned = cleaned.strip(" -:¡!¿?.")
    if len(cleaned) >= 6:
        return cleaned

    # 3) Primera línea del cuerpo con pinta de título.
    #    Se descartan líneas con precios, URLs y las coletillas típicas
    #    de la notificación ("Bajada de precio", "Descuento del 90%"...).
    _noise = re.compile(
        r"(?i)https?://|bajada de precio|cambio de precio|descuento del"
        r"|de descuento|tus favoritos|nuevo precio|todocolecci[oó]n"
    )
    for line in body.splitlines():
        line = line.strip()
        if (
            6 <= len(line) <= 120
            and not _PRICE_RE.search(line)
            and not _noise.search(line)
        ):
            return line

    return subject.strip() or "Artículo en favoritos"


# ----------------------------------------------------------------------
# Nivel 1: parser HTML especializado (BeautifulSoup)
# ----------------------------------------------------------------------
# Los correos de bajada de precio de Todocolección siguen un patrón:
#   - Encabezado "Descuento del XX%".
#   - Imagen de la portada del libro.
#   - Título del anuncio como bloque de texto junto a la imagen.
#   - Precio nuevo en letra grande + precio antiguo tachado.
#   - Texto "¡XX% de descuento!".
#   - Botón "Ver" que enlaza al anuncio.
#
# Todas las funciones siguientes localizan esos elementos por CONTENIDO,
# ATRIBUTOS y CONTEXTO (nunca por posición fija), para tolerar cambios
# menores en el diseño. Cada una devuelve None si no encuentra nada, y
# entonces la cascada continúa con los niveles inferiores.


def _get_soup(html: str) -> Optional["BeautifulSoup"]:
    """Construye el árbol BeautifulSoup, o None si bs4 no está disponible."""
    if not html:
        return None
    if not _BS4_AVAILABLE:
        logger.warning(
            "BeautifulSoup4 no está instalado: se omite el parser HTML "
            "especializado (pip install beautifulsoup4)."
        )
        return None
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception as exc:  # noqa: BLE001
        logger.debug("BeautifulSoup no pudo parsear el HTML: %s", exc)
        return None


# Tokens que vetan un enlace SIEMPRE (tracking, cuenta, bajas): jamás
# aparecen en la URL de un anuncio legítimo.
_HARD_BAD_LINK_WORDS = (
    "/api/", "idmopen", "randid", "/mitc/", "comunicaciones",
    "notificaciones", "unsubscribe", "/login", "/registro", "/cuenta",
    "/foto", "/imagen", "/img/", "images.", "cloud10", "static.",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
)


def _is_generic_link(href: str) -> bool:
    """
    True si el enlace parece genérico (tracking, redes, pie de página).

    REGLA (bug 2026-07-25): si la URL tiene FORMA DE ANUNCIO
    (~x########...), los tokens "blandos" NO la vetan — "logo" casaba
    dentro de "diáLOGOs" y los anuncios de Platón perdían su enlace.
    Solo los tokens duros (tracking/cuenta) vetan siempre.
    """
    low = href.lower()
    if any(b in low for b in _HARD_BAD_LINK_WORDS):
        return True
    if _AD_URL_RE.search(low):
        return False  # es un anuncio: los tokens blandos no aplican
    return any(b in low for b in _BAD_LINK_WORDS)


def _looks_like_button(tag: "Tag") -> bool:
    """
    Heurística: ¿este <a> (o su contenedor inmediato) parece un botón?

    En los correos HTML los botones suelen ser <a> con fondo de color
    (atributo style/bgcolor) o clases tipo 'btn'/'button', a menudo
    dentro de una celda <td> coloreada.
    """
    def _attrs_hint(t: "Tag") -> bool:
        style = (t.get("style") or "").lower()
        cls = " ".join(t.get("class") or []).lower()
        return (
            "background" in style
            or bool(t.get("bgcolor"))
            or "btn" in cls
            or "button" in cls
        )

    if _attrs_hint(tag):
        return True
    parent = tag.parent
    # Mirar un par de niveles hacia arriba (td/tr/span contenedores)
    for _ in range(2):
        if parent is None or not isinstance(parent, Tag):
            break
        if _attrs_hint(parent):
            return True
        parent = parent.parent
    return False


def extract_cta_link(soup: Optional["BeautifulSoup"]) -> Optional[str]:
    """
    Localiza el enlace del botón "Ver" (llamada a la acción del anuncio).

    Puntuación por evidencia acumulada, no por posición:
      - Texto del enlace igual a "Ver" (o variantes)  → evidencia fuerte.
      - Aspecto de botón (fondo, clase btn...)        → evidencia media.
      - URL con pinta de anuncio (/lote, id numérico) → evidencia media.
    Se descartan siempre los enlaces genéricos (baja, ayuda, redes).
    """
    if soup is None:
        return None

    best_href: Optional[str] = None
    best_score = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "todocoleccion" not in href.lower():
            continue
        if _is_generic_link(href):
            continue
        # REGLA DURA: sin forma de anuncio no hay candidato (evita que
        # un botón apunte a tracking o a páginas de cuenta).
        if not _AD_URL_RE.search(href.lower()):
            continue

        text_n = normalize(a.get_text(" ", strip=True))
        score = 0
        if text_n in _CTA_TEXTS:
            score += 100  # el botón "Ver" es siempre el enlace prioritario
        elif text_n.startswith("ver "):
            score += 60
        if _looks_like_button(a):
            score += 40
        score += 30  # todas las candidatas tienen ya forma de anuncio

        if score > best_score:
            best_score = score
            best_href = href

    return best_href if best_score >= 30 else None


def _int_attr(tag: "Tag", name: str) -> Optional[int]:
    """Lee un atributo numérico tipo width='600' o '600px'."""
    val = tag.get(name)
    if not val:
        return None
    m = re.match(r"\s*(\d+)", str(val))
    return int(m.group(1)) if m else None


def extract_cover_image(
    soup: Optional["BeautifulSoup"], ad_link: Optional[str] = None
) -> Optional[str]:
    """
    Localiza la URL de la imagen de portada del anuncio.

    Evidencias a favor: imagen dentro de un enlace al anuncio, dominio de
    imágenes de Todocolección, dimensiones grandes, texto alternativo
    largo. Se descartan logos, iconos y píxeles de seguimiento.
    """
    if soup is None:
        return None

    best_src: Optional[str] = None
    best_score = 0
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        low = src.lower()
        if any(b in low for b in _BAD_IMG_WORDS):
            continue
        w, h = _int_attr(img, "width"), _int_attr(img, "height")
        # Píxeles de seguimiento e iconos: descartar por tamaño
        if (w is not None and w <= 60) or (h is not None and h <= 60):
            continue

        score = 1
        if re.search(r"todocoleccion|tcdn|cloudfront|imagenes|images|fotos", low):
            score += 20
        parent_a = img.find_parent("a", href=True)
        if parent_a:
            phref = parent_a["href"]
            if ad_link and phref == ad_link:
                score += 40  # la portada enlaza al mismo anuncio que el botón
            elif "todocoleccion" in phref.lower() and not _is_generic_link(phref):
                score += 25
        if w and h:
            score += min((w * h) // 10000, 15)  # premiar tamaño, con tope
        alt = (img.get("alt") or "").strip()
        if len(alt) >= 10:
            score += 10  # las portadas suelen llevar el título en el alt

        if score > best_score:
            best_score = score
            best_src = src

    return best_src if best_score >= 15 else None


def _font_size_px(tag: "Tag") -> float:
    """Tamaño de fuente aproximado en px de un elemento (style o <font size>)."""
    style = (tag.get("style") or "").lower()
    m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(px|pt|em)?", style)
    if m:
        size = float(m.group(1))
        unit = m.group(2) or "px"
        if unit == "pt":
            return size * 4 / 3
        if unit == "em":
            return size * 16
        return size
    if tag.name == "font":
        legacy = _int_attr(tag, "size")
        if legacy:
            return 10 + legacy * 3  # aproximación de la escala 1-7 de <font>
    return 0.0


def _single_price_in(tag: "Tag") -> Optional[float]:
    """Si el texto del elemento contiene exactamente UN precio, lo devuelve."""
    text = tag.get_text(" ", strip=True)
    if len(text) > 40:  # contenedores grandes: no son "el precio"
        return None
    prices = extract_prices(text)
    return prices[0] if len(prices) == 1 else None


def extract_prices_from_html(
    soup: Optional["BeautifulSoup"],
) -> tuple[Optional[float], Optional[float]]:
    """
    Devuelve `(precio_anterior, precio_nuevo)` a partir de la estructura.

    - Precio anterior: importe dentro de un elemento TACHADO
      (<s>, <strike>, <del> o style con text-decoration: line-through).
    - Precio nuevo: importe en el elemento con MAYOR tamaño de fuente
      (el "precio grande" del correo); si no hay tamaños, el importe
      más cercano al tachado que no sea el propio tachado.
    """
    if soup is None:
        return None, None

    old_price: Optional[float] = None
    new_price: Optional[float] = None

    # --- Precio anterior: tachado -------------------------------------
    struck: list[Tag] = list(soup.find_all(["s", "strike", "del"]))
    struck += [
        t for t in soup.find_all(style=re.compile(r"line-through", re.I))
        if t not in struck
    ]
    for el in struck:
        price = _single_price_in(el)
        if price is None:
            # A veces el tachado envuelve algo más de texto
            prices = extract_prices(el.get_text(" ", strip=True))
            price = prices[0] if prices else None
        if price is not None:
            old_price = price
            break

    # --- Precio nuevo: letra grande -----------------------------------
    best_size = 0.0
    for el in soup.find_all(True):
        if not isinstance(el, Tag):
            continue
        size = _font_size_px(el)
        if size <= best_size:
            continue
        # No confundirlo con el propio precio tachado
        if el.find_parent(["s", "strike", "del"]) is not None:
            continue
        if re.search(r"line-through", (el.get("style") or ""), re.I):
            continue
        price = _single_price_in(el)
        if price is not None and price != old_price:
            best_size = size
            new_price = price

    # Sin tamaños de fuente: probar con el "compañero" del tachado
    if new_price is None and old_price is not None and struck:
        container = struck[0].find_parent(["td", "tr", "p", "div"])
        if container is not None:
            prices = [
                p for p in extract_prices(container.get_text(" ", strip=True))
                if p != old_price
            ]
            if prices:
                new_price = prices[0]

    # Coherencia: el precio nuevo debe ser menor que el anterior
    if old_price is not None and new_price is not None and new_price > old_price:
        old_price, new_price = new_price, old_price

    return old_price, new_price


def extract_discount_from_html(text: str) -> tuple[Optional[float], str]:
    """
    Extrae el porcentaje de descuento con prioridad semántica.

    Devuelve `(porcentaje, fuente)` donde `fuente` describe la estrategia
    ganadora (para el log). Orden de prioridad:
      1. "Descuento del XX%"   (encabezado del correo)
      2. "¡XX% de descuento!"  (texto del cuerpo)
      3. Cualquier porcentaje encontrado por regex genérica.
    """
    for regex, source in (
        (_DISCOUNT_HEADER_RE, "encabezado 'Descuento del XX%'"),
        (_DISCOUNT_PHRASE_RE, "texto '¡XX% de descuento!'"),
        (_PERCENT_RE, "regex genérica de porcentaje"),
    ):
        m = regex.search(text)
        if m:
            try:
                value = _parse_es_number(m.group(1))
            except ValueError:
                continue
            if 0 < value <= 100:
                return value, source
    return None, ""


# Textos de navegación/pie de página que NUNCA pueden ser el título.
# Comparación por igualdad exacta (normalizada, sin tildes): palabras
# cortas como "baja" o "blog" solo se rechazan si son TODO el texto,
# para no descartar títulos legítimos que las contengan ("Rebajas...").
_NAV_TEXTS = (
    "cancelar tu suscripcion", "cancelar suscripcion", "darse de baja",
    "baja", "ayuda", "privacidad", "condiciones", "facebook", "twitter",
    "instagram", "linkedin", "pinterest", "youtube", "blog",
    "ver en navegador", "abrir en navegador", "aviso legal", "cookies",
    "contacto", "politica de privacidad",
    # Vistos en notificaciones reales con título erróneo (2026-07-14):
    # enlaces de pie de página y de pujas que colaban como "título".
    "preferencias", "mejorar oferta", "mejora tu oferta",
    "gestionar preferencias", "preferencias de correo",
    "configuracion de comunicaciones", "mis favoritos", "favoritos",
    # Botones del anuncio que se colaban como título (volcado 2026-07-26:
    # dos lotes quedaron llamados "Hacer oferta").
    "hacer oferta", "haz tu oferta", "hacer una oferta", "comprar ahora",
    "anadir a favoritos", "ver anuncio", "ver producto", "ver oferta",
)

# Palabras/frases que invalidan un título aunque solo sean parte de él.
# Solo términos que jamás aparecerían en el título de un libro.
_NAV_WORDS = (
    "suscripcion", "privacidad", "condiciones", "cookies", "facebook",
    "instagram", "twitter", "linkedin", "pinterest", "youtube",
    "darse de baja", "ver en navegador", "abrir en navegador",
    "aviso legal", "unsubscribe",
    # Nunca son parte del título de un libro:
    "preferencias", "mejorar oferta", "mejora tu oferta",
    "configuracion de comunicaciones",
    # Marketing de la app y avisos de visualización (vistos 2026-07-24):
    "app store", "google play", "aplicacion para ios",
    "aplicacion para android", "descarga la app", "descargate la app",
    "si no ves este correo", "version web",
    # Texto de las miniaturas de los correos (visto en Precios 2026-07-26):
    "foto del lote", "fotos del lote", "ver foto", "ver fotos",
    # Botones de la ficha del anuncio (volcado 2026-07-26)
    "hacer oferta", "haz tu oferta", "hacer una oferta",
)

# Tokens de HTML/CSS/maquetación que a veces se cuelan como "texto" en
# correos mal formados y JAMÁS son un título ('center' llegó a
# notificarse como título el 2026-07-24). Comparación exacta normalizada.
_JUNK_TITLE_TOKENS = frozenset({
    "center", "left", "right", "top", "bottom", "middle", "justify",
    "table", "header", "footer", "body", "html", "head", "div", "span",
    "td", "tr", "img", "width", "height", "border", "style", "font",
    "nbsp", "arial", "helvetica", "verdana", "roboto", "sans-serif",
    "serif", "important", "hidden", "block", "inline", "auto", "none",
})

# Texto ALTERNATIVO de las imágenes de Todocolección. No es el título de
# nada: es lo que lee un lector de pantalla. «Foto número 1 del pedido»
# llegó a salir en el Historial y en un toast (2026-08-07).
_ALT_DE_IMAGEN_RE = re.compile(
    r"^(?:foto|imagen|miniatura)\b.*\b(?:del|de\s+la|de)\s+"
    r"(?:lote|pedido|articulo|producto|anuncio)s?$",
    re.IGNORECASE,
)


_ID_DE_ANUNCIO_RE = re.compile(r"~x(\d{6,})")


def titulo_desde_url(url: str) -> str:
    """
    Nombre legible sacado del SLUG del anuncio.

    Es el respaldo cuando el correo no deja el título a mano, y tiene
    una virtud que ninguna heurística del HTML iguala: sale de la propia
    dirección del anuncio, así que SIEMPRE corresponde a él. En los
    correos de ofertas el título vive en una celda suelta, lejos de la
    portada, y el parser acababa poniendo el asunto genérico («Haz una
    oferta al vendedor») en el Historial (2026-08-07).
    """
    from urllib.parse import unquote, urlparse

    if not url:
        return ""
    ruta = unquote(urlparse(url).path or "")
    slug = ruta.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"~x\d+$", "", slug)
    slug = re.sub(r"-\d{6,}$", "", slug)
    slug = slug.replace("-", " ").replace("_", " ").strip()
    return (slug[:1].upper() + slug[1:]) if slug else ""


def _anuncios_distintos(enlaces: list[str], texto: str = "") -> set[str]:
    """
    Identificadores de los anuncios distintos que menciona el correo.

    Un aviso de bajada de precio habla de UN anuncio; los correos de
    ofertas y los boletines enganchan varios, y entonces cualquier
    heurística que junte dos importes está mezclando productos.
    """
    ids: set[str] = set()
    for cadena in list(enlaces or []) + ([texto] if texto else []):
        ids.update(_ID_DE_ANUNCIO_RE.findall(cadena or ""))
    return ids


def _is_nav_text(text: str) -> bool:
    """True si el texto es navegación/maquetación (jamás un título)."""
    text_n = normalize(text.strip())
    if text_n in _NAV_TEXTS or text_n in _JUNK_TITLE_TOKENS:
        return True
    if _ALT_DE_IMAGEN_RE.match(text_n):
        return True
    return any(w in text_n for w in _NAV_WORDS)


def _is_valid_title(text: str) -> bool:
    """
    Validación final de un candidato a título.

    Rechaza textos de navegación, pie de página o cancelación de
    suscripción, además de los filtros básicos (longitud, precios,
    porcentajes y textos del botón CTA).
    """
    text = text.strip()
    if not (6 <= len(text) <= 150):
        return False
    if _PRICE_RE.search(text) or _PERCENT_RE.search(text):
        return False
    text_n = normalize(text)
    if text_n in _CTA_TEXTS or text_n in _NAV_TEXTS:
        return False
    if text_n in _JUNK_TITLE_TOKENS:
        return False
    if any(word in text_n for word in _NAV_WORDS):
        return False
    return True


def _href_points_to_ad(href: str, ad_link: Optional[str]) -> bool:
    """
    True solo si `href` apunta al anuncio (no a navegación/pie de página).

    - Con `ad_link` conocido: debe ser la misma URL o compartir el mismo
      identificador de anuncio (tolera parámetros de tracking distintos).
    - Sin `ad_link`: la URL debe tener pinta de anuncio (/lote/, ~x123...).
    """
    if "todocoleccion" not in href.lower() or _is_generic_link(href):
        return False
    if ad_link:
        if href == ad_link:
            return True
        # Mismo identificador de anuncio aunque cambien los parámetros
        m = re.search(r"(~x\d{6,}|/lote/[\w-]+|-\d{6,})", ad_link)
        return bool(m and m.group(1) in href)
    return bool(re.search(r"/lote/|~x\d{6,}|-\d{6,}", href.lower()))


def extract_title_from_html(
    soup: Optional["BeautifulSoup"],
    ad_link: Optional[str] = None,
    cover_src: Optional[str] = None,
) -> Optional[str]:
    """
    Extrae el título del anuncio aprovechando la estructura del correo.

    El título NUNCA debe provenir de enlaces de navegación ("cancelar tu
    suscripción", "ayuda", redes sociales...): todo candidato pasa por
    `_is_valid_title()` y solo se consideran enlaces cuyo href apunte
    realmente al anuncio (`_href_points_to_ad()`).

    Estrategias, por orden de prioridad:
      1. Texto asociado al enlace DEL ANUNCIO (mismo destino que el
         botón "Ver" o URL con identificador de anuncio).
      2. Bloque de texto situado junto a la portada (misma fila/tabla).
      3. Atributo alt de la imagen de portada.
      4. (Devuelve None → el llamador cae al asunto del correo.)
    """
    if soup is None:
        return None

    # 1) Texto del enlace que apunta al anuncio
    best: Optional[str] = None
    for a in soup.find_all("a", href=True):
        if not _href_points_to_ad(a["href"], ad_link):
            continue
        text = a.get_text(" ", strip=True)
        if _is_valid_title(text) and (best is None or len(text) > len(best)):
            best = text
    if best:
        logger.debug("Título: candidato desde enlace del anuncio.")
        return best.strip()

    # Localizar la portada (la usan las estrategias 2 y 3)
    cover_img: Optional[Tag] = soup.find("img", src=cover_src) if cover_src else None

    # 2) Bloque de texto junto a la portada (misma fila/tabla contenedora)
    if cover_img is not None:
        container = cover_img.find_parent(["tr", "table", "div"])
        if container is not None:
            for chunk in container.stripped_strings:
                if _is_valid_title(chunk):
                    logger.debug("Título: candidato junto a la portada.")
                    return chunk.strip()

    # 3) alt de la portada
    if cover_img is not None:
        alt = (cover_img.get("alt") or "").strip()
        if _is_valid_title(alt):
            logger.debug("Título: candidato desde alt de la portada.")
            return alt

    # 4) Nada razonable: que el llamador use el asunto del correo
    return None


# ----------------------------------------------------------------------
# Parser principal: cascada de confianza
# ----------------------------------------------------------------------
# Pesos de la puntuación de confianza (suman 1.0)
_CONF_LINK = 0.25
_CONF_OLD_PRICE = 0.20
_CONF_NEW_PRICE = 0.20
_CONF_PERCENT = 0.20
_CONF_TITLE = 0.15
_CONF_WARN_THRESHOLD = 0.6


def parse_alert_email(msg: email.message.Message) -> PriceAlert:
    """
    Parsea un correo de Todocolección y devuelve un `PriceAlert`.

    Cascada de confianza (por campo, la primera estrategia que acierta
    gana; las demás quedan como red de seguridad):

        1. Parser HTML especializado (bs4): botón "Ver", precio tachado,
           precio grande, título junto a la portada, imagen de portada.
        2. Parser semántico del texto ("Descuento del XX%", "¡XX% de
           descuento!").
        3. Expresiones regulares genéricas.
        4. Heurísticas originales (dos importes → mayor es el antiguo,
           título desde el asunto, enlace más largo...).

    Además calcula `confidence` (0-1) según cuántos campos se pudieron
    extraer, y deja constancia en el log de la fuente de cada dato.
    """
    subject = decode_header_value(msg.get("Subject", ""))
    plain, raw_html = _extract_payloads(msg)

    # Texto plano derivado del HTML + enlaces (comportamiento original)
    html_text = ""
    html_links: list[str] = []
    if raw_html:
        extractor = _HTMLTextExtractor()
        try:
            extractor.feed(raw_html)
            html_text = extractor.text
            html_links = extractor.links
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error parseando HTML: %s", exc)

    body = plain if len(plain) > len(html_text) else html_text
    full_text = subject + "\n" + body

    # ------------------------------------------------------------------
    # Nivel 1: parser HTML especializado
    # ------------------------------------------------------------------
    soup = _get_soup(raw_html)

    sources: dict[str, str] = {}  # estrategia ganadora por campo

    # --- Enlace -------------------------------------------------------
    link = extract_cta_link(soup)
    if link:
        sources["link"] = "HTML especializado (botón 'Ver')"
        logger.info("Enlace obtenido desde botón 'Ver'.")
    else:
        link = extract_link(full_text, html_links)
        if link:
            sources["link"] = "heurística de enlaces"
            logger.info("Enlace obtenido mediante heurística de enlaces.")
        else:
            logger.info("No se encontró ningún enlace al anuncio.")

    # --- Imagen de portada ---------------------------------------------
    cover_image_url = extract_cover_image(soup, ad_link=link)
    if cover_image_url:
        sources["cover_image_url"] = "HTML especializado"
        logger.info("Imagen de portada obtenida desde HTML.")
    else:
        logger.debug("No se encontró imagen de portada.")

    # --- Precios --------------------------------------------------------
    # ¿Cuántos ANUNCIOS distintos menciona el correo? Un aviso de bajada
    # de precio trae UNO (comprobado en todos los avisos reales
    # guardados). Si trae varios, cualquier pareja de importes puede ser
    # de anuncios diferentes: el correo de ofertas del 2026-08-07 daba
    # "220 € → 40 %", juntando el Flavio Josefo con el Heródoto.
    anuncios = _anuncios_distintos(html_links, full_text)
    varios_anuncios = len(anuncios) > 1
    if varios_anuncios:
        sources["varios_anuncios"] = f"{len(anuncios)} anuncios en el correo"

    old_price, new_price = extract_prices_from_html(soup)
    if old_price is not None:
        sources["old_price"] = "HTML especializado (tachado)"
        logger.info("Precio anterior obtenido desde HTML (elemento tachado).")
    if new_price is not None:
        sources["new_price"] = "HTML especializado (texto grande)"
        logger.info("Precio nuevo obtenido desde HTML (texto destacado).")

    if old_price is None or new_price is None:
        # Nivel 3-4: regex + heurística original "mayor = antiguo"
        prices = extract_prices(full_text)
        if old_price is None and new_price is None:
            if varios_anuncios:
                logger.info(
                    "Sin precios fiables: el correo menciona varios "
                    "anuncios y la heurística mezclaría unos con otros."
                )
            elif len(prices) >= 2:
                old_price, new_price = prices[0], prices[1]
                if new_price > old_price:
                    old_price, new_price = new_price, old_price
                sources["old_price"] = sources["new_price"] = "heurística de texto"
                logger.info("Precios obtenidos mediante heurística de texto.")
            elif len(prices) == 1:
                new_price = prices[0]
                sources["new_price"] = "regex (único precio)"
                logger.info("Solo se encontró un precio en el texto.")
        elif new_price is None and old_price is not None:
            lower = [p for p in prices if p < old_price]
            if lower:
                new_price = max(lower)  # el más cercano por debajo del antiguo
                sources["new_price"] = "heurística de texto"
                logger.info("Precio nuevo completado mediante heurística de texto.")
        elif old_price is None and new_price is not None:
            higher = [p for p in prices if p > new_price]
            if higher:
                old_price = min(higher)
                sources["old_price"] = "heurística de texto"
                logger.info("Precio anterior completado mediante heurística de texto.")

    # --- Porcentaje -----------------------------------------------------
    percent, percent_source = extract_discount_from_html(full_text)
    if percent is not None:
        # Clasificar la fuente: frases semánticas vs. regex genérica
        if "regex" in percent_source:
            sources["discount_percent"] = "regex genérica"
        else:
            sources["discount_percent"] = f"parser semántico ({percent_source})"
        logger.info("Porcentaje obtenido desde %s.", percent_source)
    else:
        logger.debug("Porcentaje no encontrado; se calculará desde los precios.")

    # --- Título ---------------------------------------------------------
    title = extract_title_from_html(soup, ad_link=link, cover_src=cover_image_url)
    if title:
        sources["title"] = "HTML especializado"
        logger.info("Título obtenido desde HTML.")
    else:
        title = extract_title(subject, body)
        sources["title"] = "asunto/heurística de texto"
        logger.info("Título obtenido desde asunto/heurística de texto.")

    # Guardia final: NUNCA aceptar textos de navegación como título
    # ("preferencias", "Mejorar oferta"... — vistos en avisos reales).
    if _is_nav_text(title):
        logger.warning(
            "Título descartado por ser texto de navegación: %r", title
        )
        title = "Artículo en favoritos"
        sources["title"] = "relleno (título de navegación rechazado)"

    # ------------------------------------------------------------------
    # Puntuación de confianza
    # ------------------------------------------------------------------
    confidence = 0.0
    if link:
        confidence += _CONF_LINK
    if old_price is not None:
        confidence += _CONF_OLD_PRICE
    if new_price is not None:
        confidence += _CONF_NEW_PRICE
    if percent is not None:
        confidence += _CONF_PERCENT
    if title and title != "Artículo en favoritos":
        confidence += _CONF_TITLE
    confidence = round(min(confidence, 1.0), 2)

    alert = PriceAlert(
        title=title,
        old_price=old_price,
        new_price=new_price,
        discount_percent=percent,
        link=link,
        cover_image_url=cover_image_url,
        confidence=confidence,
        sources=sources,
    )
    alert.compute_discount()
    if percent is None and alert.discount_percent is not None:
        sources["discount_percent"] = "calculado desde precios"

    logger.info("Parser confidence = %.2f", confidence)
    if confidence < _CONF_WARN_THRESHOLD:
        logger.warning(
            "Confianza baja (%.2f < %.2f) al parsear el correo '%s': "
            "revisar si Todocolección ha cambiado el formato.",
            confidence, _CONF_WARN_THRESHOLD, subject,
        )
    return alert


# ----------------------------------------------------------------------
# Detección de LOTES de libros (5 o más)
# ----------------------------------------------------------------------
# Todocolección envía tanto avisos de precio de anuncios individuales
# como correos que contienen LOTES (varios tomos vendidos juntos) — por
# ejemplo "LOTE DE 7 LIBROS BIBLIOTECA CLÁSICA GREDOS" o
# "HERÓDOTO - HISTORIA - LIBROS I AL IX - 5 TOMOS".
#
# El usuario quiere una notificación específica cuando aparezca un lote
# de `min_lot_books` (5 por defecto) o más libros. Igual que el parser
# de precios, la detección usa una CASCADA de estrategias independientes
# (la evidencia más fuerte gana), de modo que un cambio de formato del
# correo no rompa la detección:
#
#   1. Frase explícita "lote de N libros/tomos/volúmenes/obras..."
#      (número en cifras o en letras: "lote de siete libros").
#   2. Cantidad + unidad: "7 libros", "5 TOMOS", "12 vols.", aunque no
#      aparezca la palabra "lote".
#   3. Rango de tomos en números romanos: "libros I al IX" → 9,
#      "tomos III-VII" → 5.
#
# Cada estrategia aporta un recuento; se toma el MÁXIMO plausible y se
# registra la fuente para el log y el panel de depuración.
#
# NOTA (2026-07-24): existió una estrategia 4 "recuento estructural de
# anuncios en el HTML". Se ELIMINÓ: los boletines de recomendaciones
# listan N anuncios DISTINTOS y se notificaban como falso "lote de N
# libros". Solo el texto del propio anuncio es evidencia de lote.

@dataclass
class LotAlert:
    """Información de un lote de libros detectado en un correo."""

    title: str                        # Título del anuncio/lote
    book_count: int                   # Número de libros estimado
    link: Optional[str] = None        # Enlace al anuncio (si se encontró)
    price: Optional[float] = None     # Precio del lote (si se encontró)
    source: str = ""                  # Estrategia que produjo el recuento
    confidence: float = 0.0           # 0-1 según la fuerza de la evidencia


# Números en letras (español), 2-30: cubre los tamaños de lote reales.
_WORD_NUMBERS = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "treinta": 30,
}
_WORD_NUM_RE = "|".join(_WORD_NUMBERS)

# Unidades que cuentan como "libro" dentro de un lote
_LOT_UNITS = r"(?:libros?|tomos?|vol(?:u|ú)menes|vol(?:umen)?s?\.?|obras?|ejemplares?|t[ií]tulos?)"

# Estrategia 1: "lote de 7 libros", "lote 5 tomos", "lote de siete libros"
_LOT_PHRASE_RE = re.compile(
    rf"lote\s+(?:de\s+)?(\d{{1,3}}|{_WORD_NUM_RE})\s+{_LOT_UNITS}",
    re.IGNORECASE,
)
# Estrategia 2: "7 libros", "5 TOMOS", "12 vols." (sin la palabra lote)
_COUNT_UNIT_RE = re.compile(
    rf"\b(\d{{1,3}}|{_WORD_NUM_RE})\s+{_LOT_UNITS}\b",
    re.IGNORECASE,
)
# (No hay estrategia de rangos romanos: "libros I-VI" es el alcance de
# UN volumen en la Biblioteca Clásica Gredos, no un recuento de libros.)


def _lot_number(token: str) -> Optional[int]:
    """'7' o 'siete' → 7. None si no es un número plausible de libros."""
    token = normalize(token.strip())
    if token.isdigit():
        n = int(token)
    else:
        n = _WORD_NUMBERS.get(token, 0)
    # Límite superior de cordura: nadie vende "1988 libros"; los números
    # de 3+ cifras suelen ser años o números de serie de la colección.
    return n if 2 <= n <= 150 else None


def count_books_in_text(text: str) -> tuple[int, str]:
    """
    Estima cuántos libros menciona un texto (título o cuerpo del correo).

    Devuelve `(recuento, fuente)`; `(0, "")` si no hay evidencia.

    SOLO cuenta la evidencia EXPLÍCITA de cantidad: "lote de N libros" o
    "N tomos". El rango en romanos ("Anales, libros I-VI") NO cuenta:
    así es como la propia Biblioteca Clásica Gredos titula el alcance de
    UN volumen, y tomarlo por un recuento convertía tomos sueltos en
    lotes fantasma — 23 de 26 "lotes" del volcado real del 2026-07-26
    eran un único tomo ("Anales libros I-VI" = tomo 19, no 6 libros), y
    además falseaba el número de los lotes de verdad ("HISTORIAS - 3
    TOMOS - POLIBIO" se registró como lote de 39).
    """
    best, source = 0, ""

    # 1) "lote de N libros" — evidencia máxima
    for m in _LOT_PHRASE_RE.finditer(text):
        n = _lot_number(m.group(1))
        if n and n > best:
            best, source = n, "frase 'lote de N libros'"
    if best:
        return best, source

    # 2) "N libros" / "N tomos" sin la palabra lote
    for m in _COUNT_UNIT_RE.finditer(text):
        n = _lot_number(m.group(1))
        if n and n > best:
            best, source = n, "cantidad + unidad ('N tomos')"

    return best, source


def detect_lot(msg: email.message.Message) -> Optional[LotAlert]:
    """
    Analiza un correo de Todocolección buscando un lote de libros.

    Devuelve un `LotAlert` con el mejor recuento encontrado, o None si
    el correo no aporta ninguna evidencia de lote. El llamador decide si
    el recuento supera el umbral configurado (`min_lot_books`).

    Robustez:
    - Nunca lanza: cualquier error de parseo devuelve None y queda en el log.
    - Solo el TEXTO del anuncio cuenta como evidencia (los boletines con
      varios anuncios distintos NO son un lote).
    """
    try:
        subject = decode_header_value(msg.get("Subject", ""))
        plain, raw_html = _extract_payloads(msg)

        html_text = ""
        if raw_html:
            extractor = _HTMLTextExtractor()
            try:
                extractor.feed(raw_html)
                html_text = extractor.text
            except Exception as exc:  # noqa: BLE001
                logger.debug("detect_lot: error aplanando HTML: %s", exc)

        body = plain if len(plain) > len(html_text) else html_text
        full_text = subject + "\n" + body

        # Título y enlace: reutilizar el parser de precios (tolerante)
        alert = parse_alert_email(msg)

        # --- Recuento SOLO sobre el texto del ANUNCIO -----------------
        # El asunto de un aviso de favoritos ES el título del anuncio;
        # el cuerpo trae además el carrusel de "recomendados", con otros
        # anuncios que hablan de sus propios tomos. Contando el cuerpo,
        # un tomo suelto se convertía en lote fantasma: "HISTORIA -
        # LIBROS I-II - HERODOTO" salía como lote de 9 porque más abajo
        # se anunciaba "LOS NUEVE LIBROS DE LA HISTORIA" (volcado real,
        # 2026-07-26). Con el asunto los cinco casos daban el resultado
        # correcto, incluidos los lotes de verdad ("3 TOMOS - POLIBIO").
        texto_anuncio = "\n".join(p for p in (subject, alert.title) if p)
        count, source = count_books_in_text(texto_anuncio)
        confidence = 0.0
        if count:
            confidence = 0.9 if "lote" in source else 0.7

        if not count:
            return None

        prices = extract_prices(full_text)
        lot = LotAlert(
            title=alert.title,
            book_count=count,
            link=alert.link,
            price=alert.new_price if alert.new_price is not None else (prices[0] if prices else None),
            source=source,
            confidence=round(confidence, 2),
        )
        logger.info(
            "Lote detectado: %d libro(s) [%s] en '%s'",
            lot.book_count, lot.source, lot.title,
        )
        return lot
    except Exception as exc:  # noqa: BLE001 - la detección jamás debe tumbar el monitor
        logger.error("detect_lot: error inesperado: %s", exc)
        return None


# ----------------------------------------------------------------------
# Persistencia de correos (.eml) para depuración y pruebas
# ----------------------------------------------------------------------
# Nombre del archivo donde el monitor guarda el último correo procesado,
# para que el panel de depuración pueda "Reprocesar último correo".
LAST_EMAIL_FILENAME = "last_email.eml"


def save_last_email(raw_bytes: bytes, directory: Union[str, Path] = ".") -> Path:
    """
    Guarda los bytes crudos del último correo procesado como .eml.

    Llamar desde el bucle del monitor justo después de descargar el
    mensaje (p. ej. con los bytes que devuelve IMAP FETCH RFC822).
    El panel de depuración usa este archivo para reprocesar sin esperar
    a que llegue un correo nuevo.
    """
    path = Path(directory) / LAST_EMAIL_FILENAME
    try:
        path.write_bytes(raw_bytes)
    except OSError as exc:
        logger.warning("No se pudo guardar el último correo en %s: %s", path, exc)
    return path


def load_email_file(path: Union[str, Path]) -> email.message.Message:
    """Carga un archivo .eml (exportado de Gmail, etc.) como Message."""
    with open(path, "rb") as fh:
        return email.message_from_binary_file(fh)


# ----------------------------------------------------------------------
# Precio de una PUBLICACIÓN vigilada (enlace añadido por el usuario)
# ----------------------------------------------------------------------
# Cascada de extracción sobre el HTML de la página del anuncio
# (Todocolección, Wallapop, Iberlibro...), de más a menos estructurado:
#   1. JSON-LD / JSON embebido: "price": "45.00"
#   2. Microdatos: itemprop="price" content="45.00"
#   3. Open Graph: og:price:amount / product:price:amount
#   4. Texto visible: primer importe con € cerca del principio
_JSON_PRICE_RE = re.compile(
    r'"price"\s*:\s*"?(\d{1,6}(?:[.,]\d{1,2})?)"?', re.IGNORECASE
)
# Wallapop (JSON de Next.js): "price":{"amount":45,...}
_AMOUNT_PRICE_RE = re.compile(
    r'"price"\s*:\s*\{[^{}]{0,80}?"amount"\s*:\s*"?(\d{1,6}(?:[.,]\d{1,2})?)',
    re.IGNORECASE,
)
# Iberlibro/AbeBooks: itemprop="price" content="EUR 45.00" (con divisa)
_ITEMPROP_PRICE_RE = re.compile(
    r'itemprop=["\']price["\'][^>]*content=["\'](?:EUR\s*|€\s*)?'
    r'(\d{1,6}(?:[.,]\d{1,2})?)["\']',
    re.IGNORECASE,
)
_OG_PRICE_RE = re.compile(
    r'(?:og|product):price:amount["\'][^>]*content=["\'](\d{1,6}(?:[.,]\d{1,2})?)["\']',
    re.IGNORECASE,
)
_DATA_PRICE_RE = re.compile(
    r'data-price=["\'](\d{1,6}(?:[.,]\d{1,2})?)["\']', re.IGNORECASE
)


def extract_price_from_listing_html(html: str) -> Optional[float]:
    """Precio de la página de un anuncio; None si no hay nada plausible."""
    if not html:
        return None
    for regex in (
        _ITEMPROP_PRICE_RE, _OG_PRICE_RE, _AMOUNT_PRICE_RE,
        _JSON_PRICE_RE, _DATA_PRICE_RE,
    ):
        m = regex.search(html)
        if m:
            try:
                value = _parse_es_number(m.group(1))
            except ValueError:
                continue
            if 0.5 <= value <= 100_000:
                return value
    # Último recurso: primer importe del texto visible
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001
        return None
    prices = [p for p in extract_prices(extractor.text) if 0.5 <= p <= 100_000]
    return prices[0] if prices else None


# Título y descripción en metadatos Open Graph (TC, Wallapop, AbeBooks…),
# con el orden de atributos en ambos sentidos.
_OG_TEXT_RE = re.compile(
    r'(?:og:title|og:description)["\'][^>]*content=["\']([^"\']{1,4000})["\']',
    re.IGNORECASE,
)
_OG_TEXT_REV_RE = re.compile(
    r'content=["\']([^"\']{1,4000})["\'][^>]*'
    r'(?:og:title|og:description)["\']',
    re.IGNORECASE,
)
_JSONLD_RE = re.compile(
    r"<script[^>]*ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)


def message_key(msg: email.message.Message) -> str:
    """
    Identificador ESTABLE de un correo, para no reinsertar sus datos.

    Usa el `Message-ID` (constante aunque el correo cambie de UID, se
    marque leído/no leído o se reprocese). Si falta, se deriva una
    huella del remitente + asunto + fecha.
    """
    raw = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    if raw:
        return raw.strip("<>").strip()
    import hashlib

    huella = "|".join(
        decode_header_value(msg.get(cab, "") or "")
        for cab in ("From", "Subject", "Date")
    )
    return "sha1:" + hashlib.sha1(huella.encode("utf-8")).hexdigest()


def clean_ad_url(url: str) -> str:
    """
    URL del anuncio sin parámetros de seguimiento (utm_*, idmopen…).

    Los enlaces de los correos llevan cola de campaña, así que el MISMO
    anuncio llega con cadenas distintas en cada aviso; sin limpiarla,
    cada correo añadiría una publicación vigilada repetida.
    """
    if not url:
        return ""
    from urllib.parse import urlsplit, urlunsplit

    partes = urlsplit(url.strip())
    limpio = urlunsplit((partes.scheme, partes.netloc, partes.path, "", ""))
    return limpio.rstrip("/") or url.strip()


# --- ¿El anuncio vende UN tomo o VARIOS? ------------------------------
# Un anuncio con varios volúmenes lleva el precio del CONJUNTO, así que
# su precio no puede entrar en la serie de un tomo suelto: la gráfica
# de "Platón — Diálogos I" acabó con un punto de 330 € que era el de
# los tres tomos juntos (2026-08-02).
_ROMANO_VOL = r"(?=[IVXL])(?:XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
# Un GRUPO es un volumen o un rango de libros: "II", "I-VI", "XIV - XIX"
_GRUPO_VOL = re.compile(
    rf"\b{_ROMANO_VOL}\b(?:\s*[-–—]\s*{_ROMANO_VOL}\b)?", re.I
)
# Lo que une DOS grupos cuando se enumeran ("I-V y VI-XII", "I-IV + XI-XVI")
_UNE_VOL = re.compile(
    r"^[\s.,;]*(?:y|e|and|\+|&|,|/)\s*(?:los\s+)?"
    r"(?:tomos?|vols?\.?|vol[úu]menes|libros?)?[\s.,;]*$",
    re.I,
)
_CUENTA_VOL = re.compile(
    r"\b(\d{1,2})\s*(?:vol[úu]menes|vols?\.?|tomos)\b", re.I
)
_TOMOS_ORDINALES = re.compile(
    r"\b(?:1er|1º|primer)\s+tomo\b.*\b(?:2do|2º|segundo)\s+tomo\b", re.I | re.S
)


def varios_volumenes(texto: str) -> bool:
    """
    ¿El anuncio vende MÁS DE UN volumen?

    Medido sobre los anuncios reales del usuario: "DIÁLOGOS TOMO I, II Y
    III" o "ANALES - LIBROS I-IV + XI-XVI" son varios tomos, mientras
    que "Historias I. Libros XIV - XIX" es UNO (su número de volumen y
    el rango de libros que contiene).
    """
    if not texto:
        return False
    if _TOMOS_ORDINALES.search(texto):
        return True
    m = _CUENTA_VOL.search(texto)
    if m and int(m.group(1)) > 1:
        return True
    grupos = [g for g in _GRUPO_VOL.finditer(texto) if g.group().strip()]
    if len(grupos) >= 3:
        return True
    if len(grupos) == 2:
        # Dos grupos solo cuentan si van ENUMERADOS ("I-V y VI-XII");
        # si entre ellos hay otra cosa ("Historias I. Libros XIV-XIX"),
        # el primero es el número del tomo.
        entre = texto[grupos[0].end():grupos[1].start()]
        return bool(_UNE_VOL.match(entre))
    return False


# --- ¿La publicación sigue en venta? ----------------------------------
# Los TRES sitios que se vigilan publican la disponibilidad con el
# vocabulario de schema.org, y ese dato manda sobre cualquier texto
# (medido el 2026-07-31 sobre las 220 publicaciones vigiladas:
# Wallapop 49/50, IberLibro 19/19 y Todocolección 131/151 lo traen; el
# resto de Todocolección no trae ninguno y ahí deciden las frases).
_DISPONIBILIDAD_RE = re.compile(
    r"availability\"?\s*[:=]\s*\"?\s*([A-Za-z:/._]+)"
    r"|itemprop=[\"']availability[\"'][^>]*?(?:href|content)=[\"']([^\"']+)",
    re.I,
)
_EN_VENTA = ("instock", "instoreonly", "onlineonly", "limitedavailability",
             "preorder", "backorder")
_AGOTADO = ("soldout", "outofstock", "out_of_stock", "discontinued")

# Frases con las que cada sitio lo dice cuando no hay metadato. Van
# ANCLADAS: "vendedor" contiene "vendido", y las páginas traen
# carruseles de otros anuncios que sí están vendidos.
#   · Todocolección: "artículo vendido", subastas finalizadas
#   · Wallapop     : "producto/artículo ya no está disponible"
#   · IberLibro    : "este ejemplar ya se ha vendido / no está disponible"
_VENDIDO_RE = re.compile(
    r"(art[íi]culo\s+vendido"
    r"|producto\s+vendido"
    r"|ejemplar\s+(?:ya\s+)?(?:se\s+ha\s+)?vendido"
    r"|ya\s+(?:ha\s+sido\s+|se\s+ha\s+)?vendido"
    r"|(?:art[íi]culo|producto|ejemplar|libro)\s+(?:ya\s+)?no\s+"
    r"(?:est[áa]\s+disponible|se\s+encuentra\s+disponible|est[áa]\s+a\s+la\s+venta)"
    r"|anuncio\s+(?:finalizado|retirado|cerrado)"
    r"|subasta\s+finalizada)",
    re.I,
)


def listing_availability(html: str) -> str:
    """
    Disponibilidad declarada por la página: `"venta"`, `"agotado"` o
    `""` si el anuncio no la publica.
    """
    for directo, itemprop in _DISPONIBILIDAD_RE.findall(html or ""):
        valor = (directo or itemprop).lower()
        if any(v in valor for v in _AGOTADO):
            return "agotado"
        if any(v in valor for v in _EN_VENTA):
            return "venta"
    return ""


def listing_sold(html: str) -> bool:
    """
    ¿El anuncio está VENDIDO (o retirado)?

    Un anuncio vendido CONSERVA su precio en la página, así que sin
    mirarlo la vigilancia seguiría apuntando un precio que ya no se
    puede pagar.

    Manda el metadato de disponibilidad, que es del propio anuncio: si
    dice que está en venta, no hay más que hablar (evita que un
    "vendido" del carrusel de recomendados retire un precio bueno). Solo
    cuando la página no lo publica se recurre a las frases del sitio, y
    ceñidas al texto del anuncio.

    Una página que no carga (0 caracteres) NO es una venta: puede ser un
    fallo de red, y borrar un precio por eso sería peor.
    """
    if not html:
        return False
    disponible = listing_availability(html)
    if disponible:
        return disponible == "agotado"
    propio = extract_listing_text(html, max_chars=4000)
    return bool(_VENDIDO_RE.search(propio))


def extract_listing_text(html: str, max_chars: int = 20_000) -> str:
    """
    Texto del PROPIO anuncio (título + descripción del vendedor) para
    reconocer qué contiene — p. ej. los tomos de un lote.

    SOLO fuentes ceñidas al anuncio: metadatos og:, JSON-LD @type
    Product (en TC lleva la descripción íntegra del vendedor), <h1> y
    el contenedor id="descripcion". El texto visible COMPLETO de la
    página queda como ÚLTIMO recurso: arrastra los carruseles de
    "anuncios relacionados" y etiquetaba tomos que no van en el lote
    (19 falsos en la prueba real 2026-07-26).
    """
    if not html:
        return ""
    import json as _json
    from html import unescape

    partes: list[str] = []
    for regex in (_OG_TEXT_RE, _OG_TEXT_REV_RE):
        partes.extend(unescape(m.group(1)) for m in regex.finditer(html))

    for m in _JSONLD_RE.finditer(html):
        try:
            data = _json.loads(m.group(1))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if (
                isinstance(item, dict)
                and str(item.get("@type", "")).lower() == "product"
            ):
                for campo in ("name", "description"):
                    valor = item.get(campo)
                    if isinstance(valor, str) and valor.strip():
                        partes.append(valor)

    soup = _get_soup(html)
    if soup is not None:
        h1 = soup.find("h1")
        if h1 is not None:
            partes.append(h1.get_text(" ", strip=True))
        desc = soup.find(id="descripcion")
        if desc is not None:
            partes.append(desc.get_text("\n", strip=True))

    if partes:
        vistos: set[str] = set()
        unicos: list[str] = []
        for p in partes:
            p = p.strip()
            if p and p not in vistos:
                vistos.add(p)
                unicos.append(p)
        return "\n".join(unicos)[:max_chars]

    # Último recurso (páginas sin metadatos ni estructura conocida)
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
        return extractor.text[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


def fetch_listing_price(url: str, timeout: int = 8) -> Optional[float]:
    """
    Descarga la página de una publicación y extrae su precio actual.

    Tolerante: cualquier fallo (red, 404, bloqueo anti-bot, sin precio)
    devuelve None y queda en el log. Nunca lanza.
    """
    if not url.lower().startswith(("http://", "https://")):
        return None
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                "Accept-Language": "es-ES,es;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1_500_000)
        html = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo consultar la publicación %s: %s", url, exc)
        return None
    price = extract_price_from_listing_html(html)
    if price is None:
        logger.info("Sin precio reconocible en %s", url)
    return price


def format_price(value: Optional[float]) -> str:
    """Formatea un precio en euros al estilo español ('40 €', '4,50 €')."""
    if value is None:
        return "?"
    if value == int(value):
        return f"{int(value)} €"
    return f"{value:.2f}".replace(".", ",") + " €"
