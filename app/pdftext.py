"""
pdftext.py
==========
Análisis de un PDF de la colección: saca su texto estructurado y dice
con qué dificultades se ha encontrado.

El PDF NO se guarda ni se copia: se lee donde esté y solo se escribe el
texto extraído (`BDtomos/TextosTomos/NNN - Autor - Título.jsonl`), que
pesa unas diez veces menos. Así se puede borrar el PDF en cuanto el
análisis salga bien.

Por qué un `.jsonl` y no un `.txt` corrido: un texto plano pierde el
número de página, y sin él no hay cita posible ("tomo X, página Y"),
que es la razón de ser de todo esto. Cada línea del archivo es una
página con su número de PDF, su número IMPRESO, a qué sección
pertenece, el cuerpo y las notas aparte.

Lo que resuelve (medido sobre PDF reales, 2026-07-28):

- **Notas al pie**: se separan por tamaño de letra RELATIVO a cada
  documento (9 pt es cuerpo en un tomo y nota al pie en otro).
- **Encabezados repetidos**: se retiran del cuerpo, pero antes dicen en
  qué obra va cada página.
- **Guiones de final de línea**: se recomponen ("lacede-\\nmonios", que
  si no, no lo encuentra ninguna búsqueda).
- **Folio impreso**: se lee de los márgenes y se calcula el desfase con
  la página del PDF (suele ser de 2 o 3 páginas).
- **Marcas de agua** de las webs de origen.
- **Escaneos sin texto**: se detectan y se avisa, para buscar otra copia.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app import collection
from app.config import app_dir

logger = logging.getLogger(__name__)

TEXTOS_DIR = app_dir() / "BDtomos" / "TextosTomos"

# --- Reconocimiento de secciones -------------------------------------
_SECCIONES = (
    # La concordancia del traductor se titula de varias maneras: en
    # Estrabón es "ÍNDICE DE TOPÓNIMOS Y ÉTNICOS" y en otros tomos
    # "índice de nombres propios" o "índice onomástico" (2026-07-31).
    ("indice_nombres", r"indice de nombres|indice onomastico|"
                       r"indice de toponimos|indice de etnicos|"
                       r"indice de lugares|indice geografico|"
                       r"indice de nombres propios|indice de autores"),
    ("indice_general", r"indice general|^indice$|tabla de materias|sumario"),
    ("bibliografia", r"bibliografia"),
    ("abreviaturas", r"abreviaturas|siglas"),
    # "Notas", pero también "Notas cartas y fragmentos": las ediciones
    # digitales trocean la cola en un bloque de notas por obra.
    ("notas_finales", r"^notas\b|notas al texto|notas complementarias"),
    ("introduccion", r"introduccion|prologo|preliminar|prefacio"),
    ("portada", r"anteportada|portada|derechos de autor|creditos"),
)

_COMUNES = re.compile(
    r"\b(de|la|el|los|las|que|en|un|una|por|con|para|su|del|al|se|es|no|"
    r"como|m[áa]s|pero|sus|le|ha|o|este|s[íi]|porque|entre|cuando|muy|sin)\b",
    re.I,
)
_RAROS = re.compile(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ.,;:!¡?¿()\[\]«»\"'—–\-/%&*†°º\d]")
_BASURA = re.compile(r"[a-z]*(zyxwvu|vutsrq|ponmlk|jihgfe)[a-zA-Z]*", re.I)
_FOLIO = re.compile(r"^\d{1,4}$")

# --- Referencias del margen -------------------------------------------
# La colección cita a cada autor como manda la tradición, y el número
# va al margen: verso o parágrafo ("805"), página de Estéfano en Platón
# y Juliano ("229D") o página de Bekker en Aristóteles ("1094a"). En las
# ediciones digitales el conversor las suelta como líneas sueltas al
# principio de la hoja, y ahí quedaban DENTRO del texto (2026-07-29).
_REF_MARGINAL = re.compile(r"^\d{1,4}(?:[A-Ea-e]|[ab]\d{0,2})?$")
# Llamada de nota dentro del texto corrido: "escandalizarse[61] según"
_MARCA_NOTA = re.compile(r"\[(\d{1,4}[a-z]?)\]")
# Nota final suelta y su enlace de vuelta al texto ("<<")
_NOTA_CABEZA = re.compile(r"^\s*\[(\d{1,4}[a-z]?)\]\s*")
_VUELTA_NOTA = re.compile(r"\s*<<\s*$")

# --- Ediciones digitales (EPUB convertido a PDF) -----------------------
# Varios tomos de la BCG circulan como libro electrónico pasado a PDF:
# hoja A4 uniforme, sin folio impreso y con un pie "Página N" (N = la
# hoja del PDF, no la del papel) que el conversor añade a TODAS. Si no
# se reconoce el formato, ese pie entra en el texto de cada página, los
# números de verso del margen se toman por folios y las hojas cortas
# (portadillas de cada obra, notas finales) se cuentan como escaneadas
# (medido en Aristófanes I y II, 2026-07-29).
#   "Página 201"  ·  "www.lectulandia.com - Página 201"
_MARCADOR_EBOOK = re.compile(
    r"^\s*(?:\S{0,40}\s*[-–—·|]\s*)?p[áa]gina\s+(\d+)\s*$", re.I
)
# Nota final suelta: cada una ocupa su propia hoja ("[65] Deuteronomio…")
_NOTA_SUELTA = re.compile(r"^\s*\[\d{1,4}[a-z]?\]")
# Secciones que ya no son texto del tomo: la cola del volumen
_COLA_TIPOS = frozenset({"notas_finales", "indice_general", "indice_nombres"})

# Secciones que no pertenecen a ninguna obra concreta del volumen
_SIN_OBRA = frozenset(
    {"notas_finales", "indice_general", "indice_nombres", "bibliografia"}
)

# --- Índice de nombres ------------------------------------------------
# Los índices de la BCG citan de DOS maneras (medido en PDF reales):
#   · por página:        "Aquiles, 12, 45-48"
#   · por canto y verso: "Amazonas, III 189; VI 186; XXIV 804."
# y las entradas se parten en varias líneas porque el índice va a dos
# columnas estrechas.
_ROMANO = r"[IVXLCDM]+"
# Comienzo de entrada: un nombre, coma, y detrás una referencia
_ENTRADA = re.compile(
    rf"^\s*([^\d;:][^\d;:]{{1,58}}?)\s*[,:]\s*"
    rf"(?=(?:{_ROMANO}\s*[\s,]?\s*)?\d)"
)
# Índice DESCRIPTIVO: el nombre lleva una explicación y las
# localizaciones van al final ("Abante (1): compañero de Perseo en la
# lucha contra Fineo, V 126."). Se exige mayúscula inicial y que la
# línea traiga alguna cifra; si no, cualquier frase entraría.
#   Los homónimos se numeran ("Abante (1):"), así que el nombre puede
#   llevar cifras dentro; lo que no puede es empezar por ellas.
_ENTRADA_DESC = re.compile(
    r"^\s*([A-ZÁÉÍÓÚÜÑ][^;:]{1,58}?)\s*[,:]\s+(?=[A-Za-zÁ-Úá-ú])"
)
# Continuación: la línea solo trae más referencias de la entrada previa
_SOLO_REFS = re.compile(rf"^[\s\d,;.\-–']*(?:{_ROMANO}[\s\d,;.\-–']*)*$")
# Grupos de referencia dentro del texto de una entrada
_GRUPO_REF = re.compile(rf"(?:({_ROMANO})\s+)?(\d{{1,4}}(?:\s*[-–]\s*\d{{1,4}})?)")
# Abreviatura de la obra delante de la cifra: "Arat. 2, 2-3",
# "Dión 1, 1", "Demetr.-Ant. 92, 3". Los romanos NO cuentan (son
# cantos, no obras) y se descartan aparte.
_REF_OBRA = re.compile(
    r"(?:^|;)\s*([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÜÑáéíóúüñ.\-]{1,14}?\.?)\s+(?=\d)"
)
# Remisiones ("véase", "cf.") que no aportan localización
_REMISION = re.compile(r"\b(v[ée]ase|cf\.|vid\.)\b", re.I)
# Rótulo que ANCLA el comienzo del índice de nombres. Se busca SIN
# espacios: en los escaneos el rótulo va con las letras espaciadas
# ("I N D I C  E  D E  T O P Ó N I M O S"), y el espaciado que mete el
# OCR es irregular (Estrabón, 2026-07-31).
_ROTULO_INDICE = re.compile(
    r"indicede(?:nombres|onomastico|toponimos|etnicos|lugares|geografico)",
    re.I,
)
# Entrada de índice al estilo "Sábata XVI, 4,2": el nombre, el LIBRO en
# romano y luego capítulo y párrafo. Sin coma tras el nombre.
_ENTRADA_LIBRO = re.compile(
    rf"^\s*([^\d;:]{{2,58}}?)\s+({_ROMANO})\s*,\s*(?=\d)"
)
# Sin romano delante, un número tan alto es un AÑO de la bibliografía
# ("Bolonia, 1474"), no una página ni un verso.
_MAX_PAGINA = 1200


# Por debajo de esta cobertura el PDF está demasiado incompleto para
# arreglarlo con un reconocimiento parcial: mejor buscar otra copia.
UMBRAL_OCR_PARCIAL = 75.0

# Dónde suele instalarse Tesseract en Windows
_RUTAS_TESSERACT = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


class AnalisisCancelado(Exception):
    """El usuario cortó el análisis (cerró la ventana o el aviso)."""


@dataclass
class Analisis:
    """Resultado de analizar un PDF."""

    archivo: str
    sha1: str
    paginas_pdf: int = 0
    estado: str = "sin_texto"          # nativo | ocr_bueno | ocr_sucio | sin_texto
    formato: str = "impreso"           # impreso | ebook (EPUB pasado a PDF)
    legibilidad: float = 0.0
    palabras: int = 0
    paginas_sin_texto: list = field(default_factory=list)
    paginas_ocr: list = field(default_factory=list)
    # Se INTENTÓ el reconocimiento (aunque no rescatara nada): sin esta
    # marca, un OCR que no saca texto dejaba `paginas_ocr` vacío y la
    # ventana volvía a ofrecer lo mismo una y otra vez.
    ocr_intentado: bool = False
    ocr_fallo: str = ""
    desfase_folio: Optional[int] = None
    cuerpo_pt: float = 0.0
    secciones: list = field(default_factory=list)
    nombres: dict = field(default_factory=dict)
    registros: list = field(default_factory=list)
    tomo_detectado: Optional[object] = None
    obras: list = field(default_factory=list)   # títulos del volumen
    paginas_impresas: int = 0          # las del tomo en papel (Excel)
    dificultades: list = field(default_factory=list)

    @property
    def utilizable(self) -> bool:
        """¿Tiene texto suficiente para indexarlo?"""
        return self.estado != "sin_texto" and self.palabras > 500

    @property
    def cobertura(self) -> float:
        """% de páginas del PDF que traen texto."""
        if not self.paginas_pdf:
            return 0.0
        con_texto = self.paginas_pdf - len(self.paginas_sin_texto)
        return con_texto / self.paginas_pdf * 100

    @property
    def hojas_de_texto(self) -> int:
        """
        Hojas que llevan el texto del tomo (sin la cola de notas).

        En las ediciones digitales las notas finales van a hoja POR
        NOTA: en Aristófanes II son 1.072 de las 1.436, y decir "1.436
        páginas" de un tomo de 528 despista más que informa.
        """
        return hojas_de_texto(self.secciones, self.paginas_pdf)

    @property
    def paginas_rescatables(self) -> list:
        """
        Hojas sin texto que MERECE la pena reconocer.

        Se dejan fuera la cubierta y las láminas del final: son
        ilustraciones o mapas, no páginas de texto perdidas, y ofrecer
        el reconocimiento por ellas es dar la lata (Ilíada y las dos
        Comedias, 2026-07-29).
        """
        if not self.paginas_pdf:
            return list(self.paginas_sin_texto)
        return [
            p for p in self.paginas_sin_texto
            if 3 < p < self.paginas_pdf - 2
        ]

    @property
    def completable_con_ocr(self) -> bool:
        """
        ¿Merece la pena reconocer SOLO las páginas que faltan?

        Sí cuando el PDF está casi entero (por encima del umbral) pero
        le faltan páginas sueltas: suelen ser justo las que importan
        (en el Plutarco, las dos del índice de nombres). Si falta más
        de una cuarta parte, es mejor buscar otra copia que reconocer
        medio tomo.
        """
        return (
            self.estado != "sin_texto"
            and bool(self.paginas_rescatables)
            and self.cobertura >= UMBRAL_OCR_PARCIAL
        )

    def resumen(self) -> str:
        """Texto para el aviso que ve el usuario."""
        if self.estado == "sin_texto":
            return (
                "Este PDF es un ESCANEO: no lleva texto dentro, solo "
                "imágenes de las páginas.\n\nNo se puede indexar tal cual: "
                "busca otra copia en la que se pueda seleccionar el texto."
            )
        if self.formato == "ebook":
            # Aquí "página" no es la del papel: son hojas del conversor,
            # y las notas van a hoja por nota. Decirlo tal cual.
            notas = self.paginas_pdf - self.hojas_de_texto
            lineas = [
                f"Hojas del PDF: {self.paginas_pdf:,}".replace(",", ".")
                + f" ({self.hojas_de_texto} de texto"
                + (f" y {notas:,} de notas finales)".replace(",", ".")
                   if notas else ")"),
            ]
            if self.paginas_impresas:
                lineas.append(
                    f"El tomo en papel tiene {self.paginas_impresas} páginas; "
                    "esta edición digital no las conserva"
                )
        else:
            lineas = [f"Páginas: {self.paginas_pdf}"]
        lineas += [
            f"Palabras extraídas: {self.palabras:,}".replace(",", "."),
            f"Calidad del texto: {self.estado.replace('_', ' ')} "
            f"({self.legibilidad:.0f} % de palabras reconocidas)",
            f"Secciones detectadas: {len(self.secciones)}",
            f"Nombres del índice: {len(self.nombres)}",
        ]
        if self.obras:
            lineas.insert(
                1, f"Obras del volumen: {len(self.obras)} — "
                + ", ".join(o[:28] for o in self.obras[:4])
                + ("…" if len(self.obras) > 4 else "")
            )
        if self.paginas_rescatables:
            lineas.append(
                f"Páginas sin reconocer: {len(self.paginas_rescatables)} "
                f"({100 - self.cobertura:.0f} %)"
            )
        elif self.paginas_sin_texto:
            lineas.append(
                f"Hojas que son solo imagen: {len(self.paginas_sin_texto)} "
                "(cubierta o láminas)"
            )
        if self.paginas_ocr:
            lineas.append(
                f"Reconocidas con OCR: {len(self.paginas_ocr)}"
            )
        if self.desfase_folio:
            signo = "menos" if self.desfase_folio > 0 else "más"
            lineas.append(
                f"Página impresa = página del PDF {signo} "
                f"{abs(self.desfase_folio)}"
            )
        elif self.desfase_folio == 0:
            lineas.append("La página impresa coincide con la del PDF")
        return "\n".join(lineas)


def hojas_de_texto(secciones: list, paginas_pdf: int) -> int:
    """
    Hasta dónde llega el texto del tomo.

    Se recorren las secciones DESDE EL FINAL mientras sean notas o
    índices: la cola puede venir troceada (el Juliano trae cinco
    bloques de notas seguidos, uno por obra) y quedarse con el último
    dejaba 685 hojas "de texto" de las que 650 son notas.
    """
    fin = paginas_pdf
    for seccion in reversed(secciones or ()):
        if seccion.get("tipo") not in _COLA_TIPOS:
            break
        fin = min(fin, seccion.get("desde", 1) - 1)
    return max(0, fin)


def _umbral_de_nota(bloques: list) -> float:
    """
    Por debajo de qué cuerpo de letra, en ESTA hoja, hay nota al pie.

    Se mide hoja a hoja, no una vez para todo el tomo: en los escaneos
    la escala cambia de una página a otra y un umbral global mandaba a
    las notas el 87 % del texto (Moralia X: 790.000 caracteres de
    "notas" frente a 118.000 de cuerpo, 2026-07-29).

    Si no hay dos cuerpos claramente distintos —o el pequeño ocupa más
    de un tercio de la hoja— no se separa nada: esa página va entera al
    cuerpo, que es lo que es.
    """
    tam: Counter = Counter()
    for b in bloques:
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                tam[round(s["size"], 1)] += len(s["text"])
    if not tam:
        return 0.0
    dominante = tam.most_common(1)[0][0]
    umbral = dominante * 0.85
    total = sum(tam.values())
    menores = sum(c for t, c in tam.items() if t < umbral)
    if not menores or menores / total > 0.35:
        return 0.0
    return umbral


def _capitulos(marginales: list) -> list:
    """
    Cuáles de las cifras del margen son CAPÍTULOS.

    En Plutarco (y en toda la colección que numera así) el margen lleva
    el número del capítulo y, debajo, el de cada sección empezando por
    el 2: "39, 2, 3, 4…". Así que es capítulo la cifra a la que sigue
    un 2. Es la referencia con la que cita el propio índice del tomo
    ("ABANTIDAS: Arat. 2, 2-3").
    """
    return [
        n for n, siguiente in zip(marginales, marginales[1:]) if siguiente == 2
    ]


def _sin_marcador(texto: str) -> str:
    """Quita el pie "Página N" que el conversor pone en cada hoja."""
    return "\n".join(
        l for l in texto.splitlines() if not _MARCADOR_EBOOK.match(l.strip())
    )


def _es_hoja_escaneada(pagina, texto: str) -> bool:
    """
    ¿Esta hoja es SOLO imagen (y por tanto rescatable con OCR)?

    No basta con que traiga poco texto: en las ediciones digitales hay
    hojas legítimas de dos palabras — la portadilla de cada obra ("LAS
    NUBES"), el rótulo "Notas", cada nota final suelta — y contándolas
    como escaneos salían 393 hojas «sin reconocer» de un tomo que está
    entero (Aristófanes II, 2026-07-29). Lo que delata a un escaneo es
    la IMAGEN que cubre la hoja.
    """
    if len(_sin_marcador(texto).strip()) >= 100:
        return False
    try:
        # `get_image_info` no descodifica la imagen (a diferencia de
        # pedir la maqueta entera): en un tomo escaneado eso es la
        # diferencia entre décimas de segundo y medio minuto.
        imagenes = pagina.get_image_info()
    except Exception:  # noqa: BLE001 - una hoja rara no para el análisis
        return False
    caja = pagina.rect
    superficie = max(1.0, caja.width * caja.height)
    for imagen in imagenes:
        x0, y0, x1, y1 = imagen["bbox"]
        if (x1 - x0) * (y1 - y0) / superficie > 0.4:
            return True
    return False


def _calidad(texto: str) -> tuple[str, float]:
    """Estado del texto y legibilidad (% de palabras corrientes)."""
    if len(texto.strip()) < 500:
        return "sin_texto", 0.0
    palabras = re.findall(r"\S+", texto)
    legible = len(_COMUNES.findall(texto)) / max(1, len(palabras)) * 100
    raros = len(_RAROS.findall(texto)) / max(1, len(texto)) * 100
    lineas = [l for l in texto.splitlines() if l.strip()]
    basura = sum(1 for l in lineas if _BASURA.search(l)) / max(1, len(lineas)) * 100
    if legible > 22 and raros < 0.5 and basura < 1:
        return "nativo", legible
    if legible > 15 and raros < 2:
        return "ocr_bueno", legible
    return "ocr_sucio", legible


def _tipo_seccion(titulo: str) -> str:
    """Clasifica el título de una sección; 'texto' si es contenido."""
    t = collection.normalize(titulo)
    for tipo, patron in _SECCIONES:
        if re.search(patron, t):
            return tipo
    # Los rótulos de los escaneos van con las letras espaciadas
    # ("Í N D I C E  D E  T O P Ó N I M O S"): se prueba otra vez sin
    # espacios, contra el mismo patrón también sin ellos.
    apretado = t.replace(" ", "")
    for tipo, patron in _SECCIONES:
        if re.search(patron.replace(" ", ""), apretado):
            return tipo
    return "texto"


def _saca_referencias(lineas: list[str]) -> tuple[list[str], list[str]]:
    """
    Separa las referencias del margen del texto de la hoja.

    El conversor de las ediciones digitales las agrupa al PRINCIPIO de
    la hoja ("229D / 229E / 230A / daño quien la toma, pero…"), donde no
    hay margen que valga. Se cogen solo las de esa cabecera: una cifra
    suelta en mitad del texto puede ser del propio texto.
    """
    referencias: list[str] = []
    i = 0
    while i < len(lineas) and _REF_MARGINAL.match(lineas[i].strip()):
        referencias.append(lineas[i].strip())
        i += 1
    return lineas[i:], referencias


def texto_para_buscar(registro: dict) -> str:
    """
    El texto de una hoja en una sola línea, listo para buscar en él.

    El texto se guarda RENGLÓN A RENGLÓN, como está en el tomo, porque
    en los poetas y los cómicos el renglón ES el verso y unirlos sería
    perder la unidad con la que se cita. Pero entonces una frase de más
    de ocho palabras se parte en dos y no se encuentra buscándola
    literalmente (renglón medio del corpus: 64 caracteres). Se resuelve
    aquí, al buscar, y no rompiendo el texto al guardarlo.
    """
    partes = [registro.get("cuerpo", ""), registro.get("notas", "")]
    return " ".join(" ".join(partes).split())


# ----------------------------------------------------------------------
# Texto latino leído con la tabla del GRIEGO (mojibake cp1253)
# ----------------------------------------------------------------------
# Varios PDF traen el texto castellano con las vocales acentuadas
# convertidas en letras griegas: "compaρeros", "mαs", "tambiιn",
# "estαn", "caballerνa", "HIERΣN". No es que el reconocimiento las
# leyera mal: es que los bytes latinos se descodificaron con la tabla de
# códigos del griego (Windows-1253). Se comprobó carácter a carácter y
# encaja en los siete casos medidos, así que la reparación es EXACTA y
# no una adivinanza: basta con volver a leer ese byte como Latin-1.
_GRIEGO_A_LATIN = {}
for _b in range(0x80, 0x100):
    try:
        _GRIEGO_A_LATIN[bytes([_b]).decode("cp1253")] = bytes([_b]).decode(
            "latin-1"
        )
    except UnicodeDecodeError:
        continue
# Y una excepción medida aparte: en esos mismos tomos la "ú" salió como
# el dígito árabe ٥ (370 casos: "n٥mero", "seg٥n", "p٥blico"), que no
# viene de la tabla griega sino de la propia fuente del PDF.
_GRIEGO_A_LATIN["٥"] = "ú"
_GRIEGO_A_LATIN["٤"] = "Ú"

# Solo se toca lo que reconstruye una letra ESPAÑOLA: la tabla completa
# convertiría también griego legítimo en signos raros.
_ESPANOLAS = set("áéíóúñüÁÉÍÓÚÑÜ¿¡")
_MOJIBAKE = {g: l for g, l in _GRIEGO_A_LATIN.items() if l in _ESPANOLAS}
_HAY_MOJIBAKE = re.compile("[" + "".join(_MOJIBAKE) + "]")
_LATINA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")

# El aviso de que una palabra es griega DE VERDAD y no hay que tocarla.
# La tabla de arriba solo abarca 18 letras (Α Ι Ν Ρ Σ α ι ν ρ σ…), así
# que a una palabra griega auténtica le quedan otras 300 con las que
# delatarse: δ, ε, υ, ς, ω, el politónico entero. Se genera de las
# tablas de Unicode, nunca a mano, igual que `_GRIEGO_A_LATIN`.
_GRIEGAS_INEQUIVOCAS = "".join(
    c for c in (chr(_cp) for _cp in
                list(range(0x370, 0x400)) + list(range(0x1F00, 0x2000)))
    if c.isalpha() and c not in _MOJIBAKE
)
_GRIEGA = re.compile("[" + re.escape(_GRIEGAS_INEQUIVOCAS) + "]")
# Caracteres invisibles que el conversor deja sueltos y ensucian tanto
# la lectura como la búsqueda.
_INVISIBLES = str.maketrans({
    " ": " ",      # espacio duro: salía entre TODAS las palabras
    "⁠": "",       # juntador de palabras
    "​": "",       # espacio de anchura cero
    "﻿": "",
})


def repara_mojibake(texto: str) -> str:
    """
    Devuelve las letras españolas que se habían vuelto griegas.

    Se trabaja PALABRA A PALABRA y hacen falta DOS condiciones, porque
    con una sola se estropeaba griego bueno:

    1. Que la palabra YA tenga alguna letra latina. Una palabra griega
       ("ἀλλήλων", "λόγος") no lleva ninguna. Eso deja fuera también las
       transcripciones con macron (phýsis, gnōsis) y el alemán o el
       francés de la bibliografía, que son correctos.
    2. Que NO tenga ninguna letra griega de las inequívocas. Hace falta
       porque el reconocimiento confunde la Ómicron griega con la O
       latina, y entonces la condición 1 se cumple dentro de una palabra
       griega entera: "᾿Oδυσσεύς" acababa como "᾿Oδυóóεύς", con las dos
       sigmas convertidas en oes acentuadas (medido en el corpus,
       2026-08-09). La delatan las otras letras —δ, ε, υ, ς—, que no
       están en la tabla de sustituciones.
    """
    if not texto or not _HAY_MOJIBAKE.search(texto):
        return texto
    salida = []
    for trozo in re.split(r"(\s+)", texto):
        if (
            trozo and not trozo.isspace()
            and _HAY_MOJIBAKE.search(trozo)
            and _LATINA.search(trozo)
            and not _GRIEGA.search(trozo)
        ):
            trozo = "".join(_MOJIBAKE.get(c, c) for c in trozo)
        salida.append(trozo)
    return "".join(salida)


# ----------------------------------------------------------------------
# Los dos alfabetos revueltos (reparación especializada del corpus)
# ----------------------------------------------------------------------
# En una colección grecorromana, una palabra que mezcla letras griegas y
# latinas puede ser CUATRO cosas distintas, y cada una pide lo contrario
# que la anterior. Medido sobre los 210 tomos extraídos (2026-08-09):
#
#   1. ETIQUETA GEOMÉTRICA (8.339 casos, casi todos de Euclides). Los
#      Elementos nombran los puntos de cada figura con letras griegas, y
#      el PDF puso latinas donde la mayúscula griega se ve idéntica:
#      "ABΓ" por ΑΒΓ, "el cuadrado ΣN" por ΣΝ. Que Σ (griega) y N
#      (latina) convivan DENTRO de la misma etiqueta es la prueba de que
#      la etiqueta es griega y la N es la intrusa.  → todo a griego.
#   2. CANTO HOMÉRICO (18 casos, Apolonio Díscolo). Los griegos numeran
#      los cantos de la Ilíada con las 24 letras: "(Ι 649)" es el canto
#      IX, verso 649. Es griego correcto.  → no se toca.
#   3. NUMERAL ROMANO (12 casos). La misma iota, pero ocupando el sitio
#      de la I en un rótulo español: "ΙII. contenido y estilo",
#      "Ι-ΙΙ-ΙΙΙ LOS OLINTÍACOS".  → todo a latín.
#   4. MOJIBAKE cp1253 (los de arriba): "INTRODUCCIΣN".  → a español.
#
# Lo que separa 1-2-3 de 4 es si TODA letra latina de la palabra tiene
# gemela griega: "INTRODUCCIΣN" trae D, R, U, C, que no la tienen, así
# que es una palabra española con una intrusa; "ABΓ" solo trae A y B,
# que sí, así que es griega. Y lo que separa 2 de 3 es la línea: si
# alrededor manda el griego, la letra es griega.

# Mayúsculas latinas cuya gemela griega es indistinguible a la vista.
# SOLO mayúsculas: en minúscula el parecido es menor y el riesgo de
# tocar una palabra española corriente, mucho mayor.
_LATINA_A_GRIEGA = {
    "A": "Α", "B": "Β", "E": "Ε", "Z": "Ζ", "H": "Η", "I": "Ι", "K": "Κ",
    "M": "Μ", "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "Y": "Υ", "X": "Χ",
}
_GRIEGA_A_LATINA = {g: l for l, g in _LATINA_A_GRIEGA.items()}

# Vocales griegas con tono que el PDF usa por la vocal acentuada
# española. Va ANTES que la tabla de cp1253, que para estas dice otra
# cosa (ό→ü, ά→Ü): "Actόrida" es "Actórida", no "Actürida".
_TONO_A_ESPANOLA = {"ά": "á", "έ": "é", "ί": "í", "ό": "ó", "ύ": "ú",
                    "Ά": "Á", "Έ": "É", "Ί": "Í", "Ό": "Ó", "Ύ": "Ú"}
_ROMANO = re.compile(r"^[IVXLCDM]+$")
_ALGUNA_GRIEGA = re.compile(r"[Ͱ-Ͽἀ-῿]")
_SOLO_LATINA = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
# Una racha de letras seguidas. Se trabaja así y no por trozos separados
# por espacios porque los signos van pegados: "ΙII." y "Ι-ΙΙ-ΙΙΙ" no
# pasarían la prueba del numeral con el punto y los guiones dentro.
_RACHA_DE_LETRAS = re.compile(r"[^\W\d_]+", re.UNICODE)


# Una etiqueta escrita en mayúsculas griegas de verdad (ΑΒΓ, ΔΕΖ, ΣΝ),
# con al menos una letra que no tiene gemela latina. Es la firma de un
# texto de figuras, y solo la dan los tres tomos de Euclides.
_ETIQUETA_EN_GRIEGO = re.compile(r"^[Α-Ω]{2,5}$")
_SIN_GEMELA_LATINA = set("ΓΔΘΛΞΠΣΦΨΩ")
# A partir de cuántas etiquetas griegas se da un tomo por matemático.
_TOMO_DE_FIGURAS = 50


def _manda_el_griego(linea: str) -> bool:
    """¿En esta línea hay más letra griega que latina?"""
    griegas = sum(1 for c in linea if _ALGUNA_GRIEGA.match(c))
    latinas = sum(1 for c in linea if _SOLO_LATINA.match(c))
    return griegas > latinas


def cuenta_etiquetas_de_figura(texto: str) -> int:
    """
    Cuántas etiquetas en mayúsculas GRIEGAS trae el texto.

    Sirve para saber si un tomo es matemático antes de repararlo: solo
    en ellos una racha de mayúsculas latinas sueltas ("AB", "EK") es una
    etiqueta y no una palabra.
    """
    return sum(
        1 for r in _RACHA_DE_LETRAS.findall(texto)
        if _ETIQUETA_EN_GRIEGO.match(r) and _SIN_GEMELA_LATINA & set(r)
    )


def _parece_etiqueta(racha: str) -> bool:
    """
    Una racha de mayúsculas latinas que, en un tomo de figuras, es el
    nombre de un punto o de una figura ("AB", "EKZ").

    Los tres filtros están medidos sobre el corpus, y cada uno tapa lo
    que colaba el anterior:
      · de 2 a 4 letras — deja fuera HEATH y MAZON, los editores de
        Euclides y de la Ilíada, que salen en la bibliografía;
      · todas distintas — los vértices de una figura no se repiten, así
        que fuera TITO y HAHN;
      · que no sea un numeral romano — II, XII, XXIII son cifras.
    """
    return (
        2 <= len(racha) <= 4
        and racha.isupper()
        and all(c in _LATINA_A_GRIEGA for c in racha)
        and len(set(racha)) == len(racha)
        and not _ROMANO.match(racha)
    )


def _repara_palabra(palabra: str, linea_griega: bool,
                    etiquetas: bool = False) -> str:
    """Decide cuál de las cuatro cosas es esta palabra y la arregla."""
    if not _ALGUNA_GRIEGA.search(palabra):
        if etiquetas and _parece_etiqueta(palabra):
            return "".join(_LATINA_A_GRIEGA.get(c, c) for c in palabra)
        return palabra
    latinas = [c for c in palabra if _SOLO_LATINA.match(c)]
    griegas = [c for c in palabra if _ALGUNA_GRIEGA.match(c)]

    # Rama LATINA: manda el latín DENTRO de la palabra y alguna de sus
    # letras no tiene gemela griega, así que la palabra es española y la
    # griega es la intrusa.
    #
    # Lo de «manda el latín» hace falta por las palabras GRIEGAS que ya
    # traen intrusas latinas minúsculas: "Πρòς" (con ò latina), "κομίζoí"
    # (o e i latinas). Sin contar, entraban por aquí y la regla de la
    # vocal con tono les cambiaba los acentos griegos por españoles
    # —"Πñòς", "κομíζoí"—, que es estropear griego bueno para arreglar
    # español que no estaba roto (medido en el corpus, 2026-08-09).
    if len(latinas) > len(griegas) and any(
            c not in _LATINA_A_GRIEGA for c in latinas):
        salida = []
        for i, c in enumerate(palabra):
            if c in _TONO_A_ESPANOLA:
                # La única «ü» del español va en güe/güi; si el hueco es
                # ese, manda la tabla de cp1253 y no el parecido.
                antes = palabra[i - 1] if i else ""
                despues = palabra[i + 1] if i + 1 < len(palabra) else ""
                if antes in "gG" and despues in "eiéíEI":
                    salida.append(_MOJIBAKE.get(c, c))
                else:
                    salida.append(_TONO_A_ESPANOLA[c])
            else:
                salida.append(_MOJIBAKE.get(c, c))
        return "".join(salida)

    # Rama GRIEGA: o es una etiqueta/palabra griega, o un numeral romano
    # escrito con las gemelas. Lo dice la línea de alrededor.
    latinizada = "".join(_GRIEGA_A_LATINA.get(c, c) for c in palabra)
    if not linea_griega and _ROMANO.match(latinizada):
        return latinizada
    return "".join(_LATINA_A_GRIEGA.get(c, c) for c in palabra)


def repara_alfabetos(texto: str, etiquetas: bool = False) -> str:
    """
    Devuelve cada letra al alfabeto que le toca.

    Trabaja línea a línea —hace falta el contexto para saber si una iota
    suelta es el canto IX de la Ilíada o la I de un numeral romano— y
    dentro de cada línea, palabra a palabra.

    `etiquetas` solo se enciende en un tomo MATEMÁTICO (lo dice
    `cuenta_etiquetas_de_figura`): allí "AB" y "EK" son los nombres de
    dos puntos y van en griego, mientras que en cualquier otro tomo
    serían palabras y tocarlas sería estropearlas.

    Incluye lo que hacía `repara_mojibake`, así que sustituye a esa
    llamada; se conserva la otra porque es la tabla de cp1253 pura y hay
    pruebas que la comprueban por su cuenta.
    """
    if not texto:
        return texto
    if not etiquetas and not _ALGUNA_GRIEGA.search(texto):
        return texto
    lineas = []
    for linea in texto.split("\n"):
        if not etiquetas and not _ALGUNA_GRIEGA.search(linea):
            lineas.append(linea)
            continue
        manda = _manda_el_griego(linea)
        lineas.append(_RACHA_DE_LETRAS.sub(
            lambda m: _repara_palabra(m.group(0), manda, etiquetas), linea))
    return "\n".join(lineas)


def limpia_invisibles(texto: str) -> str:
    """
    Quita los caracteres que no se ven pero estorban.

    El espacio duro (U+00A0) venía entre TODAS las palabras de algunos
    tomos —de ahí ese texto tan desparramado— y el guion opcional
    (U+00AD) partía palabras por la mitad al final de renglón.
    """
    if not texto:
        return texto
    texto = texto.translate(_INVISIBLES)
    # Guion opcional al saltar de línea: se une la palabra, pero SOLO si
    # al otro lado sigue una letra. En estas ediciones el renglón
    # siguiente empieza a veces por el número de verso ("qui¬\n5
    # sieran") y unir a ciegas daba "qui5 sieran".
    texto = re.sub(r"­[ \t]*\n[ \t]*(?=[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", "", texto)
    return texto.replace("­", "")


def _junta_guiones(lineas: list[str]) -> str:
    """Recompone las palabras partidas al final de línea."""
    salida: list[str] = []
    for linea in lineas:
        if salida and salida[-1].endswith(("-", "­")) and linea[:1].islower():
            salida[-1] = salida[-1][:-1] + linea.lstrip()
        else:
            salida.append(linea)
    return "\n".join(salida)


def nombre_para_buscar(nombre_archivo: str) -> str:
    """Nombre de archivo → texto apto para cruzar con la colección."""
    # Los nombres traen coletillas de la edición digital: "(trad. Ramón
    # Bach)", "(ed. bilingüe)". Diluían la semejanza y el tomo se
    # quedaba a un pelo del umbral (Anábasis, 2026-07-29).
    texto = re.sub(
        r"\btrad(?:\.|ucci[óo]n|ucido)?\s*(?:de\s+)?"
        r"(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*[\s.]*){0,3}",
        " ", nombre_archivo, flags=re.I,
    )
    texto = re.sub(r"[()]", " ", texto)
    texto = re.sub(r"[_\-.]+", " ", texto)
    # Sin \b: en los nombres reales van pegadas ("EditorialGredos")
    texto = re.sub(
        r"(pdf|compress|gredos|editorial|madrid|biblioteca|"
        r"cl[áa]sica|clasica)", " ", texto, flags=re.I,
    )
    texto = re.sub(r"\b(vol\s*\d*|tomo)\b", " ", texto, flags=re.I)
    texto = re.sub(r"\b(19|20)\d\d\b|\[|\]|\bG\b", " ", texto)
    return " ".join(texto.split())


class _Lector:
    """Lee un PDF y saca de él todo lo que hace falta."""

    def __init__(self, ruta: Path, progreso=None) -> None:
        import fitz

        self.ruta = Path(ruta)
        self.doc = fitz.open(self.ruta)
        # Sin las imágenes, extraer la maqueta de una página escaneada
        # es TREINTA veces más rápido (medido en Moralia X: 3,48 s →
        # 0,11 s por 60 páginas) y el texto sale idéntico. Las hojas que
        # son solo imagen se detectan aparte, con `get_images`.
        self._flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
        self._cache: dict[int, str] = {}
        self.encabezados: set[str] = set()
        self.desfase: Optional[int] = None
        self.cuerpo_pt = 0.0
        self.secciones: list[dict] = []
        self.obras: list[tuple] = []      # (página, título) de cada obra
        self.formato = "impreso"
        self._cola: Optional[int] = None  # hoja donde empiezan las notas
        self.dificultades: list[str] = []
        # `progreso(fase, hechas, total)`; total 0 = no se sabe cuánto
        # queda. Un tomo de 600 páginas tarda lo suyo: la interfaz tiene
        # que poder enseñar por dónde va.
        self._progreso = progreso

    def aviso(self, fase: str, hechas: int = 0, total: int = 0) -> None:
        if self._progreso is not None:
            self._progreso(fase, hechas, total)

    def texto(self, i: int) -> str:
        """Texto plano de una hoja, extraído UNA sola vez."""
        if i not in self._cache:
            self._cache[i] = self.doc[i].get_text()
        return self._cache[i]

    def cerrar(self) -> None:
        self.doc.close()

    # --- medidas previas ----------------------------------------------
    def detecta_formato(self) -> str:
        """
        ¿Papel escaneado/maquetado o libro electrónico pasado a PDF?

        Se mira el pie de una muestra de hojas: en las ediciones
        digitales TODAS acaban en "Página N". Distinguirlo importa
        mucho — en ese formato no hay folio impreso que cuadrar, los
        números del margen son VERSOS y las hojas cortas son normales,
        no escaneos fallidos.
        """
        n = len(self.doc)
        paso = max(1, n // 40)
        muestreadas = marcadas = 0
        for i in range(0, n, paso):
            lineas = [
                l.strip() for l in self.texto(i).splitlines()
                if l.strip()
            ]
            if not lineas:
                continue
            muestreadas += 1
            marcadas += bool(_MARCADOR_EBOOK.match(lineas[-1]))
        if muestreadas and marcadas / muestreadas >= 0.6:
            self.formato = "ebook"
        elif self.cola_de_notas() and not self._hay_folios():
            # Otros conversores no ponen pie ninguno. Los delata la cola
            # de notas a hoja por nota sin un solo folio impreso
            # (Ovidio y Luciano, 2026-07-29).
            self.formato = "ebook"
        return self.formato

    def _hay_folios(self) -> bool:
        """¿Se leen números de página impresos en los márgenes?"""
        n = len(self.doc)
        vistos = 0
        for i in range(n // 4, min(n, n // 4 + 60), 3):
            pagina = self.doc[i]
            alto = pagina.rect.height
            for b in pagina.get_text("dict", flags=self._flags)["blocks"]:
                for l in b.get("lines", []):
                    y = l["bbox"][1]
                    txt = "".join(s["text"] for s in l["spans"]).strip()
                    if (y < alto * 0.09 or y > alto * 0.91) and _FOLIO.match(txt):
                        vistos += 1
        return vistos >= 4

    def cola_de_notas(self) -> int:
        """
        Hoja donde empieza la cola de NOTAS FINALES, o 0 si no hay.

        En estas ediciones cada nota ocupa su propia hoja: 1.072 de las
        1.436 de Aristófanes II, 650 de las 867 del Juliano. Se detecta
        por el texto ("[65] Deuteronomio 32, 9."), no por el índice
        interno: muchos volúmenes no traen entrada para las notas y el
        tomo salía con "867 hojas de texto" (2026-07-29).
        """
        if self._cola is not None:
            return self._cola
        n = len(self.doc)
        inicio = 0
        fallos = 0
        for i in range(n - 1, max(0, int(n * 0.25)) - 1, -1):
            texto = _sin_marcador(self.texto(i)).strip()
            if _NOTA_SUELTA.match(texto):
                inicio = i + 1
                fallos = 0
            elif not texto or len(texto) < 40:
                continue              # rótulos y hojas de corte
            else:
                fallos += 1
                if fallos >= 4:       # ya estamos en el cuerpo del tomo
                    break
        # Una cola de verdad son decenas de hojas, no cuatro notas
        self._cola = inicio if inicio and (n - inicio) >= 20 else 0
        return self._cola

    def obras_del_indice(self) -> list[tuple]:
        """
        Obras del volumen según el índice interno (nivel 1).

        Un tomo puede traer varias ("Comedias II" son cuatro): sin esto
        no se puede decir A QUÉ obra pertenece una cita.
        """
        obras = [
            (pag, titulo.strip())
            for nivel, titulo, pag in self.doc.get_toc()
            if nivel == 1 and pag > 0 and _tipo_seccion(titulo) == "texto"
        ]
        # La primera entrada suele ser la PORTADA con el título del
        # volumen ("Comedias II"), que no es una obra.
        if obras and obras[0][0] <= 4:
            obras = obras[1:]
        self.obras = obras
        return self.obras

    def obra_de(self, pagina_pdf: int) -> str:
        titulo = ""
        for pag, nombre in self.obras:
            if pag <= pagina_pdf:
                titulo = nombre
            else:
                break
        return titulo

    def limite_texto(self) -> int:
        """
        Última hoja de TEXTO: donde empieza la cola de notas.

        Medir el tomo con sus notas finales lo desfigura: en Vidas
        paralelas VII, 1.524 de 1.885 hojas son notas (letra menor y
        llenas de abreviaturas), y muestrear ahí daba "ocr sucio" en un
        texto nativo y un cuerpo de 10,8 pt que era el de las notas
        (2026-07-29).
        """
        return self.cola_de_notas() or len(self.doc)

    def muestra(self) -> str:
        """
        Texto de referencia para medir la calidad.

        Se cogen las hojas CON MÁS TEXTO del centro del libro: en las
        ediciones digitales el cuarto del libro puede caer en las notas
        finales (una línea por hoja) y el tomo entero se tomaba por un
        reconocimiento sucio (Aristófanes II, 2026-07-29).
        """
        n = self.limite_texto()
        paso = max(1, n // 120)
        textos = []
        for i in range(n // 5, min(n, int(n * 0.85)), paso):
            texto = _sin_marcador(self.texto(i))
            if len(texto.strip()) > 800:
                textos.append(texto)
            if len(textos) >= 25:
                break
        if not textos:                    # libro corto o casi sin texto
            textos = [
                _sin_marcador(self.texto(i))
                for i in range(n // 4, min(n // 4 + 25, n))
            ]
        return "\n".join(textos)

    def tam_cuerpo(self) -> float:
        tam: Counter = Counter()
        n = self.limite_texto()
        for i in range(n // 3, min(n // 3 + 40, n)):
            for b in self.doc[i].get_text("dict", flags=self._flags)["blocks"]:
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        tam[round(s["size"], 1)] += len(s["text"])
        self.cuerpo_pt = tam.most_common(1)[0][0] if tam else 0.0
        if len(tam) < 2:
            self.dificultades.append(
                "no se distinguen las notas al pie: van con el mismo "
                "tamaño de letra que el texto"
            )
        return self.cuerpo_pt

    def busca_encabezados(self) -> set[str]:
        cuenta: Counter = Counter()
        n = len(self.doc)
        paso = max(1, n // 120)
        muestreadas = 0
        for i in range(0, n, paso):
            pagina = self.doc[i]
            alto = pagina.rect.height
            muestreadas += 1
            self.aviso("Buscando los encabezados", i + 1, n)
            for b in pagina.get_text("dict", flags=self._flags)["blocks"]:
                for l in b.get("lines", []):
                    y = l["bbox"][1]
                    if y < alto * 0.08 or y > alto * 0.92:
                        txt = "".join(s["text"] for s in l["spans"]).strip()
                        if 3 < len(txt) < 60 and not txt.isdigit():
                            cuenta[collection.normalize(txt)] += 1
        minimo = max(3, muestreadas * 0.12)
        self.encabezados = {t for t, c in cuenta.items() if c >= minimo}
        return self.encabezados

    def calcula_desfase(self) -> Optional[int]:
        desfases: Counter = Counter()
        n = len(self.doc)
        for i in range(n // 4, min(n, n // 4 + 150), 3):
            pagina = self.doc[i]
            alto = pagina.rect.height
            for b in pagina.get_text("dict", flags=self._flags)["blocks"]:
                for l in b.get("lines", []):
                    y = l["bbox"][1]
                    txt = "".join(s["text"] for s in l["spans"]).strip()
                    if (y < alto * 0.09 or y > alto * 0.91) and _FOLIO.match(txt):
                        desfases[(i + 1) - int(txt)] += 1
        if not desfases:
            self.dificultades.append(
                "no se leen los números de página impresos: las citas irán "
                "por página del PDF"
            )
            return None
        mejor, veces = desfases.most_common(1)[0]
        if veces < 4:
            self.dificultades.append(
                "los números de página impresos apenas se leen: la "
                "correspondencia con el papel puede fallar"
            )
        self.desfase = mejor
        return mejor

    def busca_secciones(self) -> list[dict]:
        marcas: list[tuple[int, str, str, int]] = []
        for nivel, titulo, pag in self.doc.get_toc():
            if pag and pag > 0:
                marcas.append((pag, titulo.strip(), _tipo_seccion(titulo), nivel))
        # Con índice interno se respetan TODAS sus entradas; solo se
        # funden las deducidas a ojo de los títulos impresos, que salen
        # a docenas. Fundir las del índice dejaba el texto de cada
        # comedia bajo el rótulo de la bibliografía anterior
        # ("LAS NUBES" caía dentro de "Ediciones, traducciones y
        # comentarios", Aristófanes II 2026-07-29).
        del_indice = bool(marcas)
        if not marcas:
            self.dificultades.append(
                "el PDF no trae índice interno: las secciones se deducen "
                "de los títulos impresos y pueden quedar aproximadas"
            )
            marcas = [(p, t, tipo, 1) for p, t, tipo in self._secciones_por_pagina()]
        marcas.sort()
        secciones: list[dict] = []
        for pag, titulo, tipo, nivel in marcas:
            if secciones and secciones[-1]["titulo"] == titulo:
                continue                      # la misma entrada repetida
            if not del_indice and secciones and secciones[-1]["tipo"] == tipo:
                continue
            if secciones:
                # Dos entradas en la misma hoja: el rango no puede
                # quedar del revés (hasta < desde).
                secciones[-1]["hasta"] = max(secciones[-1]["desde"], pag - 1)
            secciones.append({"desde": pag, "hasta": len(self.doc),
                              "tipo": tipo, "titulo": titulo})
        if not secciones:
            secciones = [{"desde": 1, "hasta": len(self.doc),
                          "tipo": "texto", "titulo": ""}]
        self.secciones = self._con_cola_de_notas(secciones)
        return self.secciones

    def _con_cola_de_notas(self, secciones: list[dict]) -> list[dict]:
        """
        Añade la cola de notas finales si el índice interno la calla.

        Sin ella el tomo decía "867 hojas de texto" cuando 650 son notas
        sueltas (Juliano, 2026-07-29).
        """
        cola = self.cola_de_notas()
        n = len(self.doc)
        if not cola:
            return secciones
        # ¿El índice interno ya la marca? Entonces no se toca nada: sus
        # rótulos ("Notas cartas y fragmentos") valen más que el mío.
        if hojas_de_texto(secciones, n) < n:
            return secciones
        secciones = [s for s in secciones if s["desde"] < cola]
        for seccion in secciones:
            seccion["hasta"] = min(seccion["hasta"], cola - 1)
        secciones.append({"desde": cola, "hasta": n,
                          "tipo": "notas_finales", "titulo": "Notas"})
        return secciones

    def _secciones_por_pagina(self) -> list[tuple[int, str, str]]:
        marcas = []
        total = len(self.doc)
        for i in range(total):
            self.aviso("Deduciendo las secciones", i + 1, total)
            for linea in self.texto(i)[:400].splitlines():
                linea = linea.strip()
                if not (4 < len(linea) < 60):
                    continue
                tipo = _tipo_seccion(linea)
                if tipo != "texto" and linea.upper() == linea:
                    marcas.append((i + 1, linea, tipo))
                    break
        return marcas

    def seccion_de(self, pagina_pdf: int) -> dict:
        for s in self.secciones:
            if s["desde"] <= pagina_pdf <= s["hasta"]:
                return s
        return {"tipo": "texto", "titulo": ""}

    # --- extracción -----------------------------------------------------
    def paginas(self):
        for i in range(len(self.doc)):
            pagina = self.doc[i]
            alto = pagina.rect.height
            ancho = pagina.rect.width
            cuerpo: list[str] = []
            notas: list[str] = []
            versos: list[int] = []
            obra = ""
            maqueta = pagina.get_text("dict", flags=self._flags)["blocks"]
            umbral_nota = _umbral_de_nota(maqueta)
            for b in maqueta:
                for l in b.get("lines", []):
                    spans = l.get("spans", [])
                    if not spans:
                        continue
                    txt = "".join(s["text"] for s in spans).strip()
                    if not txt:
                        continue
                    if _MARCADOR_EBOOK.match(txt):
                        continue          # pie del conversor, no es texto
                    x0, x1 = l["bbox"][0], l["bbox"][2]
                    y = l["bbox"][1]
                    cuerpo_span = max(s["size"] for s in spans)
                    # Numeración de VERSOS: cifra suelta en el margen
                    # lateral y en letra menor. Es la referencia con la
                    # que se citan los poetas y los cómicos, así que se
                    # guarda aparte en vez de ensuciar el texto (en
                    # Aristófanes caía entera en las notas al pie).
                    if (
                        _REF_MARGINAL.match(txt)
                        and (x0 > ancho * 0.72 or x1 < ancho * 0.2)
                        and alto * 0.08 <= y <= alto * 0.92
                        and (not umbral_nota or cuerpo_span < umbral_nota)
                    ):
                        versos.append(txt)
                        continue
                    if y < alto * 0.08 or y > alto * 0.92:
                        if collection.normalize(txt) in self.encabezados:
                            obra = obra or txt
                            continue
                        if _FOLIO.match(txt):
                            continue
                    txt = _BASURA.sub("", txt).strip()
                    if not txt:
                        continue
                    if umbral_nota and cuerpo_span < umbral_nota:
                        notas.append(txt)
                    else:
                        cuerpo.append(txt)
            seccion = self.seccion_de(i + 1)
            if seccion["tipo"] in _SIN_OBRA:
                # Las notas finales y los índices van al final del
                # volumen: heredaban la última obra y quedaban atribuidos
                # a la comedia equivocada. Mejor sin obra que mal puesta.
                obra = ""
            else:
                # Un tomo puede traer varias obras: manda el encabezado
                # impreso y, si no lo hay (ediciones digitales), el índice.
                obra = obra or self.obra_de(i + 1)
            yield {
                "tipo": "pagina",
                "pdf": i + 1,
                "impresa": (i + 1 - self.desfase) if self.desfase is not None else None,
                "seccion": seccion["tipo"],
                "titulo": seccion["titulo"],
                "obra": obra,
                # Cifras del margen, TODAS y EN ORDEN DE LECTURA. En
                # verso son los versos; en prosa, los parágrafos — y así
                # se cita a Isócrates o a Jenofonte. Ordenarlas de menor
                # a mayor destrozaba los tomos con numeración de dos
                # niveles: en Plutarco el margen lleva el capítulo y
                # luego sus secciones (39, 2, 3, 4…) y sacaba
                # "2,3,4,5,6,8,10,11,39" (2026-07-29).
                **_texto_de_hoja(cuerpo, notas, versos, seccion["tipo"]),
            }


def _texto_de_hoja(
    cuerpo: list[str], notas: list[str], versos: list, seccion: str
) -> dict:
    """
    Deja lista una hoja: referencias fuera, párrafos enteros y notas
    con su número.

    Las tres cosas nacen del mismo formato (el 79 % de los tomos son
    ediciones digitales, medido sobre el corpus el 2026-07-29):

    - El conversor suelta las referencias del margen como líneas al
      principio de la hoja, donde no hay margen que mirar.
    - Deja la llamada de nota pegada a la palabra ("escandalizarse[61]
      según"), que parte la frase; se guarda aparte, en `llamadas`.
    - Cada nota final ocupa su propia hoja, encabezada por su número.

    El renglón NO se toca: en los poetas es el verso. Para buscar
    frases largas está `texto_para_buscar`.
    """
    cuerpo, refs_cabecera = _saca_referencias(cuerpo)
    # Siempre texto: la referencia puede llevar letra ("229D", "1094a")
    versos = [str(v) for v in versos] + refs_cabecera
    llamadas: list[str] = []
    nota_num = ""

    if seccion == "notas_finales":
        # Cada nota final ocupa su hoja: se guarda su NÚMERO, para poder
        # citarla y para atarla a la llamada que la invoca.
        entera = " ".join(cuerpo).strip()
        m = _NOTA_CABEZA.match(entera)
        if m:
            nota_num = m.group(1)
            cuerpo = [_NOTA_CABEZA.sub("", entera, count=1)]
        cuerpo = [_VUELTA_NOTA.sub("", l) for l in cuerpo]

    limpio: list[str] = []
    for linea in cuerpo:
        llamadas += _MARCA_NOTA.findall(linea)
        limpio.append(_MARCA_NOTA.sub("", linea))
    salida = {
        "versos": versos,
        "capitulos": _capitulos([int(v) for v in versos if str(v).isdigit()]),
        "llamadas": sorted(dict.fromkeys(llamadas), key=_orden_nota),
        "cuerpo": _junta_guiones(limpio),
        "notas": _junta_guiones(notas),
    }
    if nota_num:
        salida["nota"] = nota_num
    return salida


def _orden_nota(numero: str) -> tuple:
    m = re.match(r"(\d+)([a-z]?)", str(numero))
    return (int(m.group(1)), m.group(2)) if m else (0, "")


def es_pagina_de_indice(cuerpo: str) -> bool:
    """
    ¿Esta página TIENE FORMA de índice de nombres?

    Se comprueba por el contenido y no por lo que diga el marcador del
    PDF: en tomos reales los marcadores mienten (en el Plutarco el
    índice se anunciaba doce páginas antes de donde empieza), y un
    tercio de los PDF ni siquiera los trae.
    """
    lineas = [l.strip() for l in cuerpo.splitlines() if l.strip()]
    if len(lineas) < 8:
        return False
    entradas = sum(
        1 for l in lineas if _ENTRADA.match(l) or _ENTRADA_DESC.match(l)
    )
    refs = sum(1 for l in lineas if _SOLO_REFS.match(l) and any(c.isdigit() for c in l))
    largo_medio = sum(len(l) for l in lineas) / len(lineas)
    return (entradas + refs) / len(lineas) >= 0.45 and largo_medio < 70


def _es_romano(token: str) -> bool:
    """¿"XXIV" (un canto) o "Arat." (una obra)?"""
    return bool(re.fullmatch(_ROMANO, token.rstrip(".")))


def _referencias_por_obra(texto: str, romano_ok: bool = False) -> list[str]:
    """
    Índices que citan OBRA, capítulo y sección, no páginas.

    "ACADEMIA: Ant. 80, 2; Dión 1, 1; 14, 3" →
        ["Ant. 80, 2", "Dión 1, 1", "Dión 14, 3"]

    Así cita el índice de Vidas paralelas, donde un volumen reúne
    varias Vidas y el número de página no sirve de nada: la abreviatura
    manda y se ARRASTRA a las referencias siguientes hasta que aparece
    otra (2026-07-29).
    """
    salida: list[str] = []
    obra = ""
    for trozo in texto.split(";"):
        trozo = trozo.strip().rstrip(".,")
        if not trozo:
            continue
        m = _REF_OBRA.match(trozo)
        if m and (romano_ok or not _es_romano(m.group(1))):
            obra = m.group(1)             # con su punto: "Arat."
            trozo = trozo[m.end():]
        numeros = re.sub(r"\s*,\s*", ", ", trozo.strip())
        if not numeros or not numeros[0].isdigit():
            continue
        salida.append(f"{obra} {numeros}".strip())
    return salida


def _referencias(texto: str) -> list[str]:
    """
    Las localizaciones de una entrada, ya normalizadas.

    "III 189; VI 186"  → ["III 189", "VI 186"]   (canto y verso)
    "12, 45-48"        → ["12", "45-48"]         (páginas)
    "Ant. 80, 2; 54,5" → ["Ant. 80, 2", "Ant. 54, 5"]  (obra y capítulo)

    Los números altos SIN romano delante se descartan: son años de la
    bibliografía ("Bolonia, 1474"), no páginas.
    """
    # Ojo: un canto ("III 189") también es una palabra en mayúsculas
    # delante de una cifra. Solo se cambia de estrategia si hay una
    # abreviatura de obra DE VERDAD.
    if any(
        not _es_romano(m.group(1)) for m in _REF_OBRA.finditer(texto)
    ):
        return _referencias_por_obra(texto)
    salida: list[str] = []
    romano_actual = ""
    for romano, numero in _GRUPO_REF.findall(texto):
        if romano:
            romano_actual = romano
        numero = re.sub(r"\s*[-–]\s*", "-", numero.strip())
        primero = int(re.match(r"\d+", numero).group())
        if not romano_actual and primero > _MAX_PAGINA:
            continue
        salida.append(f"{romano_actual} {numero}".strip())
    return salida


def _texto_completo(reg: dict) -> str:
    """
    Cuerpo Y notas de una página.

    En las páginas de índice el texto va compuesto en cuerpo MENOR que
    el del volumen, así que el separador de notas se lo lleva entero
    (medido en el Jenofonte: índice a 12,9 pt frente a 15,4 del texto).
    Para el índice, ese "cuerpo menor" ES el contenido.
    """
    return (reg.get("cuerpo", "") + "\n" + reg.get("notas", "")).strip()


def paginas_de_indice(registros: list[dict]) -> list[dict]:
    """
    Páginas que forman el índice de nombres.

    Se ANCLA en el rótulo ("ÍNDICE DE NOMBRES") y sigue mientras las
    páginas conserven la forma de índice. Sin ancla no se acepta nada:
    la bibliografía tiene exactamente la misma forma (autor, coma,
    números) y colaba entradas falsas —"Bolonia, U. Rugerius, 1474"—
    en tomos que ni siquiera llevan índice de nombres.
    """
    anclas = set()
    for i, reg in enumerate(registros):
        texto = _texto_completo(reg)
        # Sin espacios: el rótulo puede venir con las letras separadas
        cabeza = collection.normalize(texto[:300]).replace(" ", "")
        if _ROTULO_INDICE.search(cabeza) or reg["seccion"] == "indice_nombres":
            anclas.add(i)
    if not anclas:
        return []
    elegidas: dict[int, dict] = {}
    for i in sorted(anclas):
        j = i
        while j < len(registros):
            texto = _texto_completo(registros[j])
            if j != i and not es_pagina_de_indice(texto):
                break
            elegidas[j] = registros[j]
            j += 1
    return [elegidas[k] for k in sorted(elegidas)]


def indice_de_nombres(registros: list[dict]) -> dict[str, list[str]]:
    """
    Convierte el índice de nombres del tomo en un mapa
    `nombre → localizaciones`.

    Es la joya del volumen: una concordancia hecha y verificada por el
    traductor, que permite responder "¿qué tomo habla de los
    lacedemonios?" sin necesidad de ninguna IA. Admite las dos formas
    de citar de la colección (página, o canto y verso) y las entradas
    partidas en varias líneas por la maquetación a dos columnas.
    """
    paginas_indice = paginas_de_indice(registros)
    mapa: dict[str, list[str]] = {}
    nombre: Optional[str] = None
    acumulado = ""
    estilo = "clasico"
    hambre = 0            # líneas que aún puede esperar por sus cifras

    def cerrar() -> None:
        nonlocal nombre, acumulado, estilo
        if nombre and acumulado:
            refs = (
                _referencias_por_obra(acumulado, romano_ok=True)
                if estilo == "libro" else _referencias(acumulado)
            )
            if refs:
                mapa.setdefault(nombre, []).extend(refs)
        nombre, acumulado, estilo = None, "", "clasico"

    for reg in paginas_indice:
        for linea in _texto_completo(reg).splitlines():
            linea = linea.strip()
            if not linea:
                continue
            # Entrada abierta que todavía no ha soltado ninguna cifra:
            # en el índice DESCRIPTIVO la localización va detrás de la
            # explicación, que ocupa una o dos líneas más ("Ábaris:
            # guerrero de Fineo, muerto por / Perseo, V 86").
            if nombre and hambre and not _referencias(acumulado):
                acumulado += " " + linea
                hambre -= 1
                continue
            # "Sábata XVI, 4,2": el LIBRO en romano hace de separador,
            # sin coma tras el nombre (Estrabón). Se mira PRIMERO: si no,
            # la regla clásica se lleva el romano dentro del nombre.
            libro = ""
            m = _ENTRADA_LIBRO.match(linea)
            if m is not None:
                libro = m.group(2)
            else:
                m = _ENTRADA.match(linea) or _ENTRADA_DESC.match(linea)
            if m:
                cerrar()
                candidato = " ".join(m.group(1).split()).strip(" .,;:")
                if len(candidato) < 2 or _REMISION.search(candidato):
                    continue
                if _es_romano(candidato):
                    # Línea de continuación que el OCR partió ("XV I,4,
                    # 14" por "XVI, 4, 14"): un romano no es un nombre.
                    continue
                nombre = candidato
                estilo = "libro" if libro else "clasico"
                acumulado = (f"{libro} " if libro else "") + linea[m.end():]
                hambre = 2          # dos líneas de margen para las cifras
                continue
            if nombre and _SOLO_REFS.match(linea) and any(c.isdigit() for c in linea):
                acumulado += " " + linea
            else:
                cerrar()
        cerrar()          # el índice sigue en la página siguiente
    return {
        n: sorted(dict.fromkeys(refs))
        for n, refs in mapa.items() if refs
    }


def analizar(
    ruta: Path, tomos: list, tomo_esperado=None, progreso=None, cancelado=None
) -> Analisis:
    """
    Analiza un PDF SIN guardarlo: devuelve su texto estructurado y la
    lista de dificultades encontradas, para que el usuario decida.

    `progreso(fase, hechas, total)` va contando el avance para la barra
    de la interfaz; con `total` a 0 la fase no se puede medir.
    `cancelado()` corta el trabajo (un tomo de 600 páginas son varios
    segundos de proceso: cerrar la ventana no debe dejarlo corriendo).
    """
    ruta = Path(ruta)

    def aviso(fase: str, hechas: int = 0, total: int = 0) -> None:
        if cancelado is not None and cancelado():
            raise AnalisisCancelado(fase)
        if progreso is not None:
            progreso(fase, hechas, total)

    aviso("Abriendo el PDF")
    res = Analisis(
        archivo=ruta.name,
        sha1=hashlib.sha1(ruta.read_bytes()).hexdigest(),
    )
    # El lector recibe `aviso`, no `progreso`: así la cancelación llega
    # también a sus barridos largos (encabezados, secciones).
    lector = _Lector(ruta, progreso=aviso)
    try:
        res.paginas_pdf = len(lector.doc)
        aviso("Comprobando si trae texto")
        res.formato = lector.detecta_formato()
        res.estado, res.legibilidad = _calidad(lector.muestra())
        res.tomo_detectado = collection.match_tomo(
            tomos, nombre_para_buscar(ruta.stem)
        )
        if res.estado == "sin_texto":
            res.dificultades.append(
                "es un escaneo: no contiene texto, solo imágenes"
            )
            return res

        total = len(lector.doc)
        sin_texto = []
        for i in range(total):
            aviso("Revisando página a página", i + 1, total)
            pagina = lector.doc[i]
            if _es_hoja_escaneada(pagina, pagina.get_text()):
                sin_texto.append(i + 1)
        res.paginas_sin_texto = sin_texto
        aviso("Midiendo la letra del cuerpo")
        res.cuerpo_pt = lector.tam_cuerpo()
        lector.busca_encabezados()
        if res.formato == "ebook":
            # No hay folio impreso que cuadrar: las cifras del margen
            # son VERSOS y daban desfases absurdos (949 en Aristófanes).
            res.desfase_folio = None
            lector.dificultades.append(
                "es una edición digital (EPUB pasado a PDF): no lleva la "
                "página del papel, así que se cita por obra, sección y "
                "número de verso"
            )
        else:
            aviso("Cuadrando los números de página")
            res.desfase_folio = lector.calcula_desfase()
        aviso("Buscando las secciones")
        res.obras = [titulo for _pag, titulo in lector.obras_del_indice()]
        res.secciones = lector.busca_secciones()
        # Cuántas páginas tiene el tomo DE VERDAD (el Excel de la
        # colección): en una edición digital no coincide con las hojas.
        referencia = tomo_esperado or res.tomo_detectado
        res.paginas_impresas = int(getattr(referencia, "paginas", 0) or 0)
        res.registros = []
        for registro in lector.paginas():
            res.registros.append(registro)
            aviso("Extrayendo el texto", registro["pdf"], total)
        res.palabras = sum(len(r["cuerpo"].split()) for r in res.registros)
        aviso("Leyendo el índice de nombres")
        res.nombres = indice_de_nombres(res.registros)
        res.dificultades = list(lector.dificultades)

        if res.estado == "ocr_sucio":
            res.dificultades.append(
                "el texto viene de un reconocimiento óptico con muchos "
                "errores: las búsquedas literales fallarán a menudo"
            )
        elif res.estado == "ocr_bueno":
            res.dificultades.append(
                "el texto viene de un reconocimiento óptico: puede tener "
                "erratas sueltas"
            )
        if not res.nombres:
            res.dificultades.append(
                "no se ha encontrado índice de nombres: se podrá buscar en "
                "el texto, pero sin la concordancia del traductor"
                + (
                    " (las ediciones digitales suelen suprimirlo; no es "
                    "cosa de esta copia)" if res.formato == "ebook" else ""
                )
            )
        if res.completable_con_ocr:
            res.dificultades.append(
                f"{len(res.paginas_rescatables)} páginas del PDF no llevan "
                f"texto ({100 - res.cobertura:.0f} %): se pueden reconocer "
                "aparte"
            )
        elif res.paginas_rescatables:
            res.dificultades.append(
                f"{len(res.paginas_rescatables)} páginas del PDF no llevan "
                f"texto ({100 - res.cobertura:.0f} %): faltan demasiadas, "
                "mejor buscar otra copia"
            )
        # La cubierta y las láminas del final no son texto perdido: se
        # dicen, pero sin proponer nada (Aristófanes, Ilíada).
        elif res.paginas_sin_texto:
            res.dificultades.append(
                f"{len(res.paginas_sin_texto)} hojas son solo imagen "
                "(cubierta o láminas): no hay texto que perder"
            )
        if res.palabras < 20_000:
            res.dificultades.append(
                f"muy poco texto para un tomo ({res.palabras:,} palabras): "
                "puede estar incompleto".replace(",", ".")
            )
        # ¿Es el tomo que el usuario dijo?
        if (
            tomo_esperado is not None
            and res.tomo_detectado is not None
            and res.tomo_detectado.orden != tomo_esperado.orden
        ):
            res.dificultades.append(
                f"por el nombre, este PDF parece el tomo "
                f"{res.tomo_detectado.orden} "
                f"({res.tomo_detectado.canonical_title()[:50]}), no el que "
                "has elegido"
            )
        return res
    finally:
        lector.cerrar()


# ----------------------------------------------------------------------
# Reconocimiento óptico SOLO de las páginas que faltan
# ----------------------------------------------------------------------
def ocr_disponible(ruta_manual: str = "") -> tuple[bool, str]:
    """
    ¿Se puede reconocer texto? Devuelve `(disponible, mensaje)`.

    Tesseract es un programa aparte (no una librería de Python) y no se
    empaqueta con la aplicación: son unos 50 MB que solo hacen falta
    para rescatar páginas sueltas. Se busca donde suele instalarse y,
    si el usuario lo tiene en otro sitio, en la ruta que indique en
    Configuración.
    """
    import os
    import shutil

    candidatos = [ruta_manual] if ruta_manual else []
    candidatos.append(shutil.which("tesseract") or "")
    candidatos.extend(_RUTAS_TESSERACT)
    exe = next((c for c in candidatos if c and Path(c).exists()), "")
    if not exe:
        return False, (
            "Para reconocer las páginas que faltan hace falta Tesseract, "
            "un programa gratuito aparte.\n\n"
            "1. Descárgalo de github.com/UB-Mannheim/tesseract/wiki\n"
            "2. Durante la instalación marca el idioma «Spanish»\n"
            "3. Vuelve a analizar este PDF\n\n"
            "Si ya lo tienes instalado en otra carpeta, indica la ruta "
            "en Configuración → «Ruta de Tesseract»."
        )
    # PyMuPDF necesita saber dónde está tessdata (los idiomas)
    tessdata = Path(exe).parent / "tessdata"
    if tessdata.exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
    if not os.environ.get("TESSDATA_PREFIX"):
        return False, (
            "Tesseract está instalado pero no se encuentran sus idiomas "
            "(carpeta «tessdata»). Reinstálalo marcando el idioma "
            "«Spanish»."
        )
    return True, exe


def idiomas_ocr() -> set[str]:
    """Idiomas instalados en la carpeta «tessdata» del equipo."""
    import os

    carpeta = Path(os.environ.get("TESSDATA_PREFIX", ""))
    if not carpeta.is_dir():
        return set()
    return {p.stem for p in carpeta.glob("*.traineddata")}


def completar_con_ocr(
    ruta: Path,
    res: Analisis,
    idioma: str = "spa",
    ruta_tesseract: str = "",
    progreso=None,
    cancelado=None,
) -> Analisis:
    """
    Reconoce SOLO las páginas sin texto y las incorpora al análisis.

    Es lo que rescata un tomo casi completo: en el Plutarco, dos de las
    diecisiete páginas ilegibles eran justo las de su índice de
    nombres. Reconocer el volumen entero sería tirar horas de proceso
    para reconstruir lo que ya estaba bien.

    `progreso(hechas, total, pagina)` y `cancelado()` permiten
    enseñar el avance y parar desde la interfaz.
    """
    import fitz

    # Solo las hojas que valen la pena: la cubierta y las láminas no
    # traen texto que rescatar.
    pendientes = list(res.paginas_rescatables)
    if not pendientes:
        return res                      # nada que reconocer: ni se mira
    ok, detalle = ocr_disponible(ruta_tesseract)
    if not ok:
        raise RuntimeError(detalle)

    # El idioma tiene que estar INSTALADO. Tesseract se instala por
    # defecto solo con inglés: pedirle "spa" sin haber marcado
    # «Spanish» falla en todas las páginas y el usuario solo veía que
    # "no funciona" (2026-07-28).
    instalados = idiomas_ocr()
    nota_idioma = ""
    if instalados and idioma not in instalados:
        if "eng" in instalados:
            nota_idioma = (
                f"el idioma «{idioma}» no está instalado en Tesseract: las "
                "páginas se han reconocido en inglés y las tildes y la ñ "
                "pueden salir mal. Para arreglarlo, vuelve a ejecutar el "
                "instalador de Tesseract y marca «Spanish» en los idiomas "
                "adicionales"
            )
            idioma = "eng"
        else:
            raise RuntimeError(
                "Tesseract está instalado pero sin ningún idioma "
                "utilizable.\n\nVuelve a ejecutar su instalador y marca "
                "el idioma «Spanish» en la lista de idiomas adicionales."
            )

    res.ocr_intentado = True          # aunque no rescate nada: no repetir
    res.ocr_fallo = ""
    por_numero = {r["pdf"]: r for r in res.registros}
    doc = fitz.open(ruta)
    reconocidas: list[int] = []
    vacias: list[int] = []
    errores: list[str] = []
    try:
        for hechas, numero in enumerate(pendientes, 1):
            if cancelado is not None and cancelado():
                break
            if progreso is not None:
                progreso(hechas, len(pendientes), numero)
            pagina = doc[numero - 1]
            try:
                tp = pagina.get_textpage_ocr(
                    language=idioma, dpi=300, full=True
                )
                texto = pagina.get_text(textpage=tp).strip()
            except Exception as exc:  # noqa: BLE001 - una página rara no para todo
                logger.warning("OCR falló en la página %d: %s", numero, exc)
                fallo = str(exc)
                if "language" in fallo.lower() or "tessdata" in fallo.lower():
                    # No es cosa de esta página: sin idioma fallarían
                    # todas. Mejor parar y decir cómo se arregla.
                    raise RuntimeError(
                        "Tesseract no ha podido cargar sus idiomas.\n\n"
                        "Vuelve a ejecutar su instalador y marca «Spanish» "
                        "en la lista de idiomas adicionales, o indica la "
                        "ruta correcta en Configuración.\n\n"
                        f"({fallo})"
                    ) from exc
                errores.append(fallo)
                continue
            if len(texto) < 40:
                vacias.append(numero)
                continue
            reg = por_numero.get(numero)
            if reg is None:
                continue
            lineas = [l.strip() for l in texto.splitlines() if l.strip()]
            reg["cuerpo"] = _junta_guiones(lineas)
            reg["ocr"] = True
            reconocidas.append(numero)
    finally:
        doc.close()

    res.paginas_ocr = reconocidas
    res.paginas_sin_texto = [
        p for p in res.paginas_sin_texto if p not in set(reconocidas)
    ]
    res.palabras = sum(len(r["cuerpo"].split()) for r in res.registros)
    res.nombres = indice_de_nombres(res.registros)
    res.dificultades = [
        d for d in res.dificultades
        if "sin reconocer" not in d and "índice de nombres" not in d
    ]
    if nota_idioma:
        res.dificultades.append(nota_idioma)
    # Por qué no salió nada: el usuario merece saberlo, no un "no
    # funciona" mudo. Las páginas mudas suelen ser láminas, hojas en
    # blanco o los cortes entre partes del tomo.
    if not reconocidas:
        if errores:
            res.ocr_fallo = (
                "Tesseract no pudo leer ninguna de las páginas:\n\n"
                f"{errores[0]}"
            )
        elif vacias:
            res.ocr_fallo = (
                f"Las {len(vacias)} páginas se reconocieron, pero no "
                "contienen texto: son láminas, hojas en blanco o "
                "separadores. No se pierde nada del tomo."
            )
        else:
            res.ocr_fallo = "El reconocimiento se detuvo antes de empezar."
    elif errores:
        res.ocr_fallo = (
            f"{len(reconocidas)} páginas reconocidas; {len(errores)} "
            f"fallaron: {errores[0]}"
        )
    if res.paginas_sin_texto:
        res.dificultades.append(
            f"quedan {len(res.paginas_sin_texto)} páginas sin texto aun "
            "después del reconocimiento"
            + (" (en blanco o solo imagen)" if vacias and not errores else "")
        )
    if not res.nombres:
        res.dificultades.append(
            "no se ha encontrado índice de nombres: se podrá buscar en el "
            "texto, pero sin la concordancia del traductor"
        )
    logger.info(
        "OCR parcial: %d páginas reconocidas, %d nombres en el índice",
        len(reconocidas), len(res.nombres),
    )
    return res


def ruta_salida(tomo) -> Path:
    """Dónde vive el texto de un tomo."""
    nombre = (
        f"{tomo.orden or 0:03d} - {tomo.autor[:28]} - {tomo.obras[:40]}.jsonl"
    )
    for malo in '\\/:*?"<>|':
        nombre = nombre.replace(malo, "-")
    return TEXTOS_DIR / nombre


def guardar(res: Analisis, tomo) -> Path:
    """Escribe el texto extraído (el PDF NO se copia a ninguna parte)."""
    TEXTOS_DIR.mkdir(parents=True, exist_ok=True)
    destino = ruta_salida(tomo)
    cabecera = {
        "tipo": "tomo",
        "orden": tomo.orden,
        "numero": tomo.numero,
        "autor": tomo.autor,
        "obras": tomo.obras,
        "canonico": tomo.canonical_title(),
        "archivo_pdf": res.archivo,
        "sha1": res.sha1,
        "paginas_pdf": res.paginas_pdf,
        # Hojas que llevan texto: en una edición digital las notas van a
        # hoja por nota y "1.436 páginas" no dice nada del tomo.
        "hojas_texto": res.hojas_de_texto,
        "paginas_impresas": res.paginas_impresas,
        # OJO: "obras" ya es el título del tomo en el Excel. Las del
        # volumen (las cuatro comedias) van con nombre propio.
        "obras_del_volumen": res.obras,
        "estado": res.estado,
        "formato": res.formato,
        "legibilidad": round(res.legibilidad, 1),
        "palabras": res.palabras,
        "desfase_folio": res.desfase_folio,
        "cuerpo_pt": res.cuerpo_pt,
        "dificultades": res.dificultades,
        "secciones": res.secciones,
        "indice_nombres": res.nombres,
    }
    with destino.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(cabecera, ensure_ascii=False) + "\n")
        for reg in res.registros:
            fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
    logger.info(
        "Texto guardado: %s (%d palabras, %d nombres)",
        destino.name, res.palabras, len(res.nombres),
    )
    return destino


def clave_de_tomo(tomo) -> str:
    """
    Identificador ÚNICO de un tomo para el seguimiento de textos.

    No vale el número de orden: TRES pares de la colección lo comparten
    (200 Aristóteles/Museo, 250 Plinio/Basilio, 415 Estrabón/Ovidio) y
    el ✔ de un tomo aparecía también en el otro, con sus páginas y sus
    palabras (2026-07-29). El título canónico sí es único.
    """
    return tomo.canonical_title()


def estado_del_tomo(hecho: dict, tomo) -> Optional[dict]:
    """La cabecera guardada de ESE tomo, o None si aún no tiene texto."""
    cabecera = hecho.get(clave_de_tomo(tomo))
    if cabecera is not None:
        return cabecera
    # Textos guardados antes de que la cabecera llevara el canónico
    return hecho.get(f"{tomo.orden}|{collection.normalize(tomo.autor)}")


def _autor_del_pdf(nombre: str) -> str:
    """Autor al que apunta el NOMBRE del PDF analizado, normalizado."""
    if not nombre:
        return ""
    from app.collection import load_excel

    global _TOMOS_CACHE
    if _TOMOS_CACHE is None:
        try:
            _TOMOS_CACHE = load_excel()
        except Exception:  # noqa: BLE001 - sin Excel no se puede comparar
            _TOMOS_CACHE = []
    tomo = collection.match_tomo(
        _TOMOS_CACHE, nombre_para_buscar(Path(nombre).stem)
    )
    return collection.normalize(tomo.autor) if tomo is not None else ""


_TOMOS_CACHE: Optional[list] = None


def revisar_textos(reparar: bool = False) -> list[dict]:
    """
    Repasa los textos ya guardados y arregla lo que se pueda SIN el PDF.

    Hace falta porque el análisis ha ido mejorando y los textos viejos
    se quedaron con los defectos de su día. Dos arreglos:

    - **Cabecera**: se completan los datos que entonces no existían
      (hojas de texto, formato), calculándolos de lo ya guardado.
    - **Notas desbordadas**: cuando el umbral de "letra menor" era único
      para todo el tomo, en los escaneos se llevaba a las notas hasta el
      87 % del texto (Moralia X). Si en un tomo las notas pesan más del
      40 % y en una hoja pesan más del doble que el cuerpo, esa hoja se
      recompone: el texto vuelve al cuerpo, que es donde estaba.

    Lo que NO se puede arreglar aquí se dice, para volver a analizar el
    PDF si aún se tiene.
    """
    informes: list[dict] = []
    if not TEXTOS_DIR.exists():
        return informes
    for archivo in sorted(TEXTOS_DIR.glob("*.jsonl")):
        try:
            lineas = archivo.read_text(encoding="utf-8").splitlines()
            cabecera = json.loads(lineas[0])
        except (OSError, ValueError, IndexError):
            informes.append({"archivo": archivo.name, "avisos": ["ilegible"],
                             "reparado": []})
            continue
        registros = [json.loads(l) for l in lineas[1:] if l.strip()]
        avisos: list[str] = []
        reparado: list[str] = []

        # Las secciones se vuelven a clasificar con las reglas de hoy:
        # los bloques "Notas cartas y fragmentos" del Juliano quedaron
        # como texto normal y el tomo decía tener 867 hojas de texto
        # cuando 666 son notas (2026-07-29).
        recolocadas = 0
        for seccion in cabecera.get("secciones") or []:
            tipo = _tipo_seccion(seccion.get("titulo", ""))
            if tipo != seccion.get("tipo"):
                seccion["tipo"] = tipo
                recolocadas += 1
        if recolocadas:
            reparado.append(f"{recolocadas} secciones reclasificadas")

        hojas = hojas_de_texto(
            cabecera.get("secciones") or [], cabecera.get("paginas_pdf") or 0
        )
        if cabecera.get("hojas_texto") != hojas:
            cabecera["hojas_texto"] = hojas
            reparado.append("hojas de texto")
        if not cabecera.get("formato"):
            cabecera["formato"] = (
                "impreso" if cabecera.get("desfase_folio") is not None
                else "ebook"
            )
            reparado.append("formato")

        # Referencias del margen, llamadas de nota y número de cada nota
        # final: se sacan del texto corrido con las reglas de hoy.
        recolocado = 0
        # Cada hoja lleva su sección apuntada de cuando se analizó: si
        # las secciones se han reclasificado, hay que llevárselo a las
        # hojas o las notas finales no se reconocen como tales.
        secciones = cabecera.get("secciones") or []
        for r in registros:
            for s in secciones:
                if s["desde"] <= r.get("pdf", 0) <= s["hasta"]:
                    if r.get("seccion") != s["tipo"]:
                        r["seccion"], r["titulo"] = s["tipo"], s["titulo"]
                    break
        # Los dos alfabetos revueltos, y basura invisible. Va ANTES de
        # volver a trocear la hoja: si no, las referencias y las
        # llamadas se buscarían sobre el texto sucio.
        #
        # Primero hay que saber si el tomo es MATEMÁTICO, porque solo
        # allí "AB" y "EK" son los nombres de dos puntos. Se cuenta
        # sobre el tomo entero, no hoja a hoja: las etiquetas escritas
        # en griego de verdad pueden estar concentradas en unas pocas
        # páginas y las latinas repartidas por todas.
        de_figuras = sum(
            cuenta_etiquetas_de_figura(r.get("cuerpo") or "")
            for r in registros
        ) >= _TOMO_DE_FIGURAS
        # El título de la obra también venía roto ("INTRODUCCIΣN"), y es
        # lo que se ve en el buscador.
        arregladas = 0
        for r in registros:
            for campo in ("cuerpo", "notas", "obra", "titulo"):
                antes_campo = r.get(campo) or ""
                if not antes_campo:
                    continue
                despues = limpia_invisibles(
                    repara_alfabetos(antes_campo, etiquetas=de_figuras))
                if despues != antes_campo:
                    r[campo] = despues
                    arregladas += 1
        if arregladas:
            reparado.append(f"{arregladas} bloques con letras recuperadas")
            if de_figuras:
                reparado.append("etiquetas de figura devueltas al griego")

        for r in registros:
            lineas = [l for l in r.get("cuerpo", "").split("\n") if l.strip()]
            antes = (r.get("cuerpo", ""), r.get("versos"), r.get("llamadas"))
            nuevo = _texto_de_hoja(
                lineas, [l for l in r.get("notas", "").split("\n") if l.strip()],
                [v for v in (r.get("versos") or [])], r.get("seccion", "texto"),
            )
            if (nuevo["cuerpo"], nuevo["versos"], nuevo["llamadas"]) != antes:
                recolocado += 1
            r.update(nuevo)
        if recolocado:
            reparado.append(f"{recolocado} hojas con las referencias aparte")

        cuerpo = sum(len(r.get("cuerpo", "")) for r in registros)
        notas = sum(len(r.get("notas", "")) for r in registros)
        total = cuerpo + notas
        if total and notas / total > 0.4:
            movidas = 0
            for r in registros:
                if len(r.get("notas", "")) > 2 * len(r.get("cuerpo", "")):
                    junto = (r.get("cuerpo", "") + "\n" + r["notas"]).strip()
                    r["cuerpo"], r["notas"] = junto, ""
                    movidas += 1
            if movidas:
                reparado.append(f"{movidas} hojas con las notas desbordadas")
        palabras = sum(len(r.get("cuerpo", "").split()) for r in registros)
        if palabras != cabecera.get("palabras"):
            cabecera["palabras"] = palabras
            reparado.append("recuento de palabras")

        # El índice de nombres se vuelve a leer del TEXTO YA GUARDADO:
        # el analizador ha aprendido formatos nuevos y los tomos viejos
        # se quedaron con lo que se supo sacar entonces (el Juliano pasó
        # de 305 nombres a 450). Solo se cambia si salen MÁS.
        indice = cabecera.get("indice_nombres") or {}
        nuevos = indice_de_nombres(registros)
        if len(nuevos) > len(indice):
            cabecera["indice_nombres"] = nuevos
            reparado.append(f"índice de nombres: {len(indice)} → {len(nuevos)}")

        # ¿El texto está guardado bajo el tomo que NO es? Pasó mientras
        # la ficha se abría por número de orden, que tres pares comparten:
        # la Metamorfosis de Ovidio quedó guardada como la Geografía de
        # Estrabón (2026-07-29).
        autor_pdf = _autor_del_pdf(cabecera.get("archivo_pdf", ""))
        autor_guardado = collection.normalize(cabecera.get("autor", ""))
        # "Ovidio" y "Ovidio Nasón" son el mismo autor: solo cuenta que
        # ninguno de los dos aparezca dentro del otro.
        distinto = bool(
            autor_pdf and autor_guardado
            and autor_pdf not in autor_guardado
            and autor_guardado not in autor_pdf
        )
        if distinto:
            avisos.append(
                f"el PDF era de {cabecera.get('archivo_pdf', '')[:40]}: "
                "parece guardado en el tomo equivocado"
            )
        if len(cabecera.get("secciones") or []) <= 1:
            avisos.append("sin secciones: el PDF no traía índice interno")
        if not cabecera.get("indice_nombres"):
            avisos.append("sin índice de nombres")
        if hojas and palabras / hojas < 180:
            avisos.append(
                f"poco texto por hoja ({palabras / hojas:.0f} palabras): "
                "conviene volver a analizar el PDF"
            )
        if reparado and reparar:
            with archivo.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(cabecera, ensure_ascii=False) + "\n")
                for r in registros:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        informes.append({
            "archivo": archivo.name,
            "canonico": cabecera.get("canonico", ""),
            "avisos": avisos,
            "reparado": reparado,
        })
    return informes


# Resumen de los textos extraídos, para que la lista de seguimiento no
# tenga que abrir los .jsonl: son 108 MB de datos para el análisis, y
# abrirlos de uno en uno dejaba la ventana colgada varios segundos
# (Windows inspecciona cada archivo al abrirlo, 2026-07-31).
INDICE = "_indice.json"
# Lo ÚNICO que la lista necesita saber de cada texto
_CAMPOS_INDICE = (
    "orden", "numero", "autor", "obras", "canonico", "archivo_pdf",
    "paginas_pdf", "hojas_texto", "paginas_impresas", "palabras",
    "estado", "formato", "legibilidad", "desfase_folio", "dificultades",
)


def _ficha_de(archivo: Path, stat) -> dict:
    """Resumen de un texto extraído, leyendo SOLO su cabecera."""
    with archivo.open(encoding="utf-8") as fh:
        cabecera = json.loads(fh.readline() or "{}")
    ficha = {c: cabecera.get(c) for c in _CAMPOS_INDICE}
    ficha["nombres"] = len(cabecera.get("indice_nombres") or {})
    if not ficha.get("hojas_texto"):
        ficha["hojas_texto"] = hojas_de_texto(
            cabecera.get("secciones") or [], int(cabecera.get("paginas_pdf") or 0)
        )
    ficha["secciones"] = len(cabecera.get("secciones") or [])
    ficha["archivo"] = archivo.name
    ficha["mtime"] = int(stat.st_mtime)
    ficha["bytes"] = stat.st_size
    return ficha


def _leer_indice() -> dict:
    try:
        return json.loads((TEXTOS_DIR / INDICE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def estado_de_los_tomos() -> dict[str, dict]:
    """
    Qué tomos tienen ya su texto extraído: `{clave: resumen}`.

    Lo usa la lista de seguimiento para pintar el ✔ y el recuento; la
    clave la da `clave_de_tomo`, nunca el número de orden.

    NO abre los textos: se apoya en `_indice.json`, que guarda de cada
    uno lo poco que la lista enseña. Solo se relee la cabecera de los
    archivos nuevos o que hayan cambiado (fecha o tamaño).
    """
    hecho: dict[str, dict] = {}
    if not TEXTOS_DIR.exists():
        return hecho
    indice = _leer_indice()
    nuevo: dict[str, dict] = {}
    cambios = False
    for archivo in sorted(TEXTOS_DIR.glob("*.jsonl")):
        stat = archivo.stat()
        ficha = indice.get(archivo.name)
        if (
            not ficha
            or ficha.get("mtime") != int(stat.st_mtime)
            or ficha.get("bytes") != stat.st_size
        ):
            try:
                ficha = _ficha_de(archivo, stat)
            except (OSError, ValueError):
                continue
            cambios = True
        nuevo[archivo.name] = ficha
        clave = ficha.get("canonico") or (
            f"{ficha.get('orden')}|"
            f"{collection.normalize(ficha.get('autor') or '')}"
        )
        hecho[clave] = ficha
    if cambios or len(nuevo) != len(indice):
        try:
            (TEXTOS_DIR / INDICE).write_text(
                json.dumps(nuevo, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass          # sin índice se relee: lento, pero funciona
    return hecho


def _estado_leyendo_cabeceras() -> dict[str, dict]:
    """Versión que abre los textos (respaldo y pruebas)."""
    hecho: dict[str, dict] = {}
    if not TEXTOS_DIR.exists():
        return hecho
    for archivo in TEXTOS_DIR.glob("*.jsonl"):
        try:
            with archivo.open(encoding="utf-8") as fh:
                cabecera = json.loads(fh.readline() or "{}")
        except (OSError, ValueError):
            continue
        orden = cabecera.get("orden")
        if orden is None:
            continue
        cabecera["archivo"] = archivo.name
        # Textos guardados antes de distinguir hoja de página: se
        # deduce de sus secciones, sin releer el archivo entero.
        if not cabecera.get("hojas_texto"):
            cabecera["hojas_texto"] = hojas_de_texto(
                cabecera.get("secciones") or [],
                int(cabecera.get("paginas_pdf") or 0),
            )
        clave = cabecera.get("canonico") or (
            f"{orden}|{collection.normalize(cabecera.get('autor', ''))}"
        )
        hecho[clave] = cabecera
    return hecho
