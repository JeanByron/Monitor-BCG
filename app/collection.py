"""
collection.py
=============
Colección Biblioteca Clásica Gredos del usuario.

Fuente de datos: `BDtomos/titulosBCG.xlsx` (hoja con columnas
Número | Autor(es) | Obras en la edición | Páginas | Notas). El número
puede venir como entero (419) o como "1[2]" (número de colección con
la edición entre corchetes); para ordenar y cruzar se usa el primer
entero.

Responsabilidades:
- `load_excel()`: lee el Excel a una lista de `Tomo` (openpyxl;
  tolerante — si falta la dependencia o el archivo, lanza
  `CollectionError` con un mensaje claro para la GUI).
- `match_tomo()`: dado el título de una oferta de Todocolección,
  intenta identificar a qué tomo de la colección corresponde:
    1. Número de colección explícito en el título
       ("... BIBLIOTECA CLÁSICA GREDOS / 869", "... GREDOS 86").
    2. Autor + palabras de la obra (normalizados, sin tildes).
  Devuelve None si no hay evidencia suficiente: mejor sin etiqueta
  que etiquetar mal.

La persistencia (tabla `tomos`) vive en database.py; este módulo no
toca SQLite.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import app_dir
from app.utils import normalize

logger = logging.getLogger(__name__)

DEFAULT_XLSX: Path = app_dir() / "BDtomos" / "titulosBCG.xlsx"


class CollectionError(Exception):
    """Error de importación de la colección (mensaje apto para la GUI)."""


@dataclass
class Tomo:
    """Un tomo de la Biblioteca Clásica Gredos."""

    numero: str                    # tal cual en el Excel ("1[2]", "419")
    orden: Optional[int]           # primer entero del número (para ordenar/cruzar)
    autor: str
    obras: str
    paginas: Optional[int]
    notas: str
    poseido: bool = False          # etiquetado "obtenido" del usuario
    deseado: bool = False          # etiquetado "deseado" del usuario
    precio_objetivo: Optional[float] = None  # avisar si baja de este precio (€)
    # Distintivo de volumen cuando VARIOS tomos comparten obra
    # ("Heródoto — Historia" ×5): lo rellena `annotate_ambiguous`.
    sufijo: str = ""
    # Título de la OBRA sin el ordinal de volumen ("Tragedias" en
    # "Tragedias II"): también lo rellena `annotate_ambiguous`, y solo
    # para los tomos agrupados. Vacío = se usa `obras` tal cual.
    titulo_base: str = ""

    def canonical_title(self) -> str:
        """
        Título en el orden ORIGINAL de la base de datos, ÚNICO por tomo.

        Con 197 tomos repartidos en 57 grupos de título repetido
        (2026-07-26), el sufijo de volumen ("Libros I-II", "Vol. IV",
        "nº 82") evita que las series de precios se mezclen entre
        volúmenes distintos de la misma obra. Los volúmenes de una
        misma obra comparten SIEMPRE la parte base del título, aunque
        el Excel lleve el ordinal dentro de la obra ("Tragedias II").
        """
        base = f"{self.autor} — {self.titulo_base or self.obras}"
        return f"{base} · {self.sufijo}" if self.sufijo else base


# Ordinal romano completo (I…CCCXCIX): el lookahead evita que case la
# cadena vacía. Debe llegar a L y C — Dion Casio abarca "Libros L-LX".
_ROMAN = r"(?=[IVXLC])C{0,3}(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
# Ordinal (o RANGO) de volumen AL FINAL del título de la obra:
# "Tragedias II", "(Moralia) II", "Discursos I", "Discursos XXXVI-LX",
# "Enéadas III-IV". Solo guiones como separador de rango: la "a" de
# "Libros I a VIII" solo se admite en las Notas (en un título sería
# ambigua con la preposición).
_TRAILING_VOL_RE = re.compile(
    rf"[\s,;:·-]+(?:vol\.?|volumen|tomo|parte)?\s*"
    rf"({_ROMAN})(?:\s*[-–—]\s*({_ROMAN}))?\s*[.)]?\s*$",
    re.IGNORECASE,
)
# Volumen declarado en las Notas: "Vol. III.", "Volumen IV"
_VOL_NOTE_RE = re.compile(rf"\bvol(?:umen)?\.?\s*({_ROMAN})\b", re.IGNORECASE)
# Alcance por libros: "Libros I-II.", "Libro VII", "Libros VIII a IX"
_BOOKS_NOTE_RE = re.compile(
    rf"\blibros?\s+({_ROMAN})(?:\s*(?:[-–—]|a)\s*({_ROMAN}))?\b",
    re.IGNORECASE,
)


def split_volume(obras: str) -> tuple[str, str]:
    """
    Separa el título de la obra de su ordinal (o rango) de volumen.

    "Tragedias II" → ("Tragedias", "II"); "Discursos XXXVI-LX" →
    ("Discursos", "XXXVI-LX"); "Anábasis" → ("Anábasis", "").
    """
    texto = (obras or "").strip()
    m = _TRAILING_VOL_RE.search(texto)
    if not m or not m.group(1):
        return texto, ""
    base = texto[: m.start()].strip(" ,;:·-")
    if len(base) < 4:  # el ordinal ERA el título entero: no partir
        return texto, ""
    volumen = m.group(1).upper()
    if m.group(2):
        volumen = f"{volumen}-{m.group(2).upper()}"
    return base, volumen


def _volume_hint(tomo: Tomo) -> str:
    """
    Distintivo corto del volumen de un tomo; '' si no hay evidencia.

    Por orden de fiabilidad: "Vol. N" de las Notas → ordinal al final
    del título de la obra → "Libros X-Y" de las Notas → primera frase
    corta de las Notas. El ordinal de la obra manda sobre el alcance
    por libros para que todos los volúmenes de un grupo lleven la
    MISMA clase de etiqueta. NUNCA trocear por el punto a ciegas:
    "Vol. II." daba "Vol" y TODOS los volúmenes del grupo colisionaban
    con el mismo sufijo (bug 2026-07-26: solo el primero quedaba
    etiquetado, el resto caía a "nº 234").
    """
    nota = (tomo.notas or "").strip()
    m = _VOL_NOTE_RE.search(nota)
    if m:
        return f"Vol. {m.group(1).upper()}"
    volumen = split_volume(tomo.obras)[1]
    if volumen:
        # Rango ("XXXVI-LX") = alcance, no número de volumen
        return volumen if "-" in volumen else f"Vol. {volumen}"
    m = _BOOKS_NOTE_RE.search(nota)
    if m:
        if m.group(2):
            return f"Libros {m.group(1).upper()}-{m.group(2).upper()}"
        return f"Libro {m.group(1).upper()}"
    primero = nota.split(".")[0].strip()
    if 0 < len(primero) <= 26:
        return primero
    return ""


def annotate_ambiguous(tomos: list[Tomo]) -> list[Tomo]:
    """
    Marca los tomos cuya obra está DUPLICADA en la colección (varios
    volúmenes), asignándoles un título base común y un sufijo único
    (volumen de las Notas o del propio título; en su defecto, el nº de
    colección). Idempotente; devuelve la misma lista.

    La agrupación ignora el ordinal final del título ("Tragedias I" y
    "Tragedias II" son la MISMA obra): sin eso, cada volumen con el
    ordinal metido en la obra quedaba suelto y sin etiquetar.
    """
    grupos: dict[tuple[str, str], list[Tomo]] = {}
    for t in tomos:
        base = split_volume(t.obras)[0]
        grupos.setdefault((normalize(t.autor), normalize(base)), []).append(t)
    for miembros in grupos.values():
        if len(miembros) < 2:
            for t in miembros:
                t.sufijo = ""
                t.titulo_base = ""
            continue
        # Título base común: el más frecuente entre los miembros (así
        # respeta tildes y mayúsculas del Excel).
        from collections import Counter

        bases = Counter(split_volume(t.obras)[0] for t in miembros)
        titulo_base = bases.most_common(1)[0][0]
        vistos: set[str] = set()
        for t in miembros:
            hint = _volume_hint(t) or f"nº {t.orden}"
            if normalize(hint) in vistos:  # colisión: el nº nunca choca
                hint = f"nº {t.orden}"
            vistos.add(normalize(hint))
            t.sufijo = hint
            t.titulo_base = titulo_base
    return tomos


# Rangos especiales de la colección (dato del usuario, 2026-07-25):
# - 360-415: los tomos MÁS RAROS de conseguir.
# - 416-420: no pertenecen propiamente a la colección (apéndice).
RARE_RANGE = (360, 415)
APPENDIX_RANGE = (416, 420)


def is_rare(tomo: Tomo) -> bool:
    """True si el tomo está en el rango de los más raros (360-415)."""
    return (
        tomo.orden is not None
        and RARE_RANGE[0] <= tomo.orden <= RARE_RANGE[1]
    )


def is_appendix(tomo: Tomo) -> bool:
    """True si el tomo no pertenece propiamente a la colección (416-420)."""
    return (
        tomo.orden is not None
        and APPENDIX_RANGE[0] <= tomo.orden <= APPENDIX_RANGE[1]
    )


def _clean_numero(value) -> str:
    """
    Número de colección tal cual, sin la cola ".0" que openpyxl añade a
    las celdas numéricas (397 de 423 tomos se veían como "233.0" en la
    tabla de la Colección, bug 2026-07-26). Conserva formatos con
    edición entre corchetes ("1[2]").
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    texto = str(value).strip()
    if re.fullmatch(r"\d+\.0+", texto):
        return texto.split(".")[0]
    return texto


def _first_int(value) -> Optional[int]:
    """'1[2]' → 1; 419.0 → 419; None/'' → None."""
    if value is None:
        return None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


def load_excel(path: Path = DEFAULT_XLSX) -> list[Tomo]:
    """
    Lee el Excel de la colección y devuelve la lista de tomos.

    Salta la fila de cabecera y las filas sin autor ni obras. Lanza
    `CollectionError` si el archivo no existe o falta openpyxl.
    """
    if not Path(path).exists():
        raise CollectionError(
            f"No se encontró el Excel de la colección:\n{path}"
        )
    try:
        import openpyxl  # dependencia opcional
    except ImportError as exc:  # pragma: no cover - entorno sin openpyxl
        raise CollectionError(
            "Falta el paquete openpyxl (pip install openpyxl)."
        ) from exc

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as exc:  # noqa: BLE001 - archivo corrupto/abierto
        raise CollectionError(f"No se pudo leer el Excel: {exc}") from exc

    tomos: list[Tomo] = []
    for row in rows[1:]:  # saltar cabecera
        numero, autor, obras, paginas, notas = (list(row) + [None] * 5)[:5]
        autor = str(autor).strip() if autor else ""
        obras = str(obras).strip() if obras else ""
        if not autor and not obras:
            continue  # fila vacía o decorativa
        tomos.append(
            Tomo(
                numero=_clean_numero(numero),
                orden=_first_int(numero),
                autor=autor,
                obras=obras,
                paginas=_first_int(paginas),
                notas=str(notas).strip() if notas else "",
            )
        )
    logger.info("Colección importada: %d tomo(s) desde %s", len(tomos), path)
    return annotate_ambiguous(tomos)


# ----------------------------------------------------------------------
# Cruce título de oferta → tomo de la colección
# ----------------------------------------------------------------------
# Número de colección en el título de un anuncio: "GREDOS / 869",
# "BIBLIOTECA CLASICA GREDOS 86", "BCG nº 123"...
_NUM_NEAR_GREDOS_RE = re.compile(
    r"(?:gredos|bcg)[^0-9]{0,15}?(\d{1,3})\b"
)

# Palabras demasiado comunes para identificar una obra por sí solas
_STOPWORDS = frozenset({
    "obras", "obra", "completas", "tomo", "tomos", "libro", "libros",
    "volumen", "volumenes", "vol", "vols", "biblioteca", "clasica",
    "gredos", "editorial", "edicion", "nuevo", "nueva",
})


def _significant_words(text: str) -> list[str]:
    """Palabras significativas (≥4 letras, sin genéricas), en su orden."""
    return [
        w for w in re.split(r"[^a-z0-9]+", normalize(text))
        if len(w) >= 4 and w not in _STOPWORDS
    ]


def _lcs_len(a: list[str], b: list[str]) -> int:
    """Longitud de la subsecuencia común más larga (respeta el ORDEN)."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        row = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                row[j] = prev[j - 1] + 1
            else:
                row[j] = max(prev[j], row[j - 1])
        prev = row
    return prev[-1]


_ROMAN_TOKEN_RE = re.compile(r"\b([ivxlc]{1,7})\b")
_VALID_ROMAN = set("ivxlc")


def _volume_tokens(text: str) -> set[str]:
    """Números romanos sueltos de un texto (marcadores de volumen)."""
    return {
        tok for tok in _ROMAN_TOKEN_RE.findall(normalize(text))
        if set(tok) <= _VALID_ROMAN
    }


def score_tomo(tomo: Tomo, titulo: str) -> float:
    """
    Puntuación de semejanza entre una oferta y un tomo (0 = nada).

    Combina cuatro señales:
    - PALABRAS CLAVE del autor presentes en el título (peso 3, la señal
      más fiable: sin autor la puntuación es 0).
    - Palabras significativas de la obra presentes (peso 2).
    - ORDEN DE PALABRAS: subsecuencia común más larga entre la obra y
      el título (bonifica que aparezcan en el mismo orden).
    - SEMEJANZA GLOBAL difflib entre "autor obras" y el título (0-6).
    """
    title_n = normalize(titulo)
    title_words = _significant_words(titulo)

    autor_hits = sum(
        1 for token in re.split(r"[;,()\s]+", normalize(tomo.autor))
        if len(token) >= 4 and token in title_n
    )
    if autor_hits == 0:
        return 0.0  # sin autor no hay identificación fiable

    obra_words = _significant_words(tomo.obras)
    obra_hits = sum(1 for w in set(obra_words) if w in title_n)
    if not obra_words:
        # Diez tomos se titulan solo "Obras" o "Biblioteca" — palabras
        # tan corrientes que la lista de significativas las descarta, y
        # con ella se quedaban SIN poder emparejarse jamás (Luciano ×4,
        # Ausonio ×2, Prudencio ×2, Terencio, Pseudo Apolodoro). Con el
        # autor ya confirmado, basta con que el título aparezca tal cual.
        base = normalize(split_volume(tomo.obras)[0])
        obra_hits = 1 if base and base in title_n else 0
    if obra_hits == 0:
        return 0.0

    order_bonus = max(0, _lcs_len(obra_words, title_words) - 1) * 2.0

    from difflib import SequenceMatcher

    ratio = SequenceMatcher(
        None, normalize(f"{tomo.autor} {tomo.obras}"), title_n
    ).ratio() * 6.0

    # VOLUMEN: si las Notas del tomo indican qué libros/vol. abarca
    # ("Libros V-VI.") y el anuncio menciona esos romanos, gran bonus —
    # clave para no confundir volúmenes de obras con título repetido
    # (57 grupos, p. ej. "Heródoto — Historia" ×5).
    vol_bonus = 0.0
    # El ordinal puede venir en las Notas ("Vol. III.") o dentro del
    # propio título de la obra ("Tragedias III"): ambos cuentan.
    tomo_vols = _volume_tokens(tomo.notas) | _volume_tokens(
        split_volume(tomo.obras)[1]
    )
    if tomo_vols:
        title_vols = _volume_tokens(titulo)
        coincidentes = tomo_vols & title_vols
        vol_bonus = min(len(coincidentes), 2) * 2.5
        if title_vols and not coincidentes:
            vol_bonus = -2.0  # el anuncio habla de OTROS volúmenes

    return (
        autor_hits * 3.0 + min(obra_hits, 4) * 2.0
        + order_bonus + ratio + vol_bonus
    )


# Umbral mínimo de la puntuación difusa para aceptar un cruce
_MATCH_THRESHOLD = 7.0


def match_tomo(tomos: list[Tomo], titulo: str) -> Optional[Tomo]:
    """
    Identifica el tomo de la colección al que corresponde una oferta.

    1. Número explícito junto a "Gredos"/"BCG" en el título → cruce
       directo con el número de colección (evidencia máxima).
    2. Puntuación difusa `score_tomo` (palabras clave del autor +
       palabras de la obra + orden de palabras + semejanza global):
       gana el tomo con MAYOR semejanza si supera el umbral.

    Devuelve None si la evidencia es débil (mejor no etiquetar).
    El llamador puede usar `tomo.canonical_title()` para mostrar el
    título en el orden original de la base de datos.
    """
    if not tomos or not titulo:
        return None
    title_n = normalize(titulo)

    # 1) Número de colección explícito
    m = _NUM_NEAR_GREDOS_RE.search(title_n)
    if m:
        num = int(m.group(1))
        for tomo in tomos:
            if tomo.orden == num:
                logger.debug(
                    "Tomo identificado por número %d: %s", num, tomo.autor
                )
                return tomo

    # 2) Mayor semejanza difusa por encima del umbral
    best: Optional[Tomo] = None
    best_score = 0.0
    for tomo in tomos:
        score = score_tomo(tomo, titulo)
        if score > best_score:
            best_score = score
            best = tomo

    if best is not None and best_score >= _MATCH_THRESHOLD:
        logger.debug(
            "Tomo identificado por semejanza (%.1f): %s",
            best_score, best.canonical_title()[:70],
        )
        return best
    return None


# Autores que NO son un autor: obras colectivas o anónimas. Se comparan
# sobre el texto normalizado SIN la aclaración entre paréntesis y sin
# puntuación, así "VV.AA.", "VVAA (sofistas)" y "Varios autores" caen
# todos en el mismo saco.
_COLLECTIVE_AUTHORS = frozenset({
    "", "vvaa", "aavv", "vvaavv", "varios", "variosautores",
    "autoresvarios", "anonimo", "anonima", "anonimos", "anonimas",
    "autoranonimo", "desconocido", "autordesconocido", "sinautor",
})


def is_collective_author(autor: str) -> bool:
    """¿El campo Autor es 'VVAA', 'Anónimo' y demás (no un autor real)?"""
    base = normalize(autor or "").split("(")[0]
    return re.sub(r"[^a-z]", "", base) in _COLLECTIVE_AUTHORS


def author_for_search(autor: str) -> str:
    """
    Autor utilizable en una búsqueda externa; '' si es colectivo.

    Meter "VVAA" o "Anónimo" en el buscador de Todocolección, Iberlibro,
    AbeBooks o Wallapop estropea la consulta: son 38 tomos de la
    colección (petición del usuario, 2026-07-26). De "VVAA (sofistas)"
    se conserva SOLO la aclaración, que es parte del título real del
    tomo ("Sofistas. Testimonios y fragmentos") y sin ella la búsqueda
    queda demasiado genérica.
    """
    if not is_collective_author(autor):
        return autor or ""
    m = re.search(r"\(([^)]{3,40})\)", autor or "")
    return m.group(1).strip() if m else ""


def tomo_label(tomo: Tomo) -> str:
    """Etiqueta corta para notificaciones y tablas: 'Tomo BCG nº 419 — Plinio'."""
    num = f"nº {tomo.orden}" if tomo.orden is not None else tomo.numero
    return f"Tomo BCG {num} — {tomo.autor}"


def tomos_from_rows(rows) -> list[Tomo]:
    """
    Lista de Tomos desde filas de la tabla `tomos` de SQLite, ya pasada
    por `annotate_ambiguous` (OBLIGATORIO: los sufijos de volumen deben
    coincidir con los del monitor en toda ruta que construya la lista).
    """
    return annotate_ambiguous([
        Tomo(
            # Mismo saneo que al importar el Excel: en la base hay
            # números guardados como "150.0" y salían así en la tabla.
            numero=_clean_numero(r["numero"]), orden=r["orden"],
            autor=r["autor"],
            obras=r["obras"], paginas=r["paginas"],
            notas=r["notas"] or "", poseido=bool(r["poseido"]),
            deseado=bool(r["deseado"]),
            precio_objetivo=r["precio_objetivo"],
        )
        for r in rows
    ])


# Separadores de segmentos dentro de una descripción de lote: saltos de
# línea y puntuación "de lista". La coma NO separa (partiría títulos
# como "Vidas, opiniones..." y el autor de su obra).
_SEGMENT_RE = re.compile(r"[\n\r;|•·]+")


def match_tomos_multi(tomos: list[Tomo], texto: str) -> list[Tomo]:
    """
    Todos los tomos de la colección reconocibles en el texto de una
    publicación de LOTE (título + descripción): se cruza cada segmento
    (línea o elemento de lista) con `match_tomo` — que exige evidencia
    de autor+obra, así el ruido de la página no etiqueta nada — y se
    devuelven sin duplicados, en orden de colección.
    """
    if not tomos or not texto:
        return []
    encontrados: dict[Optional[int], Tomo] = {}
    segmentos = [s.strip() for s in _SEGMENT_RE.split(texto) if s.strip()]
    for segmento in segmentos[:400]:  # tope de cordura en páginas enormes
        if len(segmento) < 8:
            continue
        tomo = match_tomo(tomos, segmento)
        if tomo is not None:
            encontrados[tomo.orden] = tomo
    return [
        encontrados[k]
        for k in sorted(encontrados, key=lambda o: (o is None, o))
    ]
