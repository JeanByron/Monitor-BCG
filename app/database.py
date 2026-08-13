"""
database.py
===========
Persistencia en SQLite.

Dos responsabilidades:

1. Historial de alertas (tabla `history`): cada bajada de precio
   detectada, con su estado ("notificado", "ignorado" o "lote").
2. Control de duplicados (tabla `processed_uids`): UIDs IMAP ya
   procesados para no notificar dos veces el mismo correo, incluso
   entre reinicios del programa.
3. Historial de precios por libro (tabla `price_history`): cada precio
   visto de cada título, para la gráfica de evolución y para detectar
   cuándo un tomo vuelve a subir.
4. Umbrales por libro (tabla `thresholds`): patrones de título con su
   propio porcentaje mínimo de descuento (prevalecen sobre el global).
5. Metadatos (tabla `meta`): pares clave/valor persistentes (p. ej.
   fecha del último resumen diario enviado).

El acceso es seguro entre hilos gracias a un `threading.Lock` y a
`check_same_thread=False`, ya que el monitor IMAP corre en un hilo
distinto al de la interfaz.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import app_dir

logger = logging.getLogger(__name__)

DB_PATH: Path = app_dir() / "tc_monitor.db"

# Rotación del historial de alertas: al superar HISTORY_MAX filas se
# eliminan las HISTORY_PRUNE más antiguas. La tabla price_history está
# EXENTA a propósito: el histórico de precios se conserva íntegro.
HISTORY_MAX = 1000
HISTORY_PRUNE = 500


class Database:
    """Capa de acceso a la base de datos SQLite."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("Base de datos abierta: %s", path)

    # ------------------------------------------------------------------
    # Esquema
    # ------------------------------------------------------------------
    def _create_tables(self) -> None:
        """Crea las tablas si no existen (idempotente)."""
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha       TEXT    NOT NULL,   -- ISO 8601
                    titulo      TEXT    NOT NULL,
                    precio_ant  REAL,               -- precio anterior (€)
                    precio_new  REAL,               -- precio nuevo (€)
                    descuento   REAL,               -- porcentaje de descuento
                    enlace      TEXT,
                    estado      TEXT    NOT NULL,   -- 'notificado' | 'ignorado'
                    mensaje_id  TEXT                -- Message-ID del correo
                );

                CREATE TABLE IF NOT EXISTS processed_uids (
                    folder  TEXT NOT NULL,          -- carpeta IMAP
                    uid     TEXT NOT NULL,          -- UID IMAP del correo
                    fecha   TEXT NOT NULL,          -- cuándo se procesó
                    PRIMARY KEY (folder, uid)
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    clave      TEXT NOT NULL,       -- título normalizado (agrupación)
                    titulo     TEXT NOT NULL,       -- título tal cual se vio
                    fecha      TEXT NOT NULL,       -- ISO 8601
                    precio     REAL NOT NULL,       -- precio visto (€)
                    url        TEXT,                -- publicación de la que salió
                    mensaje_id TEXT                 -- correo que lo generó
                );
                CREATE INDEX IF NOT EXISTS idx_price_history_clave
                    ON price_history (clave, id);

                CREATE TABLE IF NOT EXISTS thresholds (
                    patron      TEXT PRIMARY KEY,   -- texto a buscar en el título
                    porcentaje  REAL NOT NULL       -- descuento mínimo para ese patrón
                );

                CREATE TABLE IF NOT EXISTS meta (
                    clave  TEXT PRIMARY KEY,
                    valor  TEXT
                );

                CREATE TABLE IF NOT EXISTS tomo_links (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    orden           INTEGER NOT NULL,  -- nº de colección del tomo
                    url             TEXT NOT NULL,
                    ultimo_precio   REAL,              -- último precio extraído (€)
                    ultima_revision TEXT               -- ISO 8601
                );

                CREATE TABLE IF NOT EXISTS lotes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo          TEXT NOT NULL,     -- nombre visible del lote
                    url             TEXT NOT NULL,     -- publicación vigilada
                    ultimo_precio   REAL,              -- último precio extraído (€)
                    ultima_revision TEXT               -- ISO 8601
                );

                CREATE TABLE IF NOT EXISTS tomos (
                    orden    INTEGER,            -- número de colección (para ordenar/cruzar)
                    numero   TEXT NOT NULL,      -- tal cual en el Excel ("1[2]")
                    autor    TEXT NOT NULL,
                    obras    TEXT NOT NULL,
                    paginas  INTEGER,
                    notas    TEXT,
                    poseido  INTEGER NOT NULL DEFAULT 0, -- 1 = obtenido por el usuario
                    deseado  INTEGER NOT NULL DEFAULT 0, -- 1 = en su lista de deseados
                    precio_objetivo REAL,                -- avisar si baja de aquí (€)
                    descripcion TEXT,      -- de qué trata el tomo (generada por IA)
                    temas       TEXT,      -- palabras clave, separadas por " · "
                    desc_modelo TEXT,      -- modelo que la generó
                    desc_fecha  TEXT       -- ISO 8601
                );
                """
            )
            # Migración: bases creadas antes de existir estas columnas
            for tabla, columna, ddl in (
                ("tomos", "poseido", "INTEGER NOT NULL DEFAULT 0"),
                ("tomos", "deseado", "INTEGER NOT NULL DEFAULT 0"),
                ("tomos", "precio_objetivo", "REAL"),
                ("price_history", "url", "TEXT"),
                # Message-ID del correo que generó el dato: permite
                # reprocesar un correo sin duplicar nada (2026-07-26)
                ("history", "mensaje_id", "TEXT"),
                ("price_history", "mensaje_id", "TEXT"),
                # Descripción del contenido de cada tomo (2026-07-28)
                ("tomos", "descripcion", "TEXT"),
                ("tomos", "temas", "TEXT"),
                ("tomos", "desc_modelo", "TEXT"),
                ("tomos", "desc_fecha", "TEXT"),
                # Identidad REAL del tomo en sus publicaciones vigiladas:
                # el orden lo comparten tres pares de la colección y los
                # enlaces de uno salían en la ficha del otro (2026-07-29)
                ("tomo_links", "numero", "TEXT"),
                # Publicación ya VENDIDA: se le retira el precio, que
                # ya no se puede pagar (2026-07-31)
                ("tomo_links", "vendido", "INTEGER NOT NULL DEFAULT 0"),
                ("lotes", "vendido", "INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    self._conn.execute(
                        f"ALTER TABLE {tabla} ADD COLUMN {columna} {ddl}"
                    )
                except sqlite3.OperationalError:
                    pass  # la columna ya existe

            # Índices de las columnas migradas: DESPUÉS del ALTER TABLE
            # (en una BD antigua la columna aún no existía y el índice
            # dentro del script de creación reventaba el arranque).
            self._conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_history_mensaje
                    ON history (mensaje_id);
                CREATE INDEX IF NOT EXISTS idx_price_history_mensaje
                    ON price_history (mensaje_id);
                CREATE INDEX IF NOT EXISTS idx_tomo_links_numero
                    ON tomo_links (numero);
                """
            )

            # Enlaces guardados cuando la clave era el orden: se les
            # pone el número del tomo (el primero, si dos lo comparten).
            self._conn.execute(
                "UPDATE tomo_links SET numero = ("
                "  SELECT numero FROM tomos WHERE tomos.orden = "
                "tomo_links.orden ORDER BY tomos.rowid LIMIT 1"
                ") WHERE numero IS NULL"
            )

            # Siembra ÚNICA (2026-07-26): los lotes ya detectados por el
            # monitor (historial, estado='lote') pasan a su serie de
            # precios `lote::` para que el botón Lotes nazca con datos.
            done = self._conn.execute(
                "SELECT 1 FROM meta WHERE clave = 'lotes_seed_v1'"
            ).fetchone()
            if done is None:
                filas = self._conn.execute(
                    "SELECT fecha, titulo, precio_new, enlace FROM history "
                    "WHERE estado = 'lote' AND precio_new IS NOT NULL "
                    "ORDER BY id"
                ).fetchall()
                for f in filas:
                    self._conn.execute(
                        "INSERT INTO price_history "
                        "(clave, titulo, fecha, precio, url) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            self.lot_key(f["titulo"]), f["titulo"],
                            f["fecha"], f["precio_new"], f["enlace"],
                        ),
                    )
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (clave, valor) "
                    "VALUES ('lotes_seed_v1', ?)",
                    (str(len(filas)),),
                )
                if filas:
                    logger.info(
                        "Series de lotes sembradas desde el historial: "
                        "%d punto(s).", len(filas),
                    )

    # ------------------------------------------------------------------
    # Control de duplicados (UIDs IMAP)
    # ------------------------------------------------------------------
    def is_uid_processed(self, folder: str, uid: str) -> bool:
        """Indica si un UID de una carpeta ya fue procesado antes."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM processed_uids WHERE folder = ? AND uid = ?",
                (folder, uid),
            )
            return cur.fetchone() is not None

    def mark_uid_processed(self, folder: str, uid: str) -> None:
        """Registra un UID como procesado (nunca se volverá a notificar)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_uids (folder, uid, fecha) VALUES (?, ?, ?)",
                (folder, uid, datetime.now().isoformat(timespec="seconds")),
            )

    def last_processed_uid(self, folder: str) -> Optional[int]:
        """
        Devuelve el mayor UID numérico procesado en la carpeta,
        útil para pedir al servidor solo correos posteriores.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT uid FROM processed_uids WHERE folder = ?", (folder,)
            )
            uids = [int(r["uid"]) for r in cur.fetchall() if str(r["uid"]).isdigit()]
        return max(uids) if uids else None

    # ------------------------------------------------------------------
    # Control de duplicados POR CORREO (Message-ID)
    #
    # El UID sirve para no reprocesar; esto sirve para no reinsertar. Un
    # correo puede reprocesarse a propósito (el usuario lo marca no
    # leído, volcado masivo del backlog) y entonces cada dato que genera
    # —fila de historial, punto de precio, punto de lote, publicación
    # vigilada— debe entrar UNA sola vez.
    # ------------------------------------------------------------------
    def email_inserted_status(self, mensaje_id: str) -> dict[str, int]:
        """
        Qué ha dejado ya en la base de datos un correo concreto:
        `{historial, precios, lotes, enlaces, lotes_vigilados}`.

        `enlaces`/`lotes_vigilados` se cuentan por la URL del anuncio
        (esas tablas no guardan el Message-ID: la misma publicación
        llega en varios correos y debe seguir siendo UNA fila).
        """
        vacio = {
            "historial": 0, "precios": 0, "lotes": 0,
            "enlaces": 0, "lotes_vigilados": 0,
        }
        if not mensaje_id:
            return vacio
        from app.utils import clean_ad_url

        with self._lock:
            vacio["historial"] = self._conn.execute(
                "SELECT COUNT(*) n FROM history WHERE mensaje_id = ?",
                (mensaje_id,),
            ).fetchone()["n"]
            vacio["precios"] = self._conn.execute(
                "SELECT COUNT(*) n FROM price_history "
                "WHERE mensaje_id = ? AND clave NOT LIKE 'lote::%'",
                (mensaje_id,),
            ).fetchone()["n"]
            vacio["lotes"] = self._conn.execute(
                "SELECT COUNT(*) n FROM price_history "
                "WHERE mensaje_id = ? AND clave LIKE 'lote::%'",
                (mensaje_id,),
            ).fetchone()["n"]
            urls = [
                r["url"] for r in self._conn.execute(
                    "SELECT DISTINCT url FROM price_history "
                    "WHERE mensaje_id = ? AND url IS NOT NULL",
                    (mensaje_id,),
                ).fetchall()
            ]
            enlaces_tomo = [
                clean_ad_url(r["url"]) for r in self._conn.execute(
                    "SELECT url FROM tomo_links"
                ).fetchall()
            ]
            enlaces_lote = [
                clean_ad_url(r["url"]) for r in self._conn.execute(
                    "SELECT url FROM lotes"
                ).fetchall()
            ]
        limpias = {clean_ad_url(u) for u in urls if u}
        vacio["enlaces"] = len(limpias & set(enlaces_tomo))
        vacio["lotes_vigilados"] = len(limpias & set(enlaces_lote))
        return vacio

    def email_already_inserted(self, mensaje_id: str) -> bool:
        """¿Este correo ya dejó su fila de historial? (ya fue analizado)."""
        return self.email_inserted_status(mensaje_id)["historial"] > 0

    # ------------------------------------------------------------------
    # Historial de alertas
    # ------------------------------------------------------------------
    def add_history(
        self,
        titulo: str,
        precio_ant: Optional[float],
        precio_new: Optional[float],
        descuento: Optional[float],
        enlace: Optional[str],
        estado: str,
        mensaje_id: Optional[str] = None,
    ) -> bool:
        """
        Inserta una entrada en el historial. Devuelve True si insertó.

        IDEMPOTENTE por correo: si ese `mensaje_id` (Message-ID del
        correo) ya tiene una fila con el MISMO estado, no se duplica —
        reprocesar un correo (marcarlo no leído, volcado masivo) no
        puede llenar el historial de copias.
        """
        with self._lock, self._conn:
            if mensaje_id:
                ya = self._conn.execute(
                    "SELECT 1 FROM history WHERE mensaje_id = ? AND estado = ?",
                    (mensaje_id, estado),
                ).fetchone()
                if ya is not None:
                    return False
            self._conn.execute(
                """
                INSERT INTO history
                    (fecha, titulo, precio_ant, precio_new, descuento,
                     enlace, estado, mensaje_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    titulo,
                    precio_ant,
                    precio_new,
                    descuento,
                    enlace,
                    estado,
                    mensaje_id,
                ),
            )
            # Rotación: nunca crecer sin límite (price_history NO rota)
            n = self._conn.execute(
                "SELECT COUNT(*) AS n FROM history"
            ).fetchone()["n"]
            if n > HISTORY_MAX:
                self._conn.execute(
                    "DELETE FROM history WHERE id IN ("
                    "  SELECT id FROM history ORDER BY id LIMIT ?"
                    ")",
                    (HISTORY_PRUNE,),
                )
                logger.info(
                    "Historial rotado: %d filas antiguas eliminadas "
                    "(quedaban %d).",
                    HISTORY_PRUNE, n,
                )
        return True

    def get_history(
        self, limit: int = 200, estado: Optional[str] = None
    ) -> list[sqlite3.Row]:
        """
        Últimas `limit` entradas del historial (más recientes primero).

        `estado` (opcional) filtra por 'notificado', 'ignorado' o 'lote'
        — p. ej. la lista de ofertas que sí cumplieron las condiciones.
        """
        with self._lock:
            if estado is None:
                cur = self._conn.execute(
                    "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM history WHERE estado = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (estado, limit),
                )
            return cur.fetchall()

    # ------------------------------------------------------------------
    # Historial de precios por libro
    # ------------------------------------------------------------------
    @staticmethod
    def _title_key(titulo: str) -> str:
        """Clave de agrupación: título normalizado (minúsculas, sin tildes)."""
        from app.utils import normalize  # import local para evitar ciclos

        return " ".join(normalize(titulo).split())

    def last_price(self, titulo: str) -> Optional[float]:
        """Último precio registrado para un título (None si no hay)."""
        key = self._title_key(titulo)
        with self._lock:
            cur = self._conn.execute(
                "SELECT precio FROM price_history WHERE clave = ? "
                "ORDER BY id DESC LIMIT 1",
                (key,),
            )
            row = cur.fetchone()
        return row["precio"] if row else None

    def _punto_ya_registrado(
        self,
        clave: str,
        precio: float,
        url: Optional[str],
        mensaje_id: str,
    ) -> bool:
        """
        ¿La serie ya tiene el punto que aporta este correo?

        Dos casos: (1) ya hay un punto con ese Message-ID; (2) hay un
        punto IDÉNTICO (mismo precio y publicación) de ANTES de existir
        la columna — se le pone el Message-ID (adopción) para que el
        volcado del histórico no duplique las gráficas ya guardadas.
        Debe llamarse dentro del lock.
        """
        ya = self._conn.execute(
            "SELECT 1 FROM price_history WHERE clave = ? AND mensaje_id = ?",
            (clave, mensaje_id),
        ).fetchone()
        if ya is not None:
            return True
        huerfano = self._conn.execute(
            "SELECT id FROM price_history "
            "WHERE clave = ? AND mensaje_id IS NULL AND precio = ? "
            "AND IFNULL(url, '') = IFNULL(?, '') ORDER BY id LIMIT 1",
            (clave, precio, url),
        ).fetchone()
        if huerfano is not None:
            self._conn.execute(
                "UPDATE price_history SET mensaje_id = ? WHERE id = ?",
                (mensaje_id, huerfano["id"]),
            )
            return True
        return False

    def add_price_point(
        self,
        titulo: str,
        precio: float,
        url: Optional[str] = None,
        mensaje_id: Optional[str] = None,
    ) -> bool:
        """
        Registra un precio visto de un título (para la gráfica), con la
        URL de la publicación de la que salió (clic en el punto → abre).
        Devuelve True si insertó.

        IDEMPOTENTE por correo: un `mensaje_id` solo aporta UN punto a
        cada serie; reprocesar el correo no duplica la gráfica.
        """
        key = self._title_key(titulo)
        with self._lock, self._conn:
            if mensaje_id and self._punto_ya_registrado(
                key, precio, url, mensaje_id
            ):
                return False
            self._conn.execute(
                "INSERT INTO price_history "
                "(clave, titulo, fecha, precio, url, mensaje_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    titulo,
                    datetime.now().isoformat(timespec="seconds"),
                    precio,
                    url,
                    mensaje_id,
                ),
            )
        return True

    def price_history_titles(self) -> list[tuple[str, str, int]]:
        """
        Títulos con historial: `(clave, último título visto, nº de puntos)`,
        ordenados por actividad reciente. Excluye las series de LOTES
        (prefijo `lote::`): esas viven en el botón Lotes.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT clave, titulo, n FROM (
                    SELECT clave, titulo, COUNT(*) OVER (PARTITION BY clave) AS n,
                           ROW_NUMBER() OVER (PARTITION BY clave ORDER BY id DESC) AS rn,
                           id
                    FROM price_history
                    WHERE clave NOT LIKE 'lote::%'
                ) WHERE rn = 1 ORDER BY id DESC
                """
            )
            return [(r["clave"], r["titulo"], r["n"]) for r in cur.fetchall()]

    def price_history_for(self, clave: str) -> list[tuple[str, float, Optional[str]]]:
        """Puntos `(fecha, precio, url)` de un título, en orden cronológico."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT fecha, precio, url FROM price_history WHERE clave = ? "
                "ORDER BY id",
                (clave,),
            )
            return [(r["fecha"], r["precio"], r["url"]) for r in cur.fetchall()]

    def delete_price_points(self, clave: str, url: str) -> int:
        """
        Borra de una serie los puntos que salieron de UNA publicación.

        Se usa cuando el anuncio aparece VENDIDO: ese precio ya no se
        puede pagar y falsearía la gráfica del tomo o del lote. El resto
        de la serie (otros vendedores) queda intacto.
        """
        from app.utils import clean_ad_url

        limpia = clean_ad_url(url)
        if not clave or not limpia:
            return 0
        with self._lock, self._conn:
            filas = self._conn.execute(
                "SELECT id, url FROM price_history WHERE clave = ?", (clave,)
            ).fetchall()
            ids = [
                f["id"] for f in filas if clean_ad_url(f["url"] or "") == limpia
            ]
            for punto in ids:
                self._conn.execute(
                    "DELETE FROM price_history WHERE id = ?", (punto,)
                )
        if ids:
            logger.info(
                "Publicación vendida: %d punto(s) retirados de %s",
                len(ids), clave,
            )
        return len(ids)

    def mark_link_sold(self, tabla: str, fila_id: int) -> None:
        """Marca una publicación vigilada como vendida y le quita el precio."""
        if tabla not in ("tomo_links", "lotes"):
            raise ValueError(f"Tabla de publicaciones desconocida: {tabla}")
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE {tabla} SET vendido = 1, ultimo_precio = NULL, "
                "ultima_revision = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), fila_id),
            )

    def price_history_stats(self, lotes: bool = False) -> dict[str, dict]:
        """
        Resumen de cada serie: `{clave: {n, primero, ultimo, minimo,
        maximo, fecha}}`, en UNA consulta.

        Lo usan los filtros de las ventanas Precios y Lotes: sin esto
        habría que pedir la serie entera de cada título solo para saber
        si ha bajado.
        """
        criterio = "LIKE" if lotes else "NOT LIKE"
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT clave,
                       COUNT(*)                                   AS n,
                       MIN(precio)                                AS minimo,
                       MAX(precio)                                AS maximo,
                       MAX(fecha)                                 AS fecha,
                       (SELECT precio FROM price_history p2
                         WHERE p2.clave = p.clave
                         ORDER BY p2.id ASC  LIMIT 1)             AS primero,
                       (SELECT precio FROM price_history p3
                         WHERE p3.clave = p.clave
                         ORDER BY p3.id DESC LIMIT 1)             AS ultimo
                FROM price_history p
                WHERE clave {criterio} 'lote::%'
                GROUP BY clave
                """
            )
            return {r["clave"]: dict(r) for r in cur.fetchall()}

    def clear_price_history(self) -> int:
        """Vacía la serie de precios; devuelve cuántos puntos borró."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM price_history")
            deleted = cur.rowcount
        logger.info("Historial de precios limpiado: %d punto(s).", deleted)
        return deleted

    # ------------------------------------------------------------------
    # Lotes: serie de precios propia + publicaciones vigiladas
    #
    # Las series de lotes comparten tabla con price_history pero viven
    # en su propio espacio de claves (prefijo `lote::`): así el botón
    # Precios (solo tomos identificados) y el botón Lotes no se mezclan
    # jamás, y los puntos conservan la URL clicable de las gráficas.
    # ------------------------------------------------------------------
    LOT_PREFIX = "lote::"

    @classmethod
    def lot_key(cls, titulo: str) -> str:
        """Clave de serie de un lote (prefijo + título normalizado)."""
        return cls.LOT_PREFIX + cls._title_key(titulo)

    def last_lot_price(self, titulo: str) -> Optional[float]:
        """Último precio registrado de la serie de un lote (None si no hay)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT precio FROM price_history WHERE clave = ? "
                "ORDER BY id DESC LIMIT 1",
                (self.lot_key(titulo),),
            )
            row = cur.fetchone()
        return row["precio"] if row else None

    def add_lot_price_point(
        self,
        titulo: str,
        precio: float,
        url: Optional[str] = None,
        mensaje_id: Optional[str] = None,
    ) -> bool:
        """
        Registra un precio visto de un lote (para su gráfica).
        Idempotente por `mensaje_id`, igual que las series de tomos.
        """
        clave = self.lot_key(titulo)
        with self._lock, self._conn:
            if mensaje_id and self._punto_ya_registrado(
                clave, precio, url, mensaje_id
            ):
                return False
            self._conn.execute(
                "INSERT INTO price_history "
                "(clave, titulo, fecha, precio, url, mensaje_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    clave,
                    titulo,
                    datetime.now().isoformat(timespec="seconds"),
                    precio,
                    url,
                    mensaje_id,
                ),
            )
        return True

    def lot_price_titles(self) -> list[tuple[str, str, int]]:
        """Series de LOTES: `(clave, último título visto, nº de puntos)`."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT clave, titulo, n FROM (
                    SELECT clave, titulo, COUNT(*) OVER (PARTITION BY clave) AS n,
                           ROW_NUMBER() OVER (PARTITION BY clave ORDER BY id DESC) AS rn,
                           id
                    FROM price_history
                    WHERE clave LIKE 'lote::%'
                ) WHERE rn = 1 ORDER BY id DESC
                """
            )
            return [(r["clave"], r["titulo"], r["n"]) for r in cur.fetchall()]

    def add_lote(self, titulo: str, url: str) -> int:
        """Añade un lote vigilado (URL de su publicación); devuelve su id."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO lotes (titulo, url) VALUES (?, ?)",
                (titulo.strip(), url.strip()),
            )
            return cur.lastrowid

    def add_lote_if_new(
        self, titulo: str, url: str, precio: Optional[float] = None
    ) -> Optional[int]:
        """
        Vigila un lote si su publicación no estaba ya (URL sin campaña).
        Lo usa el monitor con los lotes detectados en los correos.
        """
        from app.utils import clean_ad_url

        limpia = clean_ad_url(url)
        if not limpia:
            return None
        for r in self.get_lotes():
            if clean_ad_url(r["url"]) == limpia:
                if precio is not None and r["ultimo_precio"] != precio:
                    self.update_lote_price(r["id"], precio)
                return None
        lote_id = self.add_lote(titulo, limpia)
        if precio is not None:
            self.update_lote_price(lote_id, precio)
        logger.info("Lote vigilado añadido: %s (%s)", titulo[:60], limpia)
        return lote_id

    def get_lotes(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM lotes ORDER BY id")
            return cur.fetchall()

    def remove_lote(self, lote_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM lotes WHERE id = ?", (lote_id,))

    def update_lote_price(self, lote_id: int, precio: Optional[float]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE lotes SET ultimo_precio = ?, ultima_revision = ? "
                "WHERE id = ?",
                (
                    precio,
                    datetime.now().isoformat(timespec="seconds"),
                    lote_id,
                ),
            )

    def rename_lot(self, old_clave: str, new_titulo: str) -> str:
        """
        Renombra un lote (editor manual de títulos): su serie de precios
        y sus publicaciones vigiladas pasan al título nuevo. Devuelve la
        clave nueva. Si ya existía una serie con ese título, se fusionan.
        """
        if not old_clave.startswith(self.LOT_PREFIX):
            raise ValueError(f"Clave de lote no válida: {old_clave!r}")
        new_clave = self.lot_key(new_titulo)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE price_history SET clave = ?, titulo = ? "
                "WHERE clave = ?",
                (new_clave, new_titulo, old_clave),
            )
            filas = self._conn.execute(
                "SELECT id, titulo FROM lotes"
            ).fetchall()
            for f in filas:
                if self.lot_key(f["titulo"]) == old_clave:
                    self._conn.execute(
                        "UPDATE lotes SET titulo = ? WHERE id = ?",
                        (new_titulo, f["id"]),
                    )
        return new_clave

    def delete_lot_series(self, clave: str) -> int:
        """
        Borra la serie de precios de UN lote (acción explícita del botón
        Quitar de la ventana Lotes; la regla de conservar el histórico
        íntegro protege las series de TOMOS, no un lote que el usuario
        elimina a mano). Solo acepta claves del espacio `lote::`.
        """
        if not clave.startswith(self.LOT_PREFIX):
            raise ValueError(f"Clave de lote no válida: {clave!r}")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM price_history WHERE clave = ?", (clave,)
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Umbrales por libro
    # ------------------------------------------------------------------
    def get_thresholds(self) -> list[tuple[str, float]]:
        """Todos los umbrales por patrón, ordenados alfabéticamente."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT patron, porcentaje FROM thresholds ORDER BY patron"
            )
            return [(r["patron"], r["porcentaje"]) for r in cur.fetchall()]

    def set_thresholds(self, rows: list[tuple[str, float]]) -> None:
        """Sustituye la tabla completa de umbrales por `rows`."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM thresholds")
            self._conn.executemany(
                "INSERT OR REPLACE INTO thresholds (patron, porcentaje) VALUES (?, ?)",
                [(p.strip(), float(pct)) for p, pct in rows if p.strip()],
            )

    def threshold_for(self, titulo: str) -> Optional[float]:
        """
        Umbral específico para un título, o None si ninguno aplica.

        Un patrón aplica si aparece (normalizado) dentro del título;
        si varios aplican gana el MÁS LARGO (el más específico).
        """
        from app.utils import normalize

        title_n = normalize(titulo)
        best: Optional[tuple[int, float]] = None
        for patron, pct in self.get_thresholds():
            pat_n = normalize(patron)
            if pat_n and pat_n in title_n:
                if best is None or len(pat_n) > best[0]:
                    best = (len(pat_n), pct)
        return best[1] if best else None

    # ------------------------------------------------------------------
    # Metadatos y resumen diario
    # ------------------------------------------------------------------
    def get_meta(self, clave: str) -> Optional[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT valor FROM meta WHERE clave = ?", (clave,)
            )
            row = cur.fetchone()
        return row["valor"] if row else None

    def set_meta(self, clave: str, valor: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (clave, valor) VALUES (?, ?)",
                (clave, valor),
            )

    def summary_for_day(self, day_iso: str) -> dict:
        """
        Resumen de actividad de un día (`day_iso` = 'YYYY-MM-DD').

        Devuelve totales por estado y la mejor alerta notificada del día.
        """
        like = day_iso + "%"
        with self._lock:
            cur = self._conn.execute(
                "SELECT estado, COUNT(*) AS n FROM history "
                "WHERE fecha LIKE ? GROUP BY estado",
                (like,),
            )
            counts = {r["estado"]: r["n"] for r in cur.fetchall()}
            cur = self._conn.execute(
                "SELECT titulo, descuento FROM history "
                "WHERE fecha LIKE ? AND estado = 'notificado' "
                "AND descuento IS NOT NULL ORDER BY descuento DESC LIMIT 1",
                (like,),
            )
            best = cur.fetchone()
        return {
            "notificados": counts.get("notificado", 0),
            "ignorados": counts.get("ignorado", 0),
            "lotes": counts.get("lote", 0),
            "mejor_titulo": best["titulo"] if best else None,
            "mejor_descuento": best["descuento"] if best else None,
        }

    # ------------------------------------------------------------------
    # Colección BCG (tomos importados desde BDtomos/titulosBCG.xlsx)
    # ------------------------------------------------------------------
    def replace_tomos(self, rows: list[tuple]) -> int:
        """
        Sustituye la colección completa por `rows`:
        `(orden, numero, autor, obras, paginas, notas)`. Devuelve cuántos.

        Los etiquetados ("obtenido", "deseado"), los PRECIOS OBJETIVO y
        las DESCRIPCIONES sobreviven al reimporte: se capturan por
        NÚMERO del tomo y se vuelven a aplicar. Por número y no por
        orden porque el orden NO es único: tres pares de la colección
        lo comparten (200 Aristóteles/Museo, 250 Plinio/Basilio,
        415 Estrabón/Ovidio) y las marcas de uno saltaban al otro
        (2026-07-29). Las descripciones
        cuestan dinero y tiempo de generar: perderlas al reimportar el
        Excel sería el peor efecto colateral posible.
        """
        with self._lock, self._conn:
            marked: dict[str, set] = {}
            for columna in ("poseido", "deseado"):
                cur = self._conn.execute(
                    f"SELECT numero FROM tomos WHERE {columna} = 1"
                )
                marked[columna] = {r["numero"] for r in cur.fetchall()}
            cur = self._conn.execute(
                "SELECT numero, precio_objetivo FROM tomos "
                "WHERE precio_objetivo IS NOT NULL"
            )
            targets = {r["numero"]: r["precio_objetivo"] for r in cur.fetchall()}
            cur = self._conn.execute(
                "SELECT numero, descripcion, temas, desc_modelo, desc_fecha "
                "FROM tomos WHERE descripcion IS NOT NULL"
            )
            descripciones = [
                (r["descripcion"], r["temas"], r["desc_modelo"],
                 r["desc_fecha"], r["numero"])
                for r in cur.fetchall()
            ]
            self._conn.execute("DELETE FROM tomos")
            self._conn.executemany(
                "INSERT INTO tomos (orden, numero, autor, obras, paginas, notas) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            for columna, numeros in marked.items():
                if numeros:
                    self._conn.executemany(
                        f"UPDATE tomos SET {columna} = 1 WHERE numero = ?",
                        [(n,) for n in numeros],
                    )
            if targets:
                self._conn.executemany(
                    "UPDATE tomos SET precio_objetivo = ? WHERE numero = ?",
                    [(p, n) for n, p in targets.items()],
                )
            if descripciones:
                self._conn.executemany(
                    "UPDATE tomos SET descripcion = ?, temas = ?, "
                    "desc_modelo = ?, desc_fecha = ? WHERE numero = ?",
                    descripciones,
                )
            # Enlaces antiguos (guardados por orden) que aún no tienen
            # número: ahora que la colección está cargada, ya se puede.
            self._conn.execute(
                "UPDATE tomo_links SET numero = ("
                "  SELECT numero FROM tomos WHERE tomos.orden = "
                "tomo_links.orden ORDER BY tomos.rowid LIMIT 1"
                ") WHERE numero IS NULL"
            )
        logger.info(
            "Colección actualizada: %d tomo(s) (%d obtenidos, %d deseados, "
            "%d con descripción).",
            len(rows), len(marked["poseido"]), len(marked["deseado"]),
            len(descripciones),
        )
        return len(rows)

    @staticmethod
    def _numero_de_tomo(conn, clave) -> str:
        """
        Identidad de un tomo a partir de su número o de su orden.

        El NÚMERO es único ("415[27]" es Estrabón y "415.2" es Ovidio);
        el orden NO: tres pares de la colección lo comparten y las
        marcas de uno se contagiaban al otro (2026-07-29). Se acepta el
        orden por compatibilidad, y entonces manda el primero de los dos.

        Se recibe la conexión, no se toma el lock: los métodos de
        Database jamás pueden anidar `self._lock`.
        """
        texto = str(clave).strip()
        fila = conn.execute(
            "SELECT numero FROM tomos WHERE numero = ?", (texto,)
        ).fetchone()
        if fila is not None:
            return fila["numero"]
        fila = conn.execute(
            "SELECT numero FROM tomos WHERE orden = ? ORDER BY rowid LIMIT 1",
            (clave,),
        ).fetchone()
        return fila["numero"] if fila is not None else texto

    def set_tomo_flag(self, clave, columna: str, value: bool) -> None:
        """Marca/desmarca 'poseido' o 'deseado' en UN tomo."""
        if columna not in ("poseido", "deseado"):
            raise ValueError(f"Columna de etiquetado desconocida: {columna}")
        with self._lock, self._conn:
            numero = self._numero_de_tomo(self._conn, clave)
            self._conn.execute(
                f"UPDATE tomos SET {columna} = ? WHERE numero = ?",
                (1 if value else 0, numero),
            )

    # ------------------------------------------------------------------
    # Publicaciones vigiladas por tomo (enlaces con precio extraído)
    # ------------------------------------------------------------------
    def add_tomo_link(self, clave, url: str) -> int:
        """Añade una publicación vigilada a un tomo; devuelve su id."""
        with self._lock, self._conn:
            numero = self._numero_de_tomo(self._conn, clave)
            fila = self._conn.execute(
                "SELECT orden FROM tomos WHERE numero = ?", (numero,)
            ).fetchone()
            # `orden` se conserva por compatibilidad; si la colección aún
            # no está importada, se deduce de la propia clave.
            orden = fila["orden"] if fila else None
            if orden is None:
                orden = int(clave) if str(clave).strip().isdigit() else 0
            cur = self._conn.execute(
                "INSERT INTO tomo_links (orden, numero, url) VALUES (?, ?, ?)",
                (orden, numero, url.strip()),
            )
            return cur.lastrowid

    def add_tomo_link_if_new(
        self, clave, url: str, precio: Optional[float] = None
    ) -> Optional[int]:
        """
        Vigila la publicación de un tomo si no estaba ya (comparando la
        URL SIN parámetros de campaña). Devuelve el id nuevo, o None si
        ya existía. Lo usa el monitor: toda oferta identificada con la
        colección queda guardada en la ficha del tomo.
        """
        from app.utils import clean_ad_url

        limpia = clean_ad_url(url)
        if not limpia:
            return None
        for r in self.get_tomo_links(clave):
            if clean_ad_url(r["url"]) == limpia:
                if precio is not None and r["ultimo_precio"] != precio:
                    self.update_tomo_link_price(r["id"], precio)
                return None
        link_id = self.add_tomo_link(clave, limpia)
        if precio is not None:
            self.update_tomo_link_price(link_id, precio)
        logger.info("Publicación vigilada añadida al tomo %s: %s", clave, limpia)
        return link_id

    def remove_tomo_link(self, link_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM tomo_links WHERE id = ?", (link_id,)
            )

    def get_tomo_links(self, clave) -> list[sqlite3.Row]:
        """Publicaciones vigiladas de UN tomo (por número o por orden)."""
        with self._lock:
            numero = self._numero_de_tomo(self._conn, clave)
            cur = self._conn.execute(
                "SELECT * FROM tomo_links WHERE numero = ? ORDER BY id",
                (numero,),
            )
            return cur.fetchall()

    def update_tomo_link_price(
        self, link_id: int, precio: Optional[float]
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tomo_links SET ultimo_precio = ?, "
                "ultima_revision = ? WHERE id = ?",
                (
                    precio,
                    datetime.now().isoformat(timespec="seconds"),
                    link_id,
                ),
            )

    # ------------------------------------------------------------------
    # Descripción del contenido de cada tomo + búsqueda por palabras
    # ------------------------------------------------------------------
    def set_tomo_description(
        self,
        clave,
        descripcion: Optional[str],
        temas: Optional[str] = None,
        modelo: Optional[str] = None,
    ) -> None:
        """Guarda (o borra con None) la descripción de un tomo."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tomos SET descripcion = ?, temas = ?, "
                "desc_modelo = ?, desc_fecha = ? WHERE numero = ?",
                (
                    descripcion,
                    temas,
                    modelo,
                    datetime.now().isoformat(timespec="seconds")
                    if descripcion else None,
                    self._numero_de_tomo(self._conn, clave),
                ),
            )

    def tomos_sin_descripcion(self) -> list[sqlite3.Row]:
        """Tomos a los que aún les falta la descripción, en orden."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM tomos "
                "WHERE descripcion IS NULL OR TRIM(descripcion) = '' "
                "ORDER BY orden IS NULL, orden"
            )
            return cur.fetchall()

    def descripciones_count(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM tomos "
                "WHERE descripcion IS NOT NULL AND TRIM(descripcion) <> ''"
            )
            return cur.fetchone()["n"]

    # Peso de cada campo al ordenar los resultados de una búsqueda: lo
    # que coincide en el título vale más que lo que coincide en la
    # descripción (si no, un tema suelto adelanta al libro buscado).
    _PESOS_BUSQUEDA = (
        ("obras", 6.0), ("autor", 4.0), ("temas", 3.0),
        ("notas", 1.5), ("descripcion", 1.0), ("numero", 5.0),
    )

    def buscar_tomos(self, consulta: str, limite: int = 200) -> list[dict]:
        """
        Busca tomos por palabras clave en número, autor, título, notas,
        descripción y temas.

        Exige TODAS las palabras escritas (en cualquier campo y en
        cualquier orden), sin distinguir tildes ni mayúsculas. Devuelve
        `{fila, puntos, campos}` ordenado por relevancia. Con 423 tomos
        un barrido normal es instantáneo: no hace falta índice de texto
        completo ni complicar el esquema.
        """
        from app.utils import normalize

        palabras = [p for p in normalize(consulta).split() if p]
        if not palabras:
            return []
        resultados: list[dict] = []
        for fila in self.get_tomos():
            campos = {
                nombre: normalize(str(fila[nombre] or ""))
                for nombre, _peso in self._PESOS_BUSQUEDA
            }
            todo = " ".join(campos.values())
            if not all(p in todo for p in palabras):
                continue                      # falta alguna palabra
            puntos = 0.0
            donde: set[str] = set()
            for nombre, peso in self._PESOS_BUSQUEDA:
                texto = campos[nombre]
                for palabra in palabras:
                    if palabra in texto:
                        # Palabra COMPLETA vale más que parte de otra
                        exacta = palabra in texto.split()
                        puntos += peso * (1.6 if exacta else 1.0)
                        donde.add(nombre)
            resultados.append(
                {"fila": fila, "puntos": puntos, "campos": sorted(donde)}
            )
        resultados.sort(
            key=lambda r: (-r["puntos"], r["fila"]["orden"] or 9999)
        )
        return resultados[:limite]

    def set_tomo_target(self, clave, precio: Optional[float]) -> None:
        """Fija (o borra con None) el precio objetivo de un tomo."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tomos SET precio_objetivo = ? WHERE numero = ?",
                (precio, self._numero_de_tomo(self._conn, clave)),
            )

    def flag_count(self, columna: str) -> int:
        if columna not in ("poseido", "deseado"):
            raise ValueError(f"Columna de etiquetado desconocida: {columna}")
        with self._lock:
            cur = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM tomos WHERE {columna} = 1"
            )
            return cur.fetchone()["n"]

    def get_tomos(self) -> list[sqlite3.Row]:
        """Todos los tomos, ordenados por número de colección."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM tomos ORDER BY orden IS NULL, orden"
            )
            return cur.fetchall()

    def tomos_count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM tomos")
            return cur.fetchone()["n"]

    def clear_history(self, estado: Optional[str] = None) -> int:
        """
        Borra el historial de alertas y devuelve cuántas filas eliminó.

        Con `estado` borra solo ese subconjunto ('notificado', 'lote'...):
        así el botón "Limpiar" de una vista filtrada no arrasa el resto.
        No toca processed_uids ni price_history: no se re-notifica nada
        y la gráfica de precios conserva su serie.
        """
        with self._lock, self._conn:
            if estado is None:
                cur = self._conn.execute("DELETE FROM history")
            else:
                cur = self._conn.execute(
                    "DELETE FROM history WHERE estado = ?", (estado,)
                )
            deleted = cur.rowcount
        logger.info(
            "Historial limpiado: %d entrada(s) eliminadas%s.",
            deleted, f" (estado={estado})" if estado else "",
        )
        return deleted

    def close(self) -> None:
        """Cierra la conexión con la base de datos."""
        with self._lock:
            self._conn.close()
            logger.info("Base de datos cerrada.")
