"""
generate_expected.py
====================
Genera los archivos `.expected.json` de la batería de pruebas sin tener
que escribirlos a mano.

Flujo:
1. Lee un `.eml` (o todos los de una carpeta).
2. Ejecuta `parse_alert_email()`.
3. Muestra por pantalla los datos extraídos, la estrategia usada para
   cada campo y la confianza.
4. Pregunta: ¿Son correctos? (S/N)
5. Si respondes S, crea `<nombre>.expected.json` junto al `.eml`, con el
   formato exacto que espera `tests/test_emails.py`.

Uso:
    python generate_expected.py                        # todos los .eml de tests/emails/ sin expected
    python generate_expected.py tests/emails/foo.eml   # un correo concreto
    python generate_expected.py tests/emails/          # una carpeta concreta
    python generate_expected.py --forzar foo.eml       # regenerar aunque ya exista el JSON

Respuestas admitidas: S/N (también sí/si/y/yes/no) y Q para salir del lote.
Si respondes N, el correo se salta: corrige el parser (el panel de
depuración ayuda: `python debug_panel.py ruta.eml`) y vuelve a ejecutar
este script.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import argparse
import json
import logging
import sys
from pathlib import Path

from app import utils
# Carpeta por defecto de la batería de pruebas
DEFAULT_DIR = Path(__file__).resolve().parent / "tests" / "emails"

# Campos que se muestran y se vuelcan al JSON, con su etiqueta en pantalla
_FIELDS = (
    ("title", "Título"),
    ("old_price", "Precio anterior"),
    ("new_price", "Precio nuevo"),
    ("discount_percent", "Descuento %"),
    ("link", "Enlace"),
    ("cover_image_url", "Portada"),
)

_YES = {"s", "si", "sí", "y", "yes"}
_NO = {"n", "no"}
_QUIT = {"q", "quit", "salir"}


def expected_path_for(eml_path: Path) -> Path:
    """Ruta del .expected.json correspondiente a un .eml."""
    return eml_path.with_name(eml_path.stem + ".expected.json")


def _notify_dataset_validated(eml_path: Path) -> None:
    """
    Registra la validación en las estadísticas del dataset (opcional).

    Si dataset.py no está presente o falla, el script sigue funcionando:
    las estadísticas son un extra, no un requisito para validar correos.
    """
    try:
        from app.dataset import DatasetManager

        DatasetManager().mark_validated(eml_path)
    except Exception as exc:  # noqa: BLE001 - las stats nunca deben bloquear
        logging.getLogger(__name__).debug("Estadísticas no actualizadas: %s", exc)


def show_alert(eml_path: Path, alert: "utils.PriceAlert", subject: str, sender: str) -> None:
    """Imprime de forma legible lo que el parser ha extraído."""
    print()
    print("=" * 72)
    print(f"Correo:    {eml_path.name}")
    print(f"Asunto:    {subject}")
    print(f"Remitente: {sender}")
    print("-" * 72)
    for key, label in _FIELDS:
        value = getattr(alert, key)
        shown = "—" if value in (None, "") else value
        strategy = alert.sources.get(key, "no detectado" if value in (None, "") else "?")
        print(f"  {label:<16} {shown}")
        print(f"  {'':<16} └─ estrategia: {strategy}")
    marker = "" if alert.confidence >= 0.6 else "  ⚠ BAJA"
    print("-" * 72)
    print(f"  Confianza        {alert.confidence:.2f}{marker}")
    print("=" * 72)


def build_expected(alert: "utils.PriceAlert") -> dict:
    """
    Construye el diccionario del .expected.json.

    Solo se incluyen los campos detectados (la batería compara únicamente
    las claves presentes), más la confianza actual como mínimo exigible.
    """
    expected: dict = {}
    for key, _label in _FIELDS:
        value = getattr(alert, key)
        if value not in (None, ""):
            expected[key] = value
    expected["min_confidence"] = alert.confidence
    return expected


def ask(prompt: str) -> str:
    """Pregunta por consola y devuelve la respuesta normalizada."""
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


def process_one(eml_path: Path, force: bool) -> str:
    """
    Procesa un .eml. Devuelve: 'creado', 'saltado', 'existente' o 'quit'.
    """
    out_path = expected_path_for(eml_path)
    if out_path.exists() and not force:
        print(f"\n· {eml_path.name}: ya tiene {out_path.name} (usa --forzar para regenerarlo).")
        return "existente"

    try:
        msg = utils.load_email_file(eml_path)
        alert = utils.parse_alert_email(msg)
    except Exception as exc:  # noqa: BLE001 - informar y continuar con el resto
        print(f"\n· {eml_path.name}: ERROR al parsear: {exc}")
        return "saltado"

    subject = utils.decode_header_value(msg.get("Subject", ""))
    sender = utils.decode_header_value(msg.get("From", ""))
    show_alert(eml_path, alert, subject, sender)

    while True:
        answer = ask("¿Son correctos? (S/N, Q para salir): ")
        if answer in _YES:
            expected = build_expected(alert)
            out_path.write_text(
                json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"✔ Generado {out_path}")
            _notify_dataset_validated(eml_path)
            return "creado"
        if answer in _NO:
            print(
                "✘ Saltado. Ajusta el parser y vuelve a ejecutar este script\n"
                f"  (para iterar: python debug_panel.py {eml_path})"
            )
            return "saltado"
        if answer in _QUIT:
            return "quit"
        print("Respuesta no reconocida: escribe S, N o Q.")


def collect_targets(target: Path, force: bool) -> list[Path]:
    """Lista de .eml a procesar según el argumento recibido."""
    if target.is_file():
        return [target]
    files = sorted(target.glob("*.eml"))
    if not force:
        # En modo carpeta, por defecto solo los que aún no tienen expected
        files = [f for f in files if not expected_path_for(f).exists()]
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera .expected.json para la batería de correos, previa validación manual."
    )
    parser.add_argument(
        "ruta", nargs="?", default=str(DEFAULT_DIR),
        help=f"Un .eml o una carpeta con .eml (por defecto: {DEFAULT_DIR})",
    )
    parser.add_argument(
        "--forzar", action="store_true",
        help="Regenerar aunque el .expected.json ya exista",
    )
    parser.add_argument(
        "--verboso", action="store_true",
        help="Mostrar el log INFO del parser (estrategias en tiempo real)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verboso else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    target = Path(args.ruta)
    if not target.exists():
        print(f"No existe la ruta: {target}")
        return 1
    if target.is_file() and target.suffix.lower() != ".eml":
        print(f"El archivo debe ser un .eml: {target}")
        return 1

    files = collect_targets(target, args.forzar)
    if not files:
        print(
            f"No hay .eml pendientes en {target} "
            "(todos tienen ya su .expected.json; usa --forzar para regenerarlos)."
        )
        return 0

    print(f"Correos a revisar: {len(files)}")
    stats = {"creado": 0, "saltado": 0, "existente": 0}
    for eml in files:
        result = process_one(eml, args.forzar)
        if result == "quit":
            print("Saliendo.")
            break
        stats[result] += 1

    print(
        f"\nResumen: {stats['creado']} generado(s), "
        f"{stats['saltado']} saltado(s), {stats['existente']} ya existente(s)."
    )
    if stats["creado"]:
        print("Ejecuta ahora la batería:  pytest tests/ -v")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # La salida se cerró (p. ej. `| head`): terminar en silencio.
        sys.exit(0)
