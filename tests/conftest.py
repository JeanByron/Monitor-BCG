"""
conftest.py
===========
Infraestructura común de la batería de correos:

- Añade la raíz del proyecto al sys.path para poder importar `utils`.
- Fixture `record_result` con la que cada test registra qué campos se
  detectaron, con qué estrategia y con qué confianza.
- Al final de la sesión imprime un informe por correo y lo guarda en
  `tests/last_report.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# La raíz del proyecto (donde vive utils.py) es el padre de tests/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

# Resultados acumulados durante la sesión (uno por correo)
_RESULTS: list[dict] = []

REPORT_PATH = Path(__file__).resolve().parent / "last_report.md"

# Campos del PriceAlert que aparecen en el informe, con su etiqueta
_FIELDS = (
    ("title", "Título"),
    ("old_price", "Precio anterior"),
    ("new_price", "Precio nuevo"),
    ("discount_percent", "Descuento %"),
    ("link", "Enlace"),
    ("cover_image_url", "Portada"),
)


@pytest.fixture
def record_result():
    """Devuelve una función con la que el test registra su resultado."""

    def _record(entry: dict) -> None:
        _RESULTS.append(entry)

    return _record


def _shorten(value, width: int = 60) -> str:
    text = str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _build_report_lines() -> list[str]:
    """Construye el informe en Markdown (también legible en terminal)."""
    lines: list[str] = ["# Informe de parseo de correos", ""]
    total = len(_RESULTS)
    low = sum(1 for r in _RESULTS if r["confidence"] < 0.6)
    lines.append(f"Correos procesados: **{total}** — con confianza baja (<0.6): **{low}**")
    lines.append("")

    for r in _RESULTS:
        conf = r["confidence"]
        flag = "⚠️" if conf < 0.6 else "✅"
        lines.append(f"## {flag} {r['file']}  (confianza = {conf:.2f})")
        lines.append("")
        lines.append(f"- Asunto: {_shorten(r['subject'])}")
        lines.append(f"- Remitente: {_shorten(r['sender'])}")
        lines.append("")
        lines.append("| Campo | Valor | Estrategia |")
        lines.append("|---|---|---|")
        for key, label in _FIELDS:
            value = r["values"].get(key)
            detected = "—" if value in (None, "") else _shorten(value, 45)
            strategy = r["sources"].get(key, "no detectado" if value in (None, "") else "?")
            lines.append(f"| {label} | {detected} | {strategy} |")
        lines.append("")
    return lines


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    """Imprime el informe al final de pytest y lo guarda en last_report.md."""
    if not _RESULTS:
        return
    lines = _build_report_lines()

    terminalreporter.write_sep("=", "INFORME DE PARSEO DE CORREOS")
    for line in lines:
        terminalreporter.write_line(line)
    try:
        REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        terminalreporter.write_line(f"\nInforme guardado en: {REPORT_PATH}")
    except OSError as exc:
        terminalreporter.write_line(f"\nNo se pudo guardar el informe: {exc}")
