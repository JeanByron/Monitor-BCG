"""
formato.py
==========
Compone una página de un tomo tal como está en la Biblioteca Clásica
Gredos, a partir del texto guardado en los `.jsonl`.

Por qué hace falta
------------------
Los `.jsonl` guardan el texto FIEL al PDF, renglón a renglón, y eso es
deliberado: en los poetas el renglón ES el verso y es la unidad de cita
(ver `pdftext`). Pero volcarlos tal cual se lee fatal, porque el PDF
trae tres cosas que en el libro impreso no están donde parecen:

1. Los renglones de la prosa vienen CORTADOS a lo ancho de la caja.
2. Entre medias aparecen sueltos los marcadores del margen —el número
   de párrafo, la letra de Estéfano («403d»), el número de verso—, que
   en el libro van al margen y no en mitad de la frase.
3. Las llamadas de nota van PEGADAS a la palabra («las islas42;»,
   «Dánao40»), porque el superíndice pierde su condición al extraerse.

Este módulo NO toca los `.jsonl`: son la fuente y se leen tal cual. Lo
que hace es devolver la página ya compuesta, y quien la enseñe decide
cómo pintarla.

Cómo está compuesto un tomo de la BCG
-------------------------------------
La colección la fundó Gredos en 1977 (dirigida por Julio Calonge, hoy
por Carlos García Gual y José Javier Iso / José Luis Moralejo). Cada
volumen lleva introducción del especialista, la traducción con las
referencias canónicas al margen —Estéfano en Platón, Bekker en
Aristóteles, libro y parágrafo en los historiadores, verso en los
poetas—, notas a pie de página e índice de nombres al final. De ahí
salen las tres reglas de arriba: lo que va al margen, al margen; lo que
es nota, al pie; y el cuerpo, corrido y justificado.

Prosa o verso
-------------
La decisión NO se puede tomar por el largo del renglón: la Ilíada mide
57 caracteres de verso y hay prosa de 55 (medido sobre el corpus). Lo
que sí separa es la RACHA de renglones que llenan la caja: la prosa
justificada encadena renglones llenos, el verso casi nunca. Medido:

    prosa (Isócrates, Discursos)  86 % llenos · racha 7
    verso (Séneca, Tragedias)     26 % llenos · racha 1
    tabla (Plutarco, cronología)  30 % llenos · racha 2
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# --- lo que va al margen ----------------------------------------------
# Un renglón que SOLO lleva una referencia: el número del parágrafo o
# del verso, la letra de Estéfano («b», «c»), la página con letra
# («403d», «1094a»).
MARCA_DE_MARGEN = re.compile(r"^\s*(\d{1,4}\s?[a-eA-E]?|[a-eA-E])\s*$")

# --- final de oración --------------------------------------------------
FIN_DE_FRASE = (".", "?", "!", "»", ":", "…")

# --- llamada de nota pegada a la palabra -------------------------------
# «las islas42;» o «Dánao40». Se exige que delante haya una letra y
# detrás un signo o el final: así no se toca «Libro 42» ni «1094a».
LLAMADA_PEGADA = re.compile(
    r"(?<=[a-záéíóúüñ])(\d{1,3})(?=[\s,;.:)\]»]|$)", re.IGNORECASE
)

# --- limpieza ----------------------------------------------------------
ESPACIO_ANTES_DE_SIGNO = re.compile(r"\s+([,;:.!?»)\]])")
_INVISIBLES = str.maketrans({"\xa0": " ", "⁠": "", "​": "",
                             "﻿": ""})

# Ancho mínimo creíble de una caja de texto. Sin este suelo, en una
# página de verso el propio verso más largo hace de «caja» y el poema
# se une en un párrafo.
CAJA_MINIMA = 55
# Cuándo se considera que un renglón LLENA la caja.
LLENO = 0.85
# A partir de cuántos renglones llenos SEGUIDOS la página es prosa.
RACHA_DE_PROSA = 4
# …o de qué proporción de renglones llenos.
PROPORCION_DE_PROSA = 0.60


@dataclass
class Pagina:
    """Una página del tomo, ya compuesta."""

    bloques: list[tuple[str, str]] = field(default_factory=list)
    marcas: list[str] = field(default_factory=list)
    notas: list[tuple[str, str]] = field(default_factory=list)
    llamadas: list[str] = field(default_factory=list)
    es_verso: bool = False

    def texto(self) -> str:
        """La página en texto llano, lista para copiar o citar."""
        return "\n\n".join(t for _tipo, t in self.bloques)


# ----------------------------------------------------------------------
# Piezas
# ----------------------------------------------------------------------
def limpia(texto: str) -> str:
    """Espacios de más, invisibles y el espacio antes de una coma."""
    texto = (texto or "").translate(_INVISIBLES)
    return ESPACIO_ANTES_DE_SIGNO.sub(
        lambda m: m.group(1), " ".join(texto.split())
    )


def ancho_de_caja(lineas: list[str]) -> int:
    """
    A cuántos caracteres estaba cortado el renglón en el PDF.

    Se toma el percentil 90 y no el máximo: una línea suelta larguísima
    —una cita pegada, una tabla— desplazaría la medida.
    """
    largos = sorted(len(l.strip()) for l in lineas if l.strip())
    if not largos:
        return 0
    return largos[max(0, int(len(largos) * 0.9) - 1)]


def es_prosa(lineas: list[str]) -> bool:
    """
    ¿Esta página es prosa justificada (y por tanto se recompone)?

    Manda la RACHA de renglones llenos, no el largo del renglón: la
    prosa encadena renglones que llegan al margen; el verso, no. Se
    acepta también por proporción, para páginas cortas donde no da
    tiempo a formar racha.
    """
    utiles = [l.strip() for l in lineas if l.strip()]
    if len(utiles) < 4:
        return True                     # dos frases sueltas: prosa
    caja = max(ancho_de_caja(utiles), CAJA_MINIMA)
    llenas = [len(l) >= LLENO * caja for l in utiles]
    racha = mejor = 0
    for llena in llenas:
        racha = racha + 1 if llena else 0
        mejor = max(mejor, racha)
    return (
        mejor >= RACHA_DE_PROSA
        or sum(llenas) / len(llenas) >= PROPORCION_DE_PROSA
    )


def separa_llamadas(texto: str) -> tuple[str, list[str]]:
    """
    Despega del texto las llamadas de nota («las islas42;»).

    En el libro son un superíndice; al extraer el PDF se quedan pegadas
    a la palabra y ensucian la lectura y la búsqueda. Se devuelven
    aparte para poder pintarlas como lo que son.
    """
    llamadas: list[str] = []

    def saca(m: re.Match) -> str:
        llamadas.append(m.group(1))
        return ""

    return LLAMADA_PEGADA.sub(saca, texto), llamadas


def _es_titulo(linea: str, tras_numero: bool) -> bool:
    """
    ¿Este renglón encabeza una carta, un capítulo o un discurso?

    En la BCG el encabezado va tras el número de la pieza («9») y no
    cierra frase: «A Alipio, hermano de Cesareo». Sin esto se pegaba al
    primer párrafo y parecía parte de la oración.
    """
    if not tras_numero or len(linea) >= 70 or not linea[:1].isupper():
        return False
    return not linea.endswith(FIN_DE_FRASE + (",", ";"))


# Palabras con las que un renglón NO puede acabar si es un epígrafe:
# son de enlace y piden continuación, así que ese renglón es un trozo
# suelto de la frase, no un rótulo.
_NO_CIERRAN = frozenset("""
a al ante bajo con contra de del desde durante en entre hacia hasta
mediante para por según sin sobre tras y e o u ni que quien cuyo cuya
el la lo los las un una unos unas su sus mi mis tu tus este esta estos
estas ese esa aquel como cuando donde porque pues si no más muy ya
""".split())


def _es_epigrafe(linea: str, siguiente: str, caja: float,
                 cierra_lo_abierto: bool) -> bool:
    """
    ¿Es uno de los epígrafes que la BCG lleva al MARGEN?

    En el tomo de papel esos resumencitos —«Herípidas ataca a
    Farnabazo», «Entrevista de Agesilao y Farnabazo»— van en el margen,
    fuera de la caja; el conversor a libro electrónico los soltó dentro
    del texto corrido y sin reconocerlos se pegaban al párrafo anterior:
    la página entera quedaba en UN bloque de dos mil caracteres, sin una
    sola frontera donde cortar (Helénicas, hoja 85, 2026-08-09). De ahí
    que el pasaje del día se quedara colgado a media frase.

    Las cinco condiciones son las que separan un epígrafe de un renglón
    partido, que es lo único con lo que se puede confundir:
      · claramente corto — no llega ni a las tres cuartas partes de la
        caja, mientras que un renglón partido viene lleno;
      · empieza en mayúscula;
      · no cierra frase ni acaba en coma o punto y coma;
      · no acaba en palabra de enlace («…Pero Agesilao, que»), que es
        justo la forma del renglón partido;
      · el renglón siguiente empieza en mayúscula, es decir, abre
        oración en vez de continuar la del epígrafe.
    Y, por encima de todas, lo que va abierto tiene que cerrar frase:
    un epígrafe jamás cae en mitad de una oración.
    """
    if not cierra_lo_abierto or len(linea) >= caja * 0.72:
        return False
    if not linea[:1].isupper() or not siguiente[:1].isupper():
        return False
    if linea.endswith(FIN_DE_FRASE + (",", ";")):
        return False
    return linea.split()[-1].lower().strip(".,;:)»") not in _NO_CIERRAN


def _mayusculas(linea: str) -> bool:
    """Rótulo en versales, como los de sección de la colección."""
    letras = [c for c in linea if c.isalpha()]
    if len(letras) < 3 or len(linea) > 70:
        return False
    return sum(c.isupper() for c in letras) / len(letras) > 0.85


# ----------------------------------------------------------------------
# Composición
# ----------------------------------------------------------------------
def componer(cuerpo: str, notas: str = "") -> Pagina:
    """
    Devuelve la página compuesta: bloques, marcas del margen y notas.

    Los bloques son `(tipo, texto)` con tipo `parrafo`, `titulo`,
    `rotulo` o `verso`.
    """
    lineas = (cuerpo or "").translate(_INVISIBLES).splitlines()
    pagina = Pagina()
    utiles = [l for l in lineas if l.strip() and not MARCA_DE_MARGEN.match(l)]
    pagina.es_verso = not es_prosa(utiles)
    caja = max(ancho_de_caja(utiles), CAJA_MINIMA)
    lleno = caja * LLENO

    actual: list[str] = []

    def cerrar() -> None:
        if not actual:
            return
        junto, llamadas = separa_llamadas(" ".join(actual))
        pagina.llamadas.extend(llamadas)
        pagina.bloques.append(("parrafo", limpia(junto)))
        actual.clear()

    # El renglón siguiente hace falta para reconocer los epígrafes del
    # margen; se guarda ya pelado para no repetir el strip.
    peladas = [l.strip() for l in lineas]

    tras_numero = False
    for i, linea in enumerate(lineas):
        pelada = peladas[i]
        if MARCA_DE_MARGEN.match(linea):
            cerrar()
            if pelada and pelada not in pagina.marcas:
                pagina.marcas.append(pelada)
            # Solo un NÚMERO suelto abre pieza; las letras del margen
            # (b, c, 403d) son paginación y detrás va texto corrido.
            tras_numero = pelada.isdigit()
            continue
        if not pelada:
            cerrar()
            continue
        siguiente = next((p for p in peladas[i + 1:] if p), "")
        # Lo abierto cierra frase si no hay nada abierto o si el último
        # renglón acababa la oración. Se mira AQUÍ y no por el ancho de
        # la caja porque el renglón que cierra puede venir lleno: en
        # Helénicas medía 70 sobre una caja de 82 y el umbral de «lleno»
        # está en 69,7 — lo pasaba por tres décimas de carácter y se
        # tragaba el epígrafe siguiente.
        cierra = not actual or actual[-1].endswith(FIN_DE_FRASE)
        if (_es_titulo(pelada, tras_numero) or _mayusculas(pelada)
                or _es_epigrafe(pelada, siguiente, caja, cierra)):
            cerrar()
            limpio, llamadas = separa_llamadas(pelada)
            pagina.llamadas.extend(llamadas)
            pagina.bloques.append(("titulo", limpia(limpio)))
            tras_numero = False
            continue
        tras_numero = False

        if pagina.es_verso:
            limpio, llamadas = separa_llamadas(pelada)
            pagina.llamadas.extend(llamadas)
            pagina.bloques.append(("verso", limpia(limpio)))
            continue

        actual.append(pelada)
        # El párrafo SOLO se corta donde acaba una oración y el renglón
        # no llega al margen. Un renglón corto que NO cierra frase es un
        # trozo suelto —«…las islas42; Pélope» / «hijo de Tántalo»— y se
        # une con el siguiente: son los saltos de línea sin sentido.
        if pelada.endswith(FIN_DE_FRASE) and len(pelada) < lleno:
            cerrar()
    cerrar()

    pagina.notas = _partir_notas(notas)
    return pagina


# --- notas -------------------------------------------------------------
_CABEZA_DE_NOTA = re.compile(r"^\s*\[?(\d{1,3})\]?[.)]?\s+(\S.*)$")
# Restos de la paginación del editor que el analizador dejó caer en las
# notas: «b c d e 94a», «20C». No son notas de nada.
_RUIDO_DE_MARGEN = re.compile(r"^[\d\s]*(?:[a-eA-E]\s*|\d+\s*[a-eA-E]?\s*)+$")


def es_ruido_de_margen(texto: str) -> bool:
    limpio = (texto or "").strip()
    return bool(limpio) and len(limpio) <= 24 and \
        bool(_RUIDO_DE_MARGEN.match(limpio))


def _partir_notas(texto: str) -> list[tuple[str, str]]:
    """
    Las notas al pie, como `(número, texto)`.

    Vienen corridas y cada una empieza en su renglón, encabezada por su
    número; sin separarlas no hay manera de ponerlas al pie.
    """
    if not (texto or "").strip():
        return []
    notas: list[tuple[str, str]] = []
    numero, trozo = "", []
    for linea in (texto or "").translate(_INVISIBLES).splitlines():
        m = _CABEZA_DE_NOTA.match(linea)
        if m:
            if trozo:
                notas.append((numero, limpia(" ".join(trozo))))
            numero, trozo = m.group(1), [m.group(2)]
        else:
            trozo.append(linea.strip())
    if trozo:
        notas.append((numero, limpia(" ".join(trozo))))
    return [(n, t) for n, t in notas if t and not es_ruido_de_margen(t)]


# ----------------------------------------------------------------------
# Recortes por frase (para no dejar al lector a medias en los bordes)
# ----------------------------------------------------------------------
MAXIMO_RECORTE = 0.22
MINIMO_QUE_QUEDA = 0.55
# Tope en CARACTERES: el porcentaje castiga a los textos cortos —perder
# 14 letras de 46 es un 30 %, pero son 14 letras—.
RECORTE_ABSOLUTO = 140


def empieza_en_frase(bloques: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Quita el rabo de frase con el que arranca la página.

    Dentro de un PÁRRAFO se corta hasta la mayúscula que sigue a un
    punto; en VERSO no se parte el renglón —es la unidad de cita— y se
    descartan versos enteros. Solo si lo que se pierde es poco.
    """
    entero = sum(len(x) for _t, x in bloques) or 1
    perdido = 0
    for i, (tipo, texto) in enumerate(bloques):
        if tipo in ("titulo", "rotulo") or texto[:1].isupper():
            return bloques[i:]
        if perdido > RECORTE_ABSOLUTO and perdido / entero > MAXIMO_RECORTE:
            return bloques
        if tipo == "parrafo":
            for corte in range(len(texto) - 2):
                if texto[corte] in FIN_DE_FRASE and texto[corte + 1] == " " \
                        and texto[corte + 2].isupper():
                    va = perdido + corte
                    if va > RECORTE_ABSOLUTO and va / entero > MAXIMO_RECORTE:
                        return bloques[i:]
                    return [("parrafo", texto[corte + 2:])] + bloques[i + 1:]
        perdido += len(texto)
    return bloques


def desde_el_pasaje(
    bloques: list[tuple[str, str]], pasaje: str, minimo: int = 24,
) -> list[tuple[str, str]]:
    """
    Empieza la página donde empieza el pasaje que se quiere enseñar.

    Una hoja da dos o tres pasajes, y el del día puede ser el segundo:
    enseñando la hoja desde arriba, el resumen citaba algo que estaba
    más abajo y no cuadraba con lo primero que se leía (2026-08-08). Se
    localiza el arranque del pasaje entre los bloques y se tira lo
    anterior. Si no se encuentra —el troceado y la composición no
    parten igual—, se deja la página entera: mejor eso que perderla.
    """
    if not bloques or not pasaje:
        return bloques
    cabeza = normaliza(" ".join(pasaje.split())[:minimo * 3])[:minimo]
    if len(cabeza) < 8:
        return bloques
    for i, (_tipo, texto) in enumerate(bloques):
        if cabeza in normaliza(texto):
            return bloques[i:]
    # A veces el arranque del pasaje cae dentro de un bloque que empieza
    # antes; entonces se corta por dentro, que sigue siendo su sitio.
    for i, (tipo, texto) in enumerate(bloques):
        donde = normaliza(texto).find(cabeza[:12])
        if donde > 0:
            return [(tipo, texto[donde:])] + bloques[i + 1:]
    return bloques


def acaba_en_frase(bloques: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Cierra la página en punto, cortando la frase a medias del final.

    Aquí no hay tope de recorte —es donde se acaba de leer—, solo el
    suelo de `MINIMO_QUE_QUEDA` para no vaciar la página.
    """
    entero = sum(len(x) for _t, x in bloques)
    if not entero:
        return bloques
    for i in range(len(bloques) - 1, -1, -1):
        tipo, texto = bloques[i]
        limpio = texto.rstrip()
        if limpio.endswith(FIN_DE_FRASE):
            corte = len(texto)
        elif tipo == "parrafo":
            corte = max(
                (n + 1 for n, c in enumerate(texto) if c in FIN_DE_FRASE),
                default=0,
            )
        else:
            corte = 0                   # un verso no se parte por la mitad
        if not corte:
            continue
        queda = sum(len(x) for _t, x in bloques[:i]) + corte
        if entero - queda > RECORTE_ABSOLUTO and \
                queda / entero < MINIMO_QUE_QUEDA:
            return bloques
        if corte == len(texto):
            return bloques[:i + 1]
        return bloques[:i] + [(tipo, texto[:corte])]
    return bloques


def acaba_en_parrafo(bloques: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Cierra la página en PUNTO Y APARTE, sin partir ningún párrafo.

    Más exigente que `acaba_en_frase`: se usa donde solo se enseña UNA
    página —el pasaje del día—, porque allí el final del texto es el
    final de la lectura.
    """
    entero = sum(len(x) for _t, x in bloques)
    if not entero:
        return bloques
    for i in range(len(bloques) - 1, -1, -1):
        tipo, texto = bloques[i]
        if tipo == "titulo" or not texto.rstrip().endswith(FIN_DE_FRASE):
            continue
        queda = sum(len(x) for _t, x in bloques[:i + 1])
        if entero - queda > RECORTE_ABSOLUTO and \
                queda / entero < MINIMO_QUE_QUEDA:
            return bloques
        return bloques[:i + 1]
    return bloques


# ----------------------------------------------------------------------
# Informe de calidad (para revisar un tomo entero)
# ----------------------------------------------------------------------
def revisar(registros: list[dict]) -> dict:
    """
    Qué tal queda un tomo al componerlo: cifras para comprobarlo.

    Lo usa `tools/formatear.py`. No cambia nada: solo mide.
    """
    hojas = parrafos = versos = 0
    antes = despues = 0
    marcas = llamadas = 0
    for reg in registros:
        if reg.get("tipo") != "pagina":
            continue
        cuerpo = reg.get("cuerpo") or ""
        if not cuerpo.strip():
            continue
        hojas += 1
        antes += len([l for l in cuerpo.splitlines() if l.strip()])
        pagina = componer(cuerpo, reg.get("notas") or "")
        despues += len(pagina.bloques)
        parrafos += sum(1 for t, _ in pagina.bloques if t == "parrafo")
        versos += sum(1 for t, _ in pagina.bloques if t == "verso")
        marcas += len(pagina.marcas)
        llamadas += len(pagina.llamadas)
    return {
        "hojas": hojas,
        "renglones_del_pdf": antes,
        "bloques_compuestos": despues,
        "parrafos": parrafos,
        "versos": versos,
        "marcas_al_margen": marcas,
        "llamadas_de_nota": llamadas,
        "renglones_por_bloque": round(antes / despues, 1) if despues else 0,
    }


def normaliza(texto: str) -> str:
    """Minúsculas y sin tildes (para comparar, no para mostrar)."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def mediana_de_renglon(cuerpo: str) -> Optional[float]:
    """Renglón mediano de una página, en caracteres (para diagnóstico)."""
    largos = [len(l.strip()) for l in (cuerpo or "").splitlines() if l.strip()]
    return statistics.median(largos) if largos else None
