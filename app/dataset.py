"""
dataset.py
==========
Gestión automática del conjunto de pruebas del parser de Todocolección.

Este módulo hace que la batería de regresión "aprenda" con el tiempo:

1. ARCHIVO AUTOMÁTICO: cada correo procesado por el monitor IMAP se
   guarda en `tests/emails/`, deduplicado por Message-ID (o, en su
   defecto, por hash SHA-256 del contenido).
2. COLA DE VALIDACIÓN: los correos archivados quedan pendientes hasta
   que el usuario los valida con `generate_expected.py` (que solo
   muestra los que aún no tienen `.expected.json`).
3. APRENDIZAJE: una vez validados, pasan a ser casos permanentes de la
   batería de `pytest`.
4. ROTACIÓN: con más de `max_emails` (50) correos, se eliminan los
   `rotate_count` (20) más antiguos —según la fecha de recepción— junto
   con su `.expected.json`, y la operación se registra en `log.txt`.
   Los archivos con prefijo `keep_` están PROTEGIDOS: nunca rotan
   (útil para casos de regresión históricos que no deben perderse).
5. ESTADÍSTICAS: `dataset_stats.json` acumula totales de procesados,
   archivados, validados, casos activos, rotados y fecha del último
   "entrenamiento" (última validación).
6. COBERTURA: cuenta cuántos correos usaron cada nivel de la cascada
   (HTML especializado / parser semántico / regex / heurísticas). Si la
   proporción de heurísticas crece, Todocolección está cambiando el
   formato y conviene revisar los extractores.

Integración con el monitor IMAP
-------------------------------
    from app import utils
    from app.dataset import DatasetManager

    ds = DatasetManager()                    # raíz del proyecto por defecto

    # ... dentro del bucle, tras descargar y parsear un correo válido:
    alert = utils.parse_alert_email(msg)
    ds.record_processed(alert)               # estadísticas + cobertura
    ds.archive_email(raw_bytes, msg)         # archiva, deduplica y rota

Uso desde línea de comandos
---------------------------
    python dataset.py stats        # muestra estadísticas y cobertura
    python dataset.py pendientes   # lista la cola de validación
    python dataset.py rotate       # fuerza una comprobación de rotación

Diseño para el futuro (punto 7)
-------------------------------
Todo el proyecto interactúa con este módulo SOLO a través del contrato
público de `DatasetManager` (métodos documentados abajo). Para sustituir
esta implementación por un sistema basado en IA (p. ej. selección
inteligente de qué casos conservar, validación asistida, detección de
drift de formato) basta con crear otra clase con los mismos métodos y
firmas; ni el monitor, ni `generate_expected.py`, ni la batería de
`pytest` necesitan cambios.
"""

from __future__ import annotations

import email
import email.message
import email.utils
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Configuración por defecto
# ----------------------------------------------------------------------
DEFAULT_MAX_EMAILS = 50    # máximo de .eml no protegidos en tests/emails/
DEFAULT_ROTATE_COUNT = 20  # cuántos eliminar al superar el máximo
PROTECTED_PREFIX = "keep_"  # los .eml con este prefijo nunca rotan

STATS_FILENAME = "dataset_stats.json"
LOG_FILENAME = "log.txt"

# Estructura inicial de las estadísticas
_EMPTY_STATS: dict = {
    "total_processed": 0,          # correos procesados por el monitor
    "archived": 0,                 # correos archivados en tests/emails/
    "validated": 0,                # correos validados (expected.json creado)
    "active_regression_cases": 0,  # .eml con expected.json ahora mismo
    "rotated_out": 0,              # eliminados por rotación (histórico)
    "last_training_date": None,    # última validación (ISO 8601)
    "coverage": {                  # correos que usaron cada nivel de la cascada
        "html_especializado": 0,
        "parser_semantico": 0,
        "regex": 0,
        "heuristica": 0,
    },
    "last_updated": None,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatasetManager:
    """
    Gestor del conjunto de pruebas.

    CONTRATO PÚBLICO (estable; una futura implementación basada en IA
    debe respetarlo):

    - record_processed(alert)        → registra un correo procesado y su
                                       cobertura de estrategias.
    - archive_email(raw, msg=None)   → archiva el .eml (deduplicado) y
                                       aplica rotación; devuelve la ruta
                                       o None si era duplicado.
    - mark_validated(eml_path)       → registra una validación humana.
    - pending_validation()           → lista de .eml sin expected.json.
    - rotate_if_needed()             → aplica la rotación; devuelve la
                                       lista de rutas eliminadas.
    - get_stats()                    → copia del diccionario de stats.
    """

    def __init__(
        self,
        root: Union[str, Path, None] = None,
        max_emails: int = DEFAULT_MAX_EMAILS,
        rotate_count: int = DEFAULT_ROTATE_COUNT,
    ) -> None:
        # Por defecto, la raíz es la carpeta donde vive este módulo
        # (la raíz del proyecto), independiente del cwd del monitor.
        self.root = Path(root) if root else Path(__file__).resolve().parent.parent
        self.emails_dir = self.root / "tests" / "emails"
        self.stats_path = self.root / STATS_FILENAME
        self.log_path = self.root / LOG_FILENAME
        self.max_emails = max_emails
        self.rotate_count = rotate_count
        self.emails_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1) Archivo automático con deduplicación
    # ------------------------------------------------------------------
    def archive_email(
        self,
        raw_bytes: bytes,
        msg: Optional[email.message.Message] = None,
    ) -> Optional[Path]:
        """
        Archiva un correo en `tests/emails/` si no estaba ya.

        Deduplicación: por Message-ID si existe; si no, por SHA-256 del
        contenido. Además se comparan los hashes de contenido con los
        archivos ya presentes (cubre correos añadidos a mano).

        Devuelve la ruta creada, o None si el correo era un duplicado.
        """
        if msg is None:
            msg = email.message_from_bytes(raw_bytes)

        key = self._dedupe_key(raw_bytes, msg)
        if self._already_archived(raw_bytes, key):
            logger.debug("Correo duplicado (clave %s): no se archiva.", key)
            return None

        received = self._message_date(msg) or datetime.now(timezone.utc)
        base = f"{received:%Y%m%d-%H%M%S}_{key}"
        path = self.emails_dir / f"{base}.eml"
        counter = 1
        while path.exists():  # colisión de nombre (misma fecha, clave distinta)
            path = self.emails_dir / f"{base}-{counter}.eml"
            counter += 1

        try:
            path.write_bytes(raw_bytes)
        except OSError as exc:
            logger.warning("No se pudo archivar el correo en %s: %s", path, exc)
            return None

        logger.info("Correo archivado para la batería: %s", path.name)
        self._log_line(f"ARCHIVADO: {path.name}")

        stats = self._load_stats()
        stats["archived"] += 1
        self._save_stats(stats)

        # 4) La rotación se comprueba en cada archivado
        self.rotate_if_needed()
        return path

    def _dedupe_key(self, raw_bytes: bytes, msg: email.message.Message) -> str:
        """Clave de deduplicación: Message-ID si existe, si no SHA-256."""
        message_id = (msg.get("Message-ID") or "").strip()
        if message_id:
            return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:12]
        return hashlib.sha256(raw_bytes).hexdigest()[:12]

    def _already_archived(self, raw_bytes: bytes, key: str) -> bool:
        """True si ya existe un .eml con la misma clave o el mismo contenido."""
        # a) Por clave en el nombre de archivo (correos archivados por nosotros)
        if any(self.emails_dir.glob(f"*_{key}.eml")) or any(
            self.emails_dir.glob(f"*_{key}-*.eml")
        ):
            return True
        # b) Por contenido exacto (cubre .eml añadidos a mano con otro nombre)
        raw_hash = hashlib.sha256(raw_bytes).hexdigest()
        for existing in self.emails_dir.glob("*.eml"):
            try:
                if hashlib.sha256(existing.read_bytes()).hexdigest() == raw_hash:
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _message_date(msg: email.message.Message) -> Optional[datetime]:
        """Fecha de recepción según la cabecera Date, si es parseable."""
        try:
            date = email.utils.parsedate_to_datetime(msg.get("Date", ""))
            # Normalizar a datetime con zona para poder comparar
            if date and date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return date
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # 2) Cola de validación
    # ------------------------------------------------------------------
    def pending_validation(self) -> list[Path]:
        """Correos archivados que aún no tienen su .expected.json."""
        pending: list[Path] = []
        for eml in sorted(self.emails_dir.glob("*.eml")):
            expected = eml.with_name(eml.stem + ".expected.json")
            if not expected.exists():
                pending.append(eml)
        return pending

    # ------------------------------------------------------------------
    # 3) Aprendizaje: validación humana
    # ------------------------------------------------------------------
    def mark_validated(self, eml_path: Union[str, Path]) -> None:
        """
        Registra que un correo ha sido validado (expected.json creado).

        Lo llama `generate_expected.py` tras cada confirmación del
        usuario; actualiza el contador y la fecha de "entrenamiento".
        """
        stats = self._load_stats()
        stats["validated"] += 1
        stats["last_training_date"] = _utcnow_iso()
        self._save_stats(stats)
        self._log_line(f"VALIDADO: {Path(eml_path).name}")

    # ------------------------------------------------------------------
    # 4) Rotación del histórico
    # ------------------------------------------------------------------
    def rotate_if_needed(self) -> list[Path]:
        """
        Si hay más de `max_emails` correos NO protegidos, elimina los
        `rotate_count` más antiguos (por fecha de recepción; si no hay
        cabecera Date, por fecha de modificación del archivo) junto con
        su .expected.json, y lo anota en log.txt.

        Devuelve la lista de .eml eliminados.
        """
        rotatable = [
            p for p in self.emails_dir.glob("*.eml")
            if not p.name.startswith(PROTECTED_PREFIX)
        ]
        if len(rotatable) <= self.max_emails:
            return []

        rotatable.sort(key=self._received_or_mtime)
        victims = rotatable[: self.rotate_count]

        removed: list[Path] = []
        for eml in victims:
            expected = eml.with_name(eml.stem + ".expected.json")
            had_expected = expected.exists()
            try:
                eml.unlink()
                if had_expected:
                    expected.unlink()
            except OSError as exc:
                logger.warning("No se pudo rotar %s: %s", eml.name, exc)
                continue
            removed.append(eml)
            suffix = " (+ expected.json)" if had_expected else ""
            self._log_line(
                f"ROTACIÓN: eliminado {eml.name}{suffix} — "
                f"recibido {self._received_or_mtime(eml):%Y-%m-%d}"
            )

        if removed:
            logger.info(
                "Rotación del dataset: eliminados %d correos antiguos.", len(removed)
            )
            stats = self._load_stats()
            stats["rotated_out"] += len(removed)
            self._save_stats(stats)

        # Higiene: eliminar .expected.json huérfanos (sin su .eml)
        for orphan in self.emails_dir.glob("*.expected.json"):
            stem = orphan.name[: -len(".expected.json")]
            if not (self.emails_dir / f"{stem}.eml").exists():
                try:
                    orphan.unlink()
                    self._log_line(f"LIMPIEZA: eliminado huérfano {orphan.name}")
                except OSError as exc:
                    logger.warning("No se pudo eliminar %s: %s", orphan.name, exc)
        return removed

    def _received_or_mtime(self, eml_path: Path) -> datetime:
        """Fecha de recepción de un .eml, o su mtime si no es parseable."""
        # El nombre de los archivados por nosotros ya empieza por la fecha
        m = re.match(r"(\d{8}-\d{6})_", eml_path.name)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass
        # Si no, leer la cabecera Date del propio correo
        try:
            msg = email.message_from_bytes(eml_path.read_bytes())
            date = self._message_date(msg)
            if date:
                return date
        except OSError:
            pass
        return datetime.fromtimestamp(eml_path.stat().st_mtime, tz=timezone.utc)

    # ------------------------------------------------------------------
    # 5) y 6) Estadísticas y cobertura de estrategias
    # ------------------------------------------------------------------
    def record_processed(self, alert) -> None:
        """
        Registra un correo procesado y las estrategias que utilizó.

        `alert` es el PriceAlert devuelto por `parse_alert_email()`; su
        campo `sources` indica la estrategia ganadora de cada campo. Un
        correo puede sumar en varias categorías (p. ej. enlace por HTML
        y precios por heurística): así se ve exactamente qué niveles de
        la cascada se están usando.
        """
        stats = self._load_stats()
        stats["total_processed"] += 1
        for category in self._coverage_categories(getattr(alert, "sources", {})):
            stats["coverage"][category] = stats["coverage"].get(category, 0) + 1
        self._save_stats(stats)

    @staticmethod
    def _coverage_categories(sources: dict) -> set[str]:
        """Clasifica las estrategias usadas en las 4 categorías de cobertura."""
        categories: set[str] = set()
        for value in sources.values():
            v = str(value).lower()
            if v.startswith("html especializado"):
                categories.add("html_especializado")
            elif "semántico" in v or "semantico" in v:
                categories.add("parser_semantico")
            elif "regex" in v:
                categories.add("regex")
            elif "heurística" in v or "heuristica" in v or "asunto" in v:
                categories.add("heuristica")
            # "calculado desde precios" es un dato derivado, no una
            # estrategia de extracción: no cuenta para la cobertura.
        return categories

    def get_stats(self) -> dict:
        """Devuelve una copia actualizada de las estadísticas."""
        return self._load_stats()

    # ------------------------------------------------------------------
    # Persistencia interna
    # ------------------------------------------------------------------
    def _load_stats(self) -> dict:
        stats = json.loads(json.dumps(_EMPTY_STATS))  # copia profunda
        if self.stats_path.exists():
            try:
                on_disk = json.loads(self.stats_path.read_text(encoding="utf-8"))
                stats.update(on_disk)
                # Asegurar subclaves de cobertura aunque falten en disco
                coverage = dict(_EMPTY_STATS["coverage"])
                coverage.update(stats.get("coverage") or {})
                stats["coverage"] = coverage
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("dataset_stats.json ilegible (%s); se regenera.", exc)
        # El número de casos activos se recalcula siempre desde disco
        stats["active_regression_cases"] = sum(
            1 for eml in self.emails_dir.glob("*.eml")
            if eml.with_name(eml.stem + ".expected.json").exists()
        )
        return stats

    def _save_stats(self, stats: dict) -> None:
        stats["last_updated"] = _utcnow_iso()
        try:
            self.stats_path.write_text(
                json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("No se pudieron guardar las estadísticas: %s", exc)

    def _log_line(self, text: str) -> None:
        """Añade una línea con fecha a log.txt (tolerante a fallos)."""
        line = f"[{_utcnow_iso()}] {text}\n"
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            logger.warning("No se pudo escribir en %s: %s", self.log_path, exc)


# ----------------------------------------------------------------------
# Uso desde línea de comandos
# ----------------------------------------------------------------------
def _cmd_stats(ds: DatasetManager) -> None:
    stats = ds.get_stats()
    print("Estadísticas del conjunto de pruebas")
    print("-" * 44)
    print(f"  Correos procesados:        {stats['total_processed']}")
    print(f"  Correos archivados:        {stats['archived']}")
    print(f"  Correos validados:         {stats['validated']}")
    print(f"  Casos de regresión activos:{stats['active_regression_cases']:>5}")
    print(f"  Eliminados por rotación:   {stats['rotated_out']}")
    print(f"  Último entrenamiento:      {stats['last_training_date'] or '—'}")
    print()
    print("Cobertura de estrategias (correos que usaron cada nivel)")
    print("-" * 44)
    cov = stats["coverage"]
    total = stats["total_processed"] or 1
    for key, label in (
        ("html_especializado", "HTML especializado"),
        ("parser_semantico", "Parser semántico"),
        ("regex", "Regex"),
        ("heuristica", "Heurísticas"),
    ):
        n = cov.get(key, 0)
        print(f"  {label:<20} {n:>4}  ({100 * n / total:5.1f} % de los procesados)")
    pendientes = len(ds.pending_validation())
    print()
    print(f"Cola de validación: {pendientes} correo(s) pendiente(s)")
    if pendientes:
        print("  → python generate_expected.py")


def _cmd_pendientes(ds: DatasetManager) -> None:
    pending = ds.pending_validation()
    if not pending:
        print("No hay correos pendientes de validación.")
        return
    print(f"{len(pending)} correo(s) pendiente(s) de validación:")
    for p in pending:
        print(f"  - {p.name}")
    print("\nValídalos con: python generate_expected.py")


def _cmd_rotate(ds: DatasetManager) -> None:
    removed = ds.rotate_if_needed()
    if removed:
        print(f"Rotación aplicada: {len(removed)} correo(s) eliminados (ver log.txt).")
    else:
        print(
            f"No hace falta rotar: hay {len(list(ds.emails_dir.glob('*.eml')))} "
            f"correos (límite {ds.max_emails} sin contar protegidos '{PROTECTED_PREFIX}*')."
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ds = DatasetManager()
    command = sys.argv[1] if len(sys.argv) > 1 else "stats"
    commands = {
        "stats": _cmd_stats,
        "pendientes": _cmd_pendientes,
        "rotate": _cmd_rotate,
    }
    if command not in commands:
        print(f"Comando desconocido: {command}. Usa: stats | pendientes | rotate")
        return 1
    commands[command](ds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
