"""
rag.py
======
Buscador sobre el TEXTO de los tomos ya extraídos (tarea RAG-1 del
plan `docs/PLAN_RAG.md`).

Qué es y qué no es
------------------
Es un índice de búsqueda literal (FTS5 de SQLite, ranking BM25) sobre
los `BDtomos/TextosTomos/*.jsonl`. No sube nada a ningún servicio, no
necesita clave de API y funciona sin conexión.

Reglas que no se pueden romper
------------------------------
- El índice vive en `BDtomos/textos.db`, **aparte de `tc_monitor.db`**:
  es un archivo DERIVADO y reconstruible; borrarlo no pierde nada.
- Los `.jsonl` son la FUENTE y no se tocan: aquí solo se leen.
- Cada pasaje guarda de dónde sale (tomo, obra, sección, hoja del PDF,
  página impresa, verso/parágrafo). Sin eso no hay CITA, que es el
  objetivo de todo esto — igual que en `pdftext`, jamás un texto
  corrido sin localización.
- Las NOTAS al pie y las notas finales se indexan en pasajes aparte
  (`clase = 'notas'`): son 66.781 hojas de las 101.402 del corpus y,
  mezcladas con el texto del autor, lo taparían en los resultados.

Reindexado incremental
----------------------
Cada tomo guarda la fecha y el tamaño de su `.jsonl`. `indexar()` solo
relee los archivos nuevos o cambiados, así que analizar un PDF más y
volver a indexar cuesta segundos, no minutos. Se puede seguir
analizando PDF mientras el índice existe: lo que falte entra en la
siguiente pasada.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from app.config import app_dir

logger = logging.getLogger(__name__)

DB_PATH = app_dir() / "BDtomos" / "textos.db"
TEXTOS_DIR = app_dir() / "BDtomos" / "TextosTomos"

# Versión del esquema/troceado. Al subirla, `indexar()` reconstruye
# TODO: si cambia cómo se trocea, los pasajes viejos no son comparables
# con los nuevos y mezclarlos daría citas descolocadas.
VERSION = 2

# Qué parte del principio de un tomo se salta el pasaje del día:
# ahí están la introducción y la nota bibliográfica del editor,
# que hablan DE la obra en vez de ser la obra.
_PRELIMINARES = 0.18

# Troceado: ventana de palabras y solape. La hoja media del corpus son
# 364 palabras (medido sobre los tomos reales), así que la mayoría da
# dos o tres pasajes. El solape evita que una frase partida justo en el
# corte se quede sin encontrar.
PALABRAS_PASAJE = 180
SOLAPE = 40

# Secciones cuyo texto es del AUTOR (lo que se busca casi siempre).
# Lo demás —notas, bibliografía, índices— sigue indexado, pero pesa
# menos en el ranking y se puede excluir con un filtro.
SECCIONES_TEXTO = ("texto", "introduccion")
SECCIONES_NOTAS = ("notas_finales",)

# Penalización del ranking por clase de pasaje: 1,0 es el texto; las
# notas y los aparatos valen menos porque casi nunca son la respuesta.
_PESO_CLASE = {"cuerpo": 1.0, "notas": 0.55}
# BM25 divide por la longitud del pasaje, así que una hoja que solo
# lleva un rótulo ("LVIII\nAQUILES", dos palabras) sale por delante del
# canto entero donde Aquiles habla. Medido con el corpus real: los
# cuatro primeros resultados de «Aquiles» eran rótulos. Los rótulos no
# se tiran —dicen de qué va la sección—, pero pesan menos.
_PESO_LARGO = ((12, 0.40), (30, 0.65), (60, 0.88))

_PESO_SECCION = {
    "texto": 1.0,
    "introduccion": 0.85,
    "notas_finales": 0.55,
    "bibliografia": 0.35,
    "indice_nombres": 0.30,
    "indice_general": 0.30,
    "abreviaturas": 0.30,
    "portada": 0.20,
}


class RagError(Exception):
    """El índice no existe, está a medias o SQLite no trae FTS5."""


# --------------------------------------------------------------------
# Esquema
# --------------------------------------------------------------------

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS tomos (
    id          INTEGER PRIMARY KEY,
    canonico    TEXT UNIQUE NOT NULL,
    numero      TEXT,
    orden       INTEGER,
    autor       TEXT,
    obras       TEXT,
    formato     TEXT,
    archivo     TEXT NOT NULL,
    mtime       INTEGER,
    bytes       INTEGER,
    hojas       INTEGER,
    palabras    INTEGER,
    pasajes     INTEGER DEFAULT 0,
    nombres     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tomos_archivo ON tomos(archivo);

CREATE TABLE IF NOT EXISTS pasajes (
    id          INTEGER PRIMARY KEY,
    tomo_id     INTEGER NOT NULL REFERENCES tomos(id),
    hoja        INTEGER,
    impresa     INTEGER,
    seccion     TEXT,
    obra        TEXT,
    titulo      TEXT,
    clase       TEXT,        -- 'cuerpo' | 'notas'
    nota        TEXT,        -- número de nota final, si la hoja es una
    versos      TEXT,        -- referencias del margen, separadas por espacio
    orden       INTEGER,     -- trozo dentro de la hoja (0, 1, 2…)
    palabras    INTEGER,
    texto       TEXT NOT NULL,
    -- Lo que se INDEXA, cuando no basta con `texto`. Solo lo llevan los
    -- pasajes con griego (el 1,2 % del corpus, 2 MB): ver `_plegado`.
    busqueda    TEXT
);

-- El índice lee de esta vista, no de la tabla: así el pasaje se GUARDA
-- tal cual está en el tomo y se BUSCA por una versión ampliada, sin
-- duplicar los 87 M de caracteres del corpus.
CREATE VIEW IF NOT EXISTS pasajes_indexados AS
    SELECT id, COALESCE(busqueda, texto) AS texto FROM pasajes;
CREATE INDEX IF NOT EXISTS idx_pasajes_tomo ON pasajes(tomo_id);

-- Índice de COBERTURA para el recuento por tomo. Sin él, contar en qué
-- tomos sale una palabra obligaba a leer la fila entera de cada pasaje
-- —y la fila lleva el TEXTO, así que son lecturas grandes y dispersas:
-- «hombre» (15.433 pasajes) tardaba 4 s con el disco frío. Con estas
-- cuatro columnas la consulta se resuelve dentro del índice y no toca
-- la tabla.
CREATE INDEX IF NOT EXISTS idx_pasajes_resumen
    ON pasajes(id, tomo_id, clase, hoja);

CREATE VIRTUAL TABLE IF NOT EXISTS pasajes_fts USING fts5(
    texto,
    content='pasajes_indexados',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

-- Sincronía del índice con la tabla real. Con `content=` el texto se
-- guarda UNA vez (en `pasajes`) y FTS solo lleva el índice; al borrar
-- hay que avisarle con la orden 'delete' ANTES de perder el texto, y
-- eso es justo lo que hace el disparador de borrado. OJO: se le pasa
-- SIEMPRE lo mismo que se indexó, o sea `COALESCE(busqueda, texto)`;
-- con el texto a secas, el índice se quedaría con restos de los
-- pasajes griegos al reindexar.
CREATE TRIGGER IF NOT EXISTS pasajes_ai AFTER INSERT ON pasajes BEGIN
    INSERT INTO pasajes_fts(rowid, texto)
    VALUES (new.id, COALESCE(new.busqueda, new.texto));
END;
CREATE TRIGGER IF NOT EXISTS pasajes_ad AFTER DELETE ON pasajes BEGIN
    INSERT INTO pasajes_fts(pasajes_fts, rowid, texto)
    VALUES ('delete', old.id, COALESCE(old.busqueda, old.texto));
END;
CREATE TRIGGER IF NOT EXISTS pasajes_au AFTER UPDATE ON pasajes BEGIN
    INSERT INTO pasajes_fts(pasajes_fts, rowid, texto)
    VALUES ('delete', old.id, COALESCE(old.busqueda, old.texto));
    INSERT INTO pasajes_fts(rowid, texto)
    VALUES (new.id, COALESCE(new.busqueda, new.texto));
END;

CREATE TABLE IF NOT EXISTS nombres (
    id          INTEGER PRIMARY KEY,
    tomo_id     INTEGER NOT NULL REFERENCES tomos(id),
    nombre      TEXT NOT NULL,
    normal      TEXT NOT NULL,
    refs        TEXT,        -- localizaciones, separadas por "; "
    cuantas     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nombres_tomo ON nombres(tomo_id);
CREATE INDEX IF NOT EXISTS idx_nombres_normal ON nombres(normal);

CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def fts5_disponible() -> bool:
    """¿Trae FTS5 el SQLite de este Python?"""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE p USING fts5(t)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


# --------------------------------------------------------------------
# Troceado del texto
# --------------------------------------------------------------------

def _trozos(texto: str) -> Iterator[str]:
    """
    Parte una hoja en pasajes de `PALABRAS_PASAJE` con solape.

    El RENGLÓN no se toca: en los poetas es el verso y es la unidad de
    cita, así que los saltos de línea se conservan tal cual. Para FTS5
    un salto de línea es un separador más, de modo que buscar una frase
    que cruza dos versos sigue funcionando.
    """
    texto = texto.strip()
    if not texto:
        return
    # Cada palabra se lleva su separador pegado: al volver a unirlas, el
    # salto de verso queda EXACTAMENTE donde estaba. Partir por " " a
    # secas contaría "final\nprincipio" como una sola palabra.
    palabras = re.findall(r"\S+\s*", texto)
    if len(palabras) <= PALABRAS_PASAJE:
        yield texto
        return
    paso = max(1, PALABRAS_PASAJE - SOLAPE)
    for inicio in range(0, len(palabras), paso):
        trozo = "".join(palabras[inicio:inicio + PALABRAS_PASAJE]).strip()
        if trozo:
            yield trozo
        if inicio + PALABRAS_PASAJE >= len(palabras):
            break


# El tokenizador de SQLite (`remove_diacritics 2`) quita las tildes
# LATINAS pero no las griegas: el índice guarda «πρᾶξισ» y «λόγοσ» con
# sus acentos, así que escribir el griego a pelo («πραξις», «λογος») no
# encontraba NADA (medido el 2026-08-08). Como el politónico es casi
# imposible de teclear, a cada pasaje con griego se le añade —solo para
# BUSCAR, nunca para mostrar— una copia de sus palabras griegas sin
# acentos. Cuesta 2 MB: el griego está en el 1,2 % de los pasajes.
_HAY_GRIEGO = re.compile("[Ͱ-Ͽἀ-῿]")


def _plegado(texto: str) -> Optional[str]:
    """
    Texto ampliado para el índice, o None si basta con el original.

    Devuelve el pasaje seguido de sus palabras griegas SIN acentos, de
    modo que se encuentre escribiéndolo de las dos maneras.
    """
    if not texto or not _HAY_GRIEGO.search(texto):
        return None
    sueltas = []
    for palabra in texto.split():
        if _HAY_GRIEGO.search(palabra):
            descompuesta = unicodedata.normalize("NFD", palabra)
            sueltas.append("".join(
                c for c in descompuesta if unicodedata.category(c) != "Mn"
            ))
    if not sueltas:
        return None
    return texto + "\n" + " ".join(sueltas)


def _clase_de(seccion: str) -> str:
    """'notas' para lo que no escribió el autor del tomo."""
    return "notas" if seccion in SECCIONES_NOTAS else "cuerpo"


def _pasajes_de_hoja(reg: dict) -> Iterator[dict]:
    """
    Los pasajes que salen de una hoja del `.jsonl`.

    El cuerpo y las notas al pie van SEPARADOS: comparten hoja, pero no
    son el mismo texto ni valen lo mismo al buscar.
    """
    seccion = reg.get("seccion") or ""
    comun = {
        "hoja": reg.get("pdf"),
        "impresa": reg.get("impresa"),
        "seccion": seccion,
        "obra": reg.get("obra") or "",
        "titulo": reg.get("titulo") or "",
        "nota": reg.get("nota") or "",
        "versos": " ".join(str(v) for v in (reg.get("versos") or [])),
    }
    for clase, bruto in (
        (_clase_de(seccion), reg.get("cuerpo") or ""),
        ("notas", reg.get("notas") or ""),
    ):
        for orden, trozo in enumerate(_trozos(bruto)):
            yield {
                **comun,
                "clase": clase,
                "orden": orden,
                "palabras": len(trozo.split()),
                "texto": trozo,
            }


# --------------------------------------------------------------------
# Índice
# --------------------------------------------------------------------

@dataclass
class Progreso:
    """Lo que se lleva indexado, para la barra de la ventana."""
    tomos: int = 0
    total: int = 0
    pasajes: int = 0
    nombres: int = 0
    saltados: int = 0
    borrados: int = 0
    errores: list = field(default_factory=list)


class Indice:
    """
    El índice de búsqueda. Abre (o crea) `BDtomos/textos.db`.

    Todos los métodos son seguros entre hilos: el trabajo pesado
    (indexar) corre en un QThread desde la interfaz mientras el hilo del
    monitor y el de la GUI siguen a lo suyo, igual que en `Database`.
    OJO, misma regla: NINGÚN método puede anidar `self._lock`.
    """

    def __init__(self, ruta: Optional[Path] = None) -> None:
        self.ruta = Path(ruta) if ruta else DB_PATH
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        if not fts5_disponible():
            raise RagError(
                "Este Python trae un SQLite sin FTS5; el buscador de textos "
                "no puede funcionar."
            )
        self._lock = threading.Lock()
        self._con = sqlite3.connect(str(self.ruta), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._preparar_para_esta_version()
        self._con.executescript(_ESQUEMA)
        self._con.execute("PRAGMA journal_mode=WAL")
        # El índice pesa 250 MB y una consulta toca páginas repartidas
        # por todo el archivo: con la caché de serie (2 MB) cada búsqueda
        # nueva vuelve al disco. 64 MB de caché y mapeo en memoria salen
        # gratis y se notan en la primera consulta de cada palabra.
        self._con.execute("PRAGMA cache_size=-65536")
        self._con.execute("PRAGMA mmap_size=536870912")
        self._comprueba_version()

    # -- ciclo de vida ------------------------------------------------

    def _preparar_para_esta_version(self) -> None:
        """
        Pone el esquema al día cuando el archivo es de una versión vieja.

        Se hace EN EL SITIO, no borrando el archivo: la aplicación puede
        tenerlo abierto y en Windows entonces no se puede borrar. Y
        tampoco basta con vaciar las tablas — los `CREATE ... IF NOT
        EXISTS` no tocan lo que ya existe, así que hay que tirar
        expresamente el índice, sus disparadores y la vista, y añadir las
        columnas nuevas. El contenido se vuelve a construir después.
        """
        try:
            fila = self._con.execute(
                "SELECT valor FROM meta WHERE clave='version'"
            ).fetchone()
            version = fila[0] if fila else None
        except sqlite3.Error:
            return                  # base recién creada: nada que migrar
        if version is None or version == str(VERSION):
            return
        logger.info(
            "El índice de textos es de la versión %s y ahora es la %s: se "
            "rehace el esquema.", version, VERSION,
        )
        for orden in (
            "DROP TRIGGER IF EXISTS pasajes_ai",
            "DROP TRIGGER IF EXISTS pasajes_ad",
            "DROP TRIGGER IF EXISTS pasajes_au",
            "DROP TABLE IF EXISTS pasajes_fts",
            "DROP VIEW IF EXISTS pasajes_indexados",
        ):
            self._con.execute(orden)
        columnas = {
            r[1] for r in self._con.execute("PRAGMA table_info(pasajes)")
        }
        if columnas and "busqueda" not in columnas:
            self._con.execute("ALTER TABLE pasajes ADD COLUMN busqueda TEXT")
        # Vaciar AHORA, con los disparadores ya fuera. Si se dejara para
        # después de recrear el índice, cada borrado intentaría quitar del
        # índice —recién creado y vacío— una fila que no está, y SQLite
        # responde "database disk image is malformed".
        for tabla in ("pasajes", "nombres", "tomos"):
            try:
                self._con.execute(f"DELETE FROM {tabla}")
            except sqlite3.Error:
                pass
        self._con.execute(
            "INSERT OR REPLACE INTO meta(clave, valor) VALUES('version', ?)",
            (str(VERSION),),
        )
        self._con.commit()

    def _comprueba_version(self) -> None:
        fila = self._con.execute(
            "SELECT valor FROM meta WHERE clave='version'"
        ).fetchone()
        if fila is None:
            self._con.execute(
                "INSERT INTO meta(clave, valor) VALUES('version', ?)",
                (str(VERSION),),
            )
            self._con.commit()
            return
        if fila["valor"] != str(VERSION):
            logger.info(
                "El índice de textos es de la versión %s y ahora es la %s: "
                "se reconstruye entero.", fila["valor"], VERSION,
            )
            self._vaciar()

    def _vaciar(self) -> None:
        self._con.execute("DELETE FROM pasajes")     # el disparador limpia FTS
        self._con.execute("DELETE FROM nombres")
        self._con.execute("DELETE FROM tomos")
        self._con.execute(
            "INSERT OR REPLACE INTO meta(clave, valor) VALUES('version', ?)",
            (str(VERSION),),
        )
        self._con.commit()

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # -- construcción -------------------------------------------------

    def indexar(
        self,
        progreso: Optional[Callable[[str, int, int], None]] = None,
        cancelado: Optional[Callable[[], bool]] = None,
        forzar: bool = False,
    ) -> Progreso:
        """
        Mete en el índice los textos nuevos o cambiados.

        `forzar=True` rehace todos. Devuelve el recuento; los tomos con
        un `.jsonl` roto se anotan en `errores` y no cortan el trabajo:
        más vale un índice con 131 tomos que ninguno.
        """
        res = Progreso()
        archivos = sorted(TEXTOS_DIR.glob("*.jsonl")) if TEXTOS_DIR.exists() else []
        res.total = len(archivos)
        with self._lock:
            conocidos = {
                fila["archivo"]: fila
                for fila in self._con.execute(
                    "SELECT id, archivo, mtime, bytes FROM tomos"
                )
            }
            # Textos que ya no están (el usuario borró el .jsonl)
            vivos = {a.name for a in archivos}
            for nombre, fila in conocidos.items():
                if nombre not in vivos:
                    self._borrar_tomo(fila["id"])
                    res.borrados += 1

            for numero, archivo in enumerate(archivos, 1):
                if cancelado is not None and cancelado():
                    break
                if progreso is not None:
                    progreso(f"Indexando {archivo.stem[:40]}", numero, len(archivos))
                stat = archivo.stat()
                previo = conocidos.get(archivo.name)
                if (
                    not forzar
                    and previo is not None
                    and previo["mtime"] == int(stat.st_mtime)
                    and previo["bytes"] == stat.st_size
                ):
                    res.saltados += 1
                    continue
                try:
                    pasajes, nombres = self._indexar_archivo(archivo, stat)
                except (OSError, ValueError) as exc:
                    logger.warning("Texto ilegible %s: %s", archivo.name, exc)
                    res.errores.append(f"{archivo.name}: {exc}")
                    continue
                res.tomos += 1
                res.pasajes += pasajes
                res.nombres += nombres
            self._con.commit()
        logger.info(
            "Índice de textos: %d tomos nuevos o cambiados, %d pasajes, "
            "%d nombres (%d sin cambios).",
            res.tomos, res.pasajes, res.nombres, res.saltados,
        )
        # Reindexar borra e inserta, y SQLite se queda las páginas
        # libres: tras rehacer el corpus entero el archivo pasó de 256 a
        # 325 MB sin un solo pasaje más. Solo se compacta cuando ha
        # habido trabajo de verdad — un VACUUM reescribe el archivo.
        if res.tomos >= 20:
            if progreso is not None:
                progreso("Compactando el índice", res.total, res.total)
            self.compactar()
        return res

    def compactar(self) -> None:
        """Devuelve al disco el hueco que dejan los reindexados."""
        with self._lock:
            antes = self.ruta.stat().st_size if self.ruta.exists() else 0
            self._con.execute("VACUUM")
            despues = self.ruta.stat().st_size if self.ruta.exists() else 0
        logger.info(
            "Índice compactado: %.0f MB → %.0f MB", antes / 1e6, despues / 1e6
        )

    def _borrar_tomo(self, tomo_id: int) -> None:
        self._con.execute("DELETE FROM pasajes WHERE tomo_id=?", (tomo_id,))
        self._con.execute("DELETE FROM nombres WHERE tomo_id=?", (tomo_id,))
        self._con.execute("DELETE FROM tomos WHERE id=?", (tomo_id,))

    def _indexar_archivo(self, archivo: Path, stat) -> tuple[int, int]:
        """Vuelca UN `.jsonl` al índice. Devuelve (pasajes, nombres)."""
        with archivo.open(encoding="utf-8") as fh:
            cabecera = json.loads(fh.readline() or "{}")
            canonico = (cabecera.get("canonico") or archivo.stem).strip()

            # Un tomo reanalizado puede cambiar de nombre de archivo:
            # se borra por canónico Y por archivo, para no duplicarlo.
            for fila in self._con.execute(
                "SELECT id FROM tomos WHERE canonico=? OR archivo=?",
                (canonico, archivo.name),
            ).fetchall():
                self._borrar_tomo(fila["id"])

            cur = self._con.execute(
                "INSERT INTO tomos(canonico, numero, orden, autor, obras, "
                "formato, archivo, mtime, bytes, hojas, palabras) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    canonico,
                    str(cabecera.get("numero") or ""),
                    cabecera.get("orden") or 0,
                    cabecera.get("autor") or "",
                    cabecera.get("obras") or "",
                    cabecera.get("formato") or "",
                    archivo.name,
                    int(stat.st_mtime),
                    stat.st_size,
                    cabecera.get("hojas_texto") or 0,
                    cabecera.get("palabras") or 0,
                ),
            )
            tomo_id = cur.lastrowid

            filas = []
            for linea in fh:
                linea = linea.strip()
                if not linea:
                    continue
                reg = json.loads(linea)
                for p in _pasajes_de_hoja(reg):
                    filas.append((
                        tomo_id, p["hoja"], p["impresa"], p["seccion"],
                        p["obra"], p["titulo"], p["clase"], p["nota"],
                        p["versos"], p["orden"], p["palabras"], p["texto"],
                        _plegado(p["texto"]),
                    ))
                    if len(filas) >= 2000:
                        self._insertar_pasajes(filas)
                        filas = []
            if filas:
                self._insertar_pasajes(filas)

        nombres = self._indexar_nombres(tomo_id, cabecera.get("indice_nombres"))
        cuantos = self._con.execute(
            "SELECT COUNT(*) FROM pasajes WHERE tomo_id=?", (tomo_id,)
        ).fetchone()[0]
        self._con.execute(
            "UPDATE tomos SET pasajes=?, nombres=? WHERE id=?",
            (cuantos, nombres, tomo_id),
        )
        return cuantos, nombres

    def _insertar_pasajes(self, filas: list) -> None:
        self._con.executemany(
            "INSERT INTO pasajes(tomo_id, hoja, impresa, seccion, obra, "
            "titulo, clase, nota, versos, orden, palabras, texto, busqueda) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            filas,
        )

    def _indexar_nombres(self, tomo_id: int, indice) -> int:
        """
        El índice de nombres del traductor, tal como lo dejó `pdftext`.

        Se filtra la basura que arrastra el OCR ("y ss", romanos
        sueltos, entradas de una letra): no son nombres y ensuciarían
        la respuesta directa de RAG-3.
        """
        if not isinstance(indice, dict):
            return 0
        filas = []
        for nombre, refs in indice.items():
            nombre = (nombre or "").strip()
            if not _es_nombre(nombre):
                continue
            refs = [str(r).strip() for r in (refs or []) if str(r).strip()]
            filas.append((
                tomo_id, nombre, normaliza(nombre), "; ".join(refs), len(refs),
            ))
        if filas:
            self._con.executemany(
                "INSERT INTO nombres(tomo_id, nombre, normal, refs, cuantas) "
                "VALUES(?,?,?,?,?)",
                filas,
            )
        return len(filas)

    # -- consulta -----------------------------------------------------

    def expresion(self, consulta: str, estado: Optional[dict] = None) -> str:
        """
        Lo que se va a pedir realmente a FTS5, ya aflojado si hace falta.

        Primero se exigen TODAS las palabras. Si con eso no hay ni un
        pasaje en todo el índice, se sueltan las más COMUNES y se
        siguen exigiendo las raras — «lacedemonios» nunca se suelta,
        que es la que lleva el significado. En `estado` (un dict de
        fuera) queda anotado por cuál de las dos vías se respondió, para
        que la ventana pueda decirlo.

        Se calcula APARTE de la consulta grande para que la vista por
        tomos y la de pasajes usen exactamente la misma expresión: si
        cada una aflojara por su cuenta, el recuento de una no cuadraría
        con los resultados de la otra.
        """
        piezas = piezas_fts(consulta)
        if not piezas:
            if estado is not None:
                estado["modo"] = "vacia"
            return ""
        match = " ".join(piezas)
        if estado is not None:
            estado["modo"] = (
                "solo_vacias" if solo_palabras_vacias(consulta) else "todas"
            )
        if self._hay_algo(match) or len(piezas) == 1:
            return match

        raras = self._por_rareza(piezas)
        # Si `_por_rareza` ya descartó alguna palabra por no estar en
        # ningún pasaje, hay que probar con TODAS las que quedan antes
        # de empezar a soltar: si no, «trirreme Aquiles» (donde trirreme
        # no sale en el corpus) no llegaba ni a buscar Aquiles.
        inicio = len(raras) if len(raras) < len(piezas) else len(raras) - 1
        for cuantas in range(inicio, 0, -1):
            usadas = raras[:cuantas]
            expresion = " ".join(usadas)
            if self._hay_algo(expresion):
                if estado is not None:
                    estado["modo"] = "algunas"
                    estado["palabras"] = [
                        p.strip("*").strip('"') for p in usadas
                    ]
                return expresion
        return match          # no hay nada; que la consulta lo diga

    def _hay_algo(self, expresion: str) -> bool:
        """¿Hay AL MENOS un pasaje? (sin traerse ninguno)."""
        with self._lock:
            try:
                return self._con.execute(
                    "SELECT 1 FROM pasajes_fts WHERE pasajes_fts MATCH ? "
                    "LIMIT 1", (expresion,)
                ).fetchone() is not None
            except sqlite3.OperationalError as exc:
                raise RagError(f"Consulta no válida: {exc}") from exc

    def tomos_con(
        self,
        consulta: str,
        incluir_notas: bool = True,
        estado: Optional[dict] = None,
    ) -> list[dict]:
        """
        TODOS los tomos donde aparece lo buscado, con cuántos pasajes.

        Es la vista principal de resultados, y es COMPLETA: no hay tope
        de candidatos. Pidiendo pasajes sueltos había que cortar por
        algún sitio (los 400 mejores por BM25) y los tomos que caían
        fuera no aparecían jamás — buscando «lacedemonios», que sale en
        2.510 pasajes, faltaban las obras menores de Jenofonte.

        No devuelve TEXTO: solo el recuento y dónde está lo mejor de
        cada tomo. El texto se pide tomo a tomo, al desplegarlo.
        """
        match = self.expresion(consulta, estado)
        if not match:
            return []
        # Aquí NO se usa bm25(): SQLite no deja llamarla dentro de un
        # agregado. Y tampoco hace falta — lo que ordena esta vista es
        # cuántas veces sale lo buscado en cada tomo.
        sql = [
            "SELECT t.id AS tomo_id, t.canonico, t.autor, t.numero, t.orden,",
            "       COUNT(*) AS pasajes,",
            "       COUNT(DISTINCT p.hoja) AS hojas",
            "  FROM pasajes_fts",
            "  JOIN pasajes p ON p.id = pasajes_fts.rowid",
            "  JOIN tomos   t ON t.id = p.tomo_id",
            " WHERE pasajes_fts MATCH ?",
        ]
        if not incluir_notas:
            sql.append("   AND p.clase = 'cuerpo'")
        sql.append(" GROUP BY t.id ORDER BY pasajes DESC, t.orden")
        with self._lock:
            try:
                filas = self._con.execute("\n".join(sql), (match,)).fetchall()
            except sqlite3.OperationalError as exc:
                raise RagError(f"Consulta no válida: {exc}") from exc
        return [dict(f) for f in filas]

    def buscar(
        self,
        consulta: str,
        limite: int = 40,
        incluir_notas: bool = True,
        tomo: str = "",
        autor: str = "",
        obra: str = "",
        candidatos: int = 400,
        por_tomo: int = 3,
        estado: Optional[dict] = None,
    ) -> list["Hallazgo"]:
        """
        Busca una frase o unas palabras y devuelve pasajes con su cita.

        El orden lo da BM25 de FTS5, corregido por dónde aparece: el
        texto del autor pesa más que una nota o una entrada de la
        bibliografía. Se piden `candidatos` a FTS y se reordenan, para
        que el filtro por sección no deje la lista vacía.

        Se devuelve UN pasaje por hoja y como mucho `por_tomo` por tomo:
        los pasajes se solapan 40 palabras a propósito, así que la misma
        página salía dos y tres veces seguidas, y un tomo que repite
        mucho una palabra llenaba la lista entera él solo.

        Si exigiendo TODAS las palabras no sale nada, se repite pidiendo
        ALGUNAS: la gente pregunta en vez de teclear palabras clave
        («¿qué tomo habla de los lacedemonios?» no tiene por qué llevar
        «tomo» y «habla» en la misma página). En `estado` —un dict de
        fuera— se anota cuál de las dos vías respondió, para que la
        ventana pueda decirlo.
        """
        match = self.expresion(consulta, estado)
        if not match:
            return []
        sql = [
            "SELECT p.id, p.tomo_id, p.hoja, p.impresa, p.seccion, p.obra,",
            "       p.titulo, p.clase, p.nota, p.versos, p.texto, p.palabras,",
            "       t.canonico, t.autor, t.numero, t.orden,",
            "       bm25(pasajes_fts) AS bm",
            "  FROM pasajes_fts",
            "  JOIN pasajes p ON p.id = pasajes_fts.rowid",
            "  JOIN tomos   t ON t.id = p.tomo_id",
            " WHERE pasajes_fts MATCH ?",
        ]
        args: list = [match]
        if not incluir_notas:
            sql.append("   AND p.clase = 'cuerpo'")
        if tomo:
            sql.append("   AND t.canonico = ?")
            args.append(tomo)
        if autor:
            sql.append("   AND t.autor LIKE ?")
            args.append(f"%{autor}%")
        if obra:
            sql.append("   AND p.obra LIKE ?")
            args.append(f"%{obra}%")
        sql.append(" ORDER BY bm LIMIT ?")
        args.append(max(limite, candidatos))
        consulta_sql = "\n".join(sql)

        def pide(expresion: str) -> list:
            args[0] = expresion
            with self._lock:
                try:
                    return self._con.execute(consulta_sql, args).fetchall()
                except sqlite3.OperationalError as exc:
                    raise RagError(f"Consulta no válida: {exc}") from exc

        hallazgos = [_hallazgo(f) for f in pide(match)]
        hallazgos.sort(key=lambda h: h.peso)

        vistos: set = set()
        cuenta: dict = {}
        limpio: list[Hallazgo] = []
        for h in hallazgos:
            clave = (h.canonico, h.hoja, h.clase)
            if clave in vistos:
                continue
            if por_tomo and cuenta.get(h.canonico, 0) >= por_tomo:
                continue
            vistos.add(clave)
            cuenta[h.canonico] = cuenta.get(h.canonico, 0) + 1
            limpio.append(h)
            if len(limpio) >= limite:
                break
        return limpio

    def _por_rareza(self, piezas: list[str]) -> list[str]:
        """
        Las palabras de la consulta, de la más RARA a la más común.

        La rareza es la que manda al aflojar una búsqueda: la palabra
        que aparece en menos pasajes es la que lleva el significado.
        Una que no aparezca en ninguno se descarta (con ella la búsqueda
        jamás daría nada).
        """
        conteos = []
        with self._lock:
            for pieza in piezas:
                try:
                    cuantos = self._con.execute(
                        "SELECT COUNT(*) FROM pasajes_fts "
                        " WHERE pasajes_fts MATCH ?", (pieza,)
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    cuantos = 0
                conteos.append((cuantos, pieza))
        conteos.sort(key=lambda par: par[0])
        return [pieza for cuantos, pieza in conteos if cuantos]

    def buscar_nombres(self, consulta: str, limite: int = 60) -> list[dict]:
        """
        Busca en el índice de nombres del traductor.

        Es la respuesta DIRECTA a «¿qué tomo habla de X?» cuando X es un
        nombre propio: no hace falta leer el texto, el propio traductor
        ya lo dejó dicho con su localización exacta.
        """
        clave = normaliza(consulta).strip()
        if not clave:
            return []
        with self._lock:
            filas = self._con.execute(
                "SELECT n.nombre, n.refs, n.cuantas, t.canonico, t.autor, "
                "       t.numero, t.orden "
                "  FROM nombres n JOIN tomos t ON t.id = n.tomo_id "
                " WHERE n.normal = ? OR n.normal LIKE ? "
                " ORDER BY (n.normal = ?) DESC, n.cuantas DESC "
                " LIMIT ?",
                (clave, f"%{clave}%", clave, limite),
            ).fetchall()
        return [dict(f) for f in filas]

    def hoja_completa(self, canonico: str, hoja: int) -> dict:
        """
        Toda la hoja de la que salió un pasaje: cuerpo, notas y cita.

        Es lo que se enseña al pulsar dos veces un resultado. El pasaje
        recortado sirve para ojear; para leer hace falta la página, y
        pedirla aquí evita abrir el `.jsonl` de 100 MB desde la
        interfaz.
        """
        with self._lock:
            filas = self._con.execute(
                "SELECT p.*, t.canonico, t.autor, t.numero FROM pasajes p "
                "  JOIN tomos t ON t.id = p.tomo_id "
                " WHERE t.canonico = ? AND p.hoja = ? "
                " ORDER BY p.clase DESC, p.orden",
                (canonico, hoja),
            ).fetchall()
        if not filas:
            return {}
        # Los pasajes de una hoja se solapan: se recompone quitando la
        # repetición, no concatenando a lo bruto.
        partes: dict = {"cuerpo": [], "notas": []}
        for fila in filas:
            partes.setdefault(fila["clase"], []).append(fila["texto"])
        salida = {
            "canonico": filas[0]["canonico"],
            "autor": filas[0]["autor"],
            "numero": str(filas[0]["numero"] or ""),
            "hoja": hoja,
            "impresa": filas[0]["impresa"],
            "seccion": filas[0]["seccion"],
            "obra": filas[0]["obra"],
            "titulo": filas[0]["titulo"],
            "versos": filas[0]["versos"],
        }
        for clase, trozos in partes.items():
            salida[clase] = _recomponer(trozos)
        return salida

    def sugerir_nombres(self, prefijo: str, limite: int = 12) -> list[str]:
        """Nombres que empiezan así (para el autocompletado del buscador)."""
        clave = normaliza(prefijo).strip()
        if len(clave) < 2:
            return []
        with self._lock:
            filas = self._con.execute(
                "SELECT nombre, SUM(cuantas) AS peso FROM nombres "
                " WHERE normal LIKE ? GROUP BY normal "
                " ORDER BY peso DESC LIMIT ?",
                (f"{clave}%", limite),
            ).fetchall()
        return [f["nombre"] for f in filas]

    # ------------------------------------------------------------------
    # Pasaje del día
    # ------------------------------------------------------------------
    # Rótulos que NO son texto de la obra: por muy largo que sea el
    # pasaje, una nota bibliográfica no es una lectura del día.
    _RUTINA = (
        "bibliograf", "nota ", "notas", "indice", "índice", "abreviatur",
        "advertencia", "sigla", "apendice", "apéndice", "prologo",
        "prólogo", "cronolog", "traduccion", "traducción",
        # Aparato del editor, no obra del autor. Salían con títulos como
        # «licios y Creta · a) Ediciones» (2026-08-08).
        "edicion", "edición", "comentario", "estudio", "introducc",
        "argumento", "sinopsis", "esquema", "resumen", "presentacion",
        "presentación", "manuscrito", "aparato", "stemma", "conspectus",
    )
    # Cuántos nombres del índice tiene que traer el pasaje. Con uno solo
    # colaban las introducciones, que nombran al autor y poco más.
    _NOMBRES_MINIMOS = 2

    def pasaje_del_dia(self, fecha: str) -> dict:
        """
        Un pasaje escogido al azar, el mismo durante todo el día.

        `fecha` en formato ISO (AAAA-MM-DD). La elección se guarda en
        `meta`, así que se mantiene aunque se cierre la aplicación y
        cambia sola al día siguiente.

        Vuelve con el pasaje, su página entera, un TÍTULO y una
        descripción breve. Los dos se sacan del propio tomo —del índice
        de nombres del traductor y del texto—, no los escribe ninguna
        IA: aquí no hay ninguna, y un resumen inventado en una
        biblioteca es peor que ninguno.
        """
        guardado = self._leer_meta("pasaje_del_dia")
        elegido = None
        if guardado:
            try:
                datos = json.loads(guardado)
                if datos.get("fecha") == fecha:
                    elegido = int(datos["id"])
            except (ValueError, KeyError, TypeError):
                elegido = None
        if elegido is None:
            elegido = self._sortear_pasaje(fecha)
            if elegido is None:
                return {}
            self._escribir_meta(
                "pasaje_del_dia", json.dumps({"fecha": fecha, "id": elegido})
            )
        return self.ficha_de_pasaje(elegido)

    def _sortear_pasaje(self, semilla: str) -> Optional[int]:
        """
        Un pasaje al azar entre los que valen la pena.

        Se elige por identificador y no con OFFSET: con 106.583
        candidatos, saltar por número de fila obliga a SQLite a
        recorrerlos todos.

        Tres cribas, y las tres salieron de mirar lo que devolvía:
        · Solo tomos CON índice de nombres (60 de 210): son los únicos
          de los que se puede sacar un título que diga de qué va el
          pasaje. Sin esto salía el título del tomo, que no dice nada.
        · Nada del primer `_PRELIMINARES` del tomo: ahí están la
          introducción y la nota bibliográfica del editor, que hablan
          DE la obra en vez de ser la obra.
        · Fuera los rótulos de aparato (`_RUTINA`).
        """
        with self._lock:
            fila = self._con.execute(
                "SELECT MIN(id), MAX(id) FROM pasajes"
            ).fetchone()
            if not fila or fila[0] is None:
                return None
            bajo, alto = int(fila[0]), int(fila[1])
            dado = int(
                hashlib.sha1(semilla.encode("utf-8")).hexdigest(), 16
            ) % max(1, alto - bajo + 1) + bajo
            candidatos = []
            # Desde ese punto, y si se acaba la tabla se vuelve a empezar
            for desde in (dado, bajo):
                for f in self._con.execute(
                    "SELECT p.id, p.tomo_id, p.titulo, p.obra, p.texto, "
                    "       p.hoja, t.hojas "
                    "  FROM pasajes p JOIN tomos t ON t.id = p.tomo_id "
                    " WHERE p.id >= ? AND p.clase='cuerpo' "
                    "   AND p.seccion='texto' "
                    "   AND p.palabras BETWEEN 110 AND 190 "
                    "   AND t.nombres > 0 "
                    " ORDER BY p.id LIMIT 200",
                    (desde,),
                ):
                    hojas = int(f["hojas"] or 0)
                    if hojas and int(f["hoja"] or 0) < hojas * _PRELIMINARES:
                        continue
                    rotulo = normaliza(f"{f['titulo']} {f['obra']}")
                    if any(r in rotulo for r in self._RUTINA):
                        continue
                    candidatos.append(
                        (int(f["id"]), int(f["tomo_id"]), f["texto"])
                    )
                if candidatos:
                    break
            if not candidatos:
                # Con pocos tomos indexados no se cumple ninguna criba:
                # vale cualquier pasaje de cuerpo con algo de texto, que
                # es mejor que quedarse sin pasaje del día.
                candidatos = [
                    (int(f["id"]), int(f["tomo_id"]), f["texto"])
                    for f in self._con.execute(
                        "SELECT id, tomo_id, texto FROM pasajes "
                        " WHERE clase='cuerpo' AND palabras >= 8 "
                        " ORDER BY id"
                    )
                ]
        if not candidatos:
            return None
        # De esos, el que traiga NOMBRES del índice —sin ellos no hay de
        # qué titularlo—, empezando por donde diga el dado y NO por el
        # primero: dos días seguidos caen cerca y con «el primero»
        # repetían pasaje.
        arranque = dado % len(candidatos)
        vuelta = candidatos[arranque:] + candidatos[:arranque]
        for minimo in (self._NOMBRES_MINIMOS, 1):
            for pasaje_id, tomo_id, texto in vuelta[:40]:
                if len(self._nombres_en(tomo_id, texto, tope=3)) >= minimo:
                    return pasaje_id
        return vuelta[0][0]

    def ficha_de_pasaje(self, pasaje_id: int) -> dict:
        """El pasaje, su página, su título y de qué va."""
        with self._lock:
            fila = self._con.execute(
                "SELECT p.*, t.canonico, t.autor, t.numero, t.id AS tomo "
                "  FROM pasajes p JOIN tomos t ON t.id = p.tomo_id "
                " WHERE p.id = ?", (pasaje_id,),
            ).fetchone()
        if fila is None:
            return {}
        hoja = self.hoja_completa(fila["canonico"], fila["hoja"]) or {}
        nombres = self._nombres_en(fila["tomo"], fila["texto"])
        return {
            "id": pasaje_id,
            "canonico": fila["canonico"],
            "autor": fila["autor"],
            "numero": str(fila["numero"] or ""),
            "hoja": fila["hoja"],
            "impresa": fila["impresa"],
            "obra": fila["obra"] or "",
            "titulo_seccion": fila["titulo"] or "",
            "texto": fila["texto"],
            "pagina": hoja,
            "nombres": nombres,
            "titulo": titulo_de_pasaje(
                fila["texto"], fila["obra"] or "", fila["titulo"] or "",
                nombres, fila["canonico"],
            ),
            "descripcion": descripcion_de_pasaje(fila["texto"], nombres),
        }

    def _nombres_en(self, tomo_id: int, texto: str, tope: int = 6) -> list[str]:
        """
        Qué nombres del índice del traductor salen en este pasaje.

        Es el mejor ancla temática que hay sin IA: quien hizo el tomo ya
        decidió qué merecía entrada propia.
        """
        plano = normaliza(texto)
        with self._lock:
            filas = self._con.execute(
                "SELECT nombre, normal FROM nombres WHERE tomo_id = ? "
                " ORDER BY LENGTH(normal) DESC", (tomo_id,),
            ).fetchall()
        salida: list[tuple[int, str]] = []
        for f in filas:
            clave = f["normal"].split(",")[0].strip()
            if len(clave) < 4:
                continue
            veces = plano.count(clave)
            if veces:
                salida.append((veces, f["nombre"].split(",")[0].strip()))
        salida.sort(key=lambda par: (-par[0], -len(par[1])))
        vistos: list[str] = []
        for _veces, nombre in salida:
            if not any(normaliza(nombre) in normaliza(v) for v in vistos):
                vistos.append(nombre)
            if len(vistos) >= tope:
                break
        return vistos

    def _leer_meta(self, clave: str) -> Optional[str]:
        with self._lock:
            fila = self._con.execute(
                "SELECT valor FROM meta WHERE clave = ?", (clave,)
            ).fetchone()
        return fila["valor"] if fila else None

    def _escribir_meta(self, clave: str, valor: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO meta(clave, valor) VALUES(?, ?)",
                (clave, valor),
            )
            self._con.commit()

    # -- estado -------------------------------------------------------

    def resumen(self) -> dict:
        """Cifras del índice, para la ventana y el registro."""
        with self._lock:
            fila = self._con.execute(
                "SELECT COUNT(*) AS tomos, COALESCE(SUM(pasajes),0) AS pasajes, "
                "       COALESCE(SUM(nombres),0) AS nombres, "
                "       COALESCE(SUM(palabras),0) AS palabras FROM tomos"
            ).fetchone()
        datos = dict(fila)
        try:
            datos["bytes"] = self.ruta.stat().st_size
        except OSError:
            datos["bytes"] = 0
        datos["pendientes"] = self.pendientes()
        return datos

    def pendientes(self) -> int:
        """Cuántos textos hay sin indexar o cambiados desde la última vez."""
        if not TEXTOS_DIR.exists():
            return 0
        with self._lock:
            conocidos = {
                f["archivo"]: (f["mtime"], f["bytes"])
                for f in self._con.execute(
                    "SELECT archivo, mtime, bytes FROM tomos"
                )
            }
        cuantos = 0
        for archivo in TEXTOS_DIR.glob("*.jsonl"):
            stat = archivo.stat()
            previo = conocidos.get(archivo.name)
            if previo is None or previo != (int(stat.st_mtime), stat.st_size):
                cuantos += 1
        return cuantos

    def tomos(self) -> list[dict]:
        """Los tomos indexados, en orden de colección."""
        with self._lock:
            filas = self._con.execute(
                "SELECT canonico, autor, numero, orden, pasajes, nombres, "
                "       palabras FROM tomos ORDER BY orden, canonico"
            ).fetchall()
        return [dict(f) for f in filas]


# --------------------------------------------------------------------
# Resultados
# --------------------------------------------------------------------

@dataclass
class Hallazgo:
    """Un pasaje encontrado, con todo lo necesario para citarlo."""
    id: int
    canonico: str
    autor: str
    numero: str
    orden: int
    obra: str
    titulo: str
    seccion: str
    clase: str
    hoja: Optional[int]
    impresa: Optional[int]
    nota: str
    versos: str
    texto: str
    bm: float
    peso: float

    def cita(self) -> str:
        """
        De dónde sale, dicho como se cita un tomo.

        Se prefiere SIEMPRE la página impresa: la hoja del PDF no
        existe en el libro de papel y no sirve para citar. Si el tomo es
        una edición digital sin folio, manda el verso o el parágrafo.
        """
        partes = []
        if self.numero:
            partes.append(f"Tomo {self.numero}")
        if self.obra and self.obra != self.titulo:
            partes.append(self.obra)
        elif self.titulo:
            partes.append(self.titulo)
        if self.impresa:
            partes.append(f"pág. {self.impresa}")
        elif self.versos:
            # La referencia del margen va sola, sin el signo §.
            partes.append(self.versos.split()[0])
        elif self.hoja:
            partes.append(f"hoja {self.hoja} del PDF")
        if self.clase == "notas":
            partes.append(f"nota {self.nota}" if self.nota else "en nota")
        return " · ".join(partes)

    def encabezado(self) -> str:
        """
        Título del tomo para la cabecera del resultado.

        El canónico YA lleva el autor ("Heródoto — Historia · Libros
        I-II"): anteponerlo otra vez daba "Heródoto — Heródoto — …".
        """
        return self.canonico


def _recomponer(trozos: list[str]) -> str:
    """
    Vuelve a juntar los pasajes de una hoja quitando el solape.

    Se trocea con 40 palabras de repetición a propósito (una frase
    partida por el corte se seguiría encontrando); al mostrar la hoja
    entera, esa repetición hay que retirarla o el lector ve el mismo
    párrafo dos veces.
    """
    if not trozos:
        return ""
    entero = trozos[0]
    for trozo in trozos[1:]:
        palabras = re.findall(r"\S+\s*", trozo)
        pegado = False
        # Se prueba el solape de mayor a menor: el primero que case es
        # el bueno (los cortes son de tamaño conocido).
        for cuantas in range(min(SOLAPE + 5, len(palabras)), 3, -1):
            cabeza = "".join(palabras[:cuantas]).strip()
            if cabeza and entero.rstrip().endswith(cabeza):
                entero = entero.rstrip() + " " + "".join(palabras[cuantas:])
                pegado = True
                break
        if not pegado:
            entero = entero.rstrip() + "\n" + trozo
    return entero.strip()


def _peso_largo(palabras: int) -> float:
    """Cuánto vale un pasaje según lo que ocupa (ver `_PESO_LARGO`)."""
    for tope, peso in _PESO_LARGO:
        if palabras < tope:
            return peso
    return 1.0


def _hallazgo(fila: sqlite3.Row) -> Hallazgo:
    bm = float(fila["bm"])           # BM25 de FTS5: cuanto MENOR, mejor
    peso = bm * _PESO_CLASE.get(fila["clase"], 1.0) \
              * _PESO_SECCION.get(fila["seccion"], 0.8) \
              * _peso_largo(int(fila["palabras"] or 0))
    return Hallazgo(
        id=fila["id"], canonico=fila["canonico"], autor=fila["autor"],
        numero=str(fila["numero"] or ""), orden=int(fila["orden"] or 0),
        obra=fila["obra"] or "", titulo=fila["titulo"] or "",
        seccion=fila["seccion"] or "", clase=fila["clase"] or "cuerpo",
        hoja=fila["hoja"], impresa=fila["impresa"],
        nota=fila["nota"] or "", versos=fila["versos"] or "",
        texto=fila["texto"], bm=bm, peso=peso,
    )


# --------------------------------------------------------------------
# Consultas
# --------------------------------------------------------------------

_PALABRA = re.compile(r'"[^"]*"|\S+')

# Palabras vacías del castellano. Se quitan de las consultas SUELTAS
# (nunca de las frases entre comillas): "el alma es inmortal" pide de
# verdad "alma inmortal", y exigir además "el", "es" premiaba pasajes
# largos llenos de artículos en lugar del que habla del asunto.
_VACIAS = {
    "a", "al", "ante", "como", "con", "contra", "de", "del", "desde", "e", "el",
    "en", "entre", "es", "esa", "ese", "eso", "esta", "este", "esto",
    "ha", "han", "hasta", "la", "las", "le", "les", "lo", "los", "mas",
    "me", "mi", "mis", "ni", "no", "nos", "o", "para", "pero", "por",
    "que", "se", "segun", "sin", "sobre", "son", "su", "sus", "tras",
    "un", "una", "uno", "unos", "unas", "y", "ya",
    # Interrogativas: la gente pregunta, no teclea palabras clave
    # («¿qué tomo habla de los lacedemonios?»).
    "cual", "cuales", "cuando", "cuanto", "cuantos", "donde", "quien",
    "quienes",
}

# Palabras con las que se PREGUNTA a la aplicación, no con las que se
# escribe un tomo: en «¿qué tomo habla de los lacedemonios?», «tomo» y
# «habla» son el andamio de la pregunta. Solo se quitan si la consulta
# ES una pregunta — «libro» o «dice» dentro de una búsqueda normal son
# palabras del texto y hay que respetarlas.
_ANDAMIO = {
    "tomo", "tomos", "volumen", "volumenes", "libro", "libros", "obra",
    "obras", "habla", "hablan", "dice", "dicen", "trata", "tratan",
    "cuenta", "cuentan", "menciona", "mencionan", "aparece", "aparecen",
    "sale", "salen", "hay", "acerca", "respecto", "texto", "textos",
    "pasaje", "pasajes", "autor", "autores",
}

_PREGUNTA = re.compile(
    r"^\s*[¿?]|[?¿]\s*$|^\s*(que|qu[ée]|cu[áa]l|cu[áa]les|d[óo]nde|"
    r"qui[ée]n|qui[ée]nes|c[óo]mo|cu[áa]ndo|cu[áa]nto|cu[áa]ntos)\b",
    re.I,
)


def es_pregunta(consulta: str) -> bool:
    """¿Está preguntando en vez de teclear palabras clave?"""
    return bool(_PREGUNTA.search(consulta or ""))


def normaliza(texto: str) -> str:
    """Minúsculas y sin tildes, igual que el tokenizador del índice."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def _plano(texto: str) -> str:
    """
    Como `normaliza`, pero CARÁCTER A CARÁCTER: la salida mide lo mismo
    que la entrada.

    Hace falta para resaltar: la marca se calcula sobre el texto sin
    tildes y se pinta sobre el original, así que las posiciones tienen
    que coincidir. `normaliza` junta los espacios en blanco —y estos
    textos van llenos de saltos de verso—, con lo que ahí se descolocaba
    todo.
    """
    salida = []
    for car in texto:
        descompuesto = unicodedata.normalize("NFD", car.lower())
        base = "".join(
            c for c in descompuesto if unicodedata.category(c) != "Mn"
        )
        # Minúscula o descomposición que cambia de longitud (ß, ﬁ…):
        # se deja un carácter para no mover las posiciones.
        salida.append(base[0] if base else car)
    return "".join(salida)


# Un acento puede llegar de dos maneras: como una sola letra («ó») o
# como letra + signo suelto («o» + U+0301), que es lo que pega Windows
# al copiar de algunos PDF. Sin recomponerlo, el signo caía en la
# limpieza de puntuación y PARTÍA la palabra: «nómos» se buscaba como
# «no mos» y devolvía 1 tomo en vez de 50 (medido, 2026-08-08).
def _compone(texto: str) -> str:
    """Junta letra y acento; si no hay forma junta, quita el acento."""
    if not texto:
        return texto
    texto = unicodedata.normalize("NFC", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


# Un prefijo de una o dos letras no busca: barre. «a*» daba 121.320
# pasajes de los 172 tomos en 3,2 s — ni es un resultado ni es útil.
_MINIMO_PREFIJO = 3


def a_consulta_fts(consulta: str) -> str:
    """Lo que el usuario escribe, en sintaxis de FTS5 (Y implícita)."""
    return " ".join(piezas_fts(consulta))


def piezas_fts(consulta: str) -> list[str]:
    """
    Cada trozo de la consulta, ya escapado y por separado.

    Se devuelven sueltos para poder rehacer la búsqueda con OR cuando
    exigiéndolos todos no sale nada. No vale volver a partir la cadena
    por espacios: una frase entre comillas los lleva dentro.

    - Varias palabras = todas tienen que aparecer (Y implícita).
    - Entre comillas = frase exacta, en ese orden.
    - Un asterisco final = empieza por.

    Todo lo demás se ESCAPA: los signos de FTS5 (`*`, `:`, `^`, `-`,
    `NEAR`) dentro de un texto normal harían fallar la consulta con un
    error críptico en vez de buscar.
    """
    piezas: list[str] = []
    todas: list[str] = []
    fuera = _VACIAS | _ANDAMIO if es_pregunta(consulta) else _VACIAS
    consulta = _compone(consulta)
    for bruto in _PALABRA.findall(consulta or ""):
        prefijo = False
        frase = bruto.startswith('"')
        if frase:
            token = bruto.strip('"')
        else:
            if bruto.endswith("*") and len(bruto) > 1:
                prefijo = True
                bruto = bruto[:-1]
            token = bruto
        # Solo letras, cifras y espacios: lo demás no aporta al índice
        token = re.sub(r"[^\w\s]", " ", token, flags=re.UNICODE).strip()
        if not token:
            continue
        # Un prefijo demasiado corto barre el índice entero: se busca la
        # palabra tal cual, que al menos significa algo.
        if prefijo and len(token) < _MINIMO_PREFIJO:
            prefijo = False
        pieza = '"' + token.replace('"', '""') + '"'
        if prefijo:
            pieza += "*"
        todas.append(pieza)
        if not frase and not prefijo and normaliza(token) in fuera:
            continue
        piezas.append(pieza)
    # Si la consulta era SOLO palabras vacías ("de los"), se buscan tal
    # cual: mejor devolver algo pobre que nada. La ventana lo avisa, que
    # si no son 137.939 pasajes de los 172 tomos sin explicación.
    return piezas or todas


def solo_palabras_vacias(consulta: str) -> bool:
    """¿La consulta no tiene ni una palabra con contenido?"""
    piezas = piezas_fts(consulta)
    if not piezas:
        return False
    fuera = _VACIAS | _ANDAMIO if es_pregunta(consulta) else _VACIAS
    return all(
        normaliza(p.strip("*").strip('"')) in fuera for p in piezas
    )


_ROMANO = re.compile(r"^[ivxlcdm]+$", re.I)
_NO_NOMBRE = {
    "y ss", "ss", "id", "ibid", "cf", "vid", "op cit", "passim", "et al",
    "n", "nn", "pag", "pags", "vol", "vols", "s v",
}


def _es_nombre(nombre: str) -> bool:
    """
    Filtra lo que el OCR cuela como entrada del índice de nombres.

    Medido en el corpus: aparecen "y ss", romanos sueltos partidos por
    el reconocimiento ("XV I") y restos de una letra. No son nombres y
    darían respuestas absurdas al preguntar por un personaje.
    """
    limpio = normaliza(nombre)
    if len(limpio) < 3 or limpio in _NO_NOMBRE:
        return False
    if _ROMANO.match(limpio.replace(" ", "")):
        return False
    return any(c.isalpha() for c in limpio)


# --------------------------------------------------------------------
# Instancia compartida
# --------------------------------------------------------------------

_INDICE: Optional[Indice] = None
_INDICE_LOCK = threading.Lock()


def indice_compartido() -> Indice:
    """
    El índice de la aplicación: UNO solo, como `shared_price_fetcher`.

    Abrir una conexión por diálogo dejaría archivos WAL sueltos y dos
    indexados a la vez sobre el mismo archivo.
    """
    global _INDICE
    with _INDICE_LOCK:
        if _INDICE is None:
            _INDICE = Indice()
        return _INDICE


def cerrar_indice() -> None:
    """Cierra el índice compartido (al salir de la aplicación)."""
    global _INDICE
    with _INDICE_LOCK:
        if _INDICE is not None:
            _INDICE.close()
            _INDICE = None


def resaltar(texto: str, consulta: str, ancho: int = 320) -> str:
    """
    Recorta el pasaje alrededor de lo buscado.

    No se usa `snippet()` de FTS5 porque el pasaje ya está en la tabla y
    así el recorte respeta los saltos de verso.
    """
    palabras = [
        p for p in normaliza(consulta).split() if len(p) > 2
    ]
    plano = _plano(texto)
    donde = -1
    for p in palabras:
        donde = plano.find(p)
        if donde >= 0:
            break
    if donde < 0:
        return texto[:ancho] + ("…" if len(texto) > ancho else "")
    inicio = max(0, donde - ancho // 3)
    fin = min(len(texto), inicio + ancho)
    recorte = texto[inicio:fin]
    if inicio > 0:
        recorte = "…" + recorte.lstrip()
    if fin < len(texto):
        recorte = recorte.rstrip() + "…"
    return recorte


def marcar(texto: str, consulta: str) -> list[tuple[str, bool]]:
    """
    Parte el texto en trozos (fragmento, ¿es coincidencia?).

    Lo usa la ventana para pintar en oro lo que se buscaba. Trabaja
    sobre el texto ORIGINAL con posiciones del normalizado: así la
    marca cae sobre la palabra con su tilde y sus mayúsculas.
    """
    palabras = sorted(
        {p for p in normaliza(consulta).split() if len(p) > 2},
        key=len, reverse=True,
    )
    if not palabras or not texto:
        return [(texto, False)]
    plano = _plano(texto)          # misma longitud que el original
    marcas = [False] * len(texto)
    for p in palabras:
        desde = 0
        while True:
            donde = plano.find(p, desde)
            if donde < 0:
                break
            for i in range(donde, donde + len(p)):
                marcas[i] = True
            desde = donde + len(p)
    trozos: list[tuple[str, bool]] = []
    actual, estado = [], marcas[0] if marcas else False
    for car, marca in zip(texto, marcas):
        if marca != estado:
            trozos.append(("".join(actual), estado))
            actual, estado = [], marca
        actual.append(car)
    if actual:
        trozos.append(("".join(actual), estado))
    return trozos


# ----------------------------------------------------------------------
# Titular un pasaje sin IA
# ----------------------------------------------------------------------
# No hay ninguna IA en esta aplicación, así que el título y el resumen
# del pasaje del día NO se inventan: se sacan de lo que el propio tomo
# trae. Por orden de fiabilidad:
#   1. Los NOMBRES del índice del traductor que salen en el pasaje. Es
#      lo mejor que hay: quien editó el tomo ya decidió qué merecía
#      entrada propia.
#   2. El rótulo de la sección, cuando dice algo («Sobre la educación
#      de los niños») y no es una etiqueta vacía («Libro I», «III»).
#   3. El título canónico del tomo, que siempre está.
# Un resumen redactado exigiría un modelo de lenguaje; lo que se hace
# es ENTRESACAR la frase más significativa, que es honesto y sirve.

_ROTULO_VACIO = re.compile(
    r"^(?:libro|canto|cap[íi]tulo|parte|secci[óo]n|tomo|vol(?:umen)?\.?|"
    r"discurso|carta|fragmento)?\s*[ivxlcdm\d\s.\-–:,]*$",
    re.IGNORECASE,
)
_FIN_DE_ORACION = re.compile(r"(?<=[.!?…»])\s+")


def _rotulo_sirve(texto: str) -> bool:
    """¿El rótulo de la sección dice algo o es una etiqueta vacía?"""
    limpio = (texto or "").strip()
    return len(limpio) >= 6 and not _ROTULO_VACIO.match(limpio)


def titulo_de_pasaje(
    texto: str, obra: str, seccion: str, nombres: list[str], canonico: str,
) -> str:
    """
    Un título corto para el pasaje, con lo que el tomo ya sabe de él.

    Nunca se inventa nada: o son nombres de su propio índice, o el
    rótulo que puso el editor, o el título del tomo.
    """
    if nombres:
        cuantos = nombres[:2] if len(nombres) > 1 else nombres[:1]
        cabeza = " y ".join(n.capitalize() if n.isupper() else n
                            for n in cuantos)
        if _rotulo_sirve(seccion) or _rotulo_sirve(obra):
            marco = seccion if _rotulo_sirve(seccion) else obra
            return f"{cabeza} · {marco.strip().rstrip('.')}"
        return cabeza
    for candidato in (seccion, obra):
        if _rotulo_sirve(candidato):
            return candidato.strip().rstrip(".")
    return canonico


def descripcion_de_pasaje(texto: str, nombres: list[str]) -> str:
    """
    De qué va, en una frase ENTRESACADA del propio pasaje.

    Se elige la oración con más peso —la que más nombres del índice
    trae y más larga— porque redactar un resumen exigiría una IA y aquí
    no hay ninguna. Es una cita, no un invento.
    """
    llano = " ".join((texto or "").split())
    if not llano:
        return ""
    oraciones = [o.strip() for o in _FIN_DE_ORACION.split(llano) if o.strip()]
    if not oraciones:
        return llano[:220]
    claves = [normaliza(n) for n in nombres]

    def peso(oracion: str) -> tuple:
        plano = normaliza(oracion)
        return (
            sum(1 for c in claves if c in plano),
            min(len(oracion), 240),          # ni la más corta ni una parrafada
        )

    mejor = max(oraciones, key=peso)
    if len(mejor) > 260:
        corte = mejor.rfind(" ", 0, 250)
        mejor = mejor[:corte if corte > 0 else 250].rstrip(",;: ") + "…"
    return mejor
