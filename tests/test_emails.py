"""
test_emails.py
==============
Batería de regresión del parser de correos de Todocolección.

Carga automáticamente TODOS los archivos `.eml` de `tests/emails/`,
ejecuta `parse_alert_email()` sobre cada uno y comprueba:

1. Aserciones genéricas (para cualquier correo):
   - Título extraído y distinto del genérico.
   - Precio nuevo presente.
   - Enlace al anuncio en todocoleccion.net.
   - Confianza >= 0.6 (umbral de advertencia del parser).

2. Aserciones exactas (opcionales): si junto al `.eml` existe un
   `<nombre>.expected.json`, se comparan los campos allí definidos
   (ver tests/emails/README.md).

Cada correo registra además una entrada en el informe final
(campos detectados, estrategia usada y confianza) — ver conftest.py.

Uso:  pytest tests/ -v
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app import utils
EMAILS_DIR = Path(__file__).resolve().parent / "emails"
EML_FILES = sorted(EMAILS_DIR.glob("*.eml"))

# Umbral mínimo de confianza aceptable para un correo real
MIN_CONFIDENCE = 0.6

# Tolerancia para comparar precios y porcentajes
_NUM_TOL = 0.05


def _load_expected(eml_path: Path) -> dict:
    """Carga <nombre>.expected.json si existe; si no, dict vacío."""
    expected_path = eml_path.with_name(eml_path.stem + ".expected.json")
    if not expected_path.exists():
        return {}
    with open(expected_path, encoding="utf-8") as fh:
        return json.load(fh)


def test_hay_correos_de_prueba():
    """Aviso temprano si la carpeta de correos está vacía."""
    assert EML_FILES, (
        f"No hay archivos .eml en {EMAILS_DIR}. "
        "Exporta correos reales desde Gmail (ver tests/emails/README.md)."
    )


@pytest.mark.parametrize("eml_path", EML_FILES, ids=lambda p: p.name)
def test_parse_correo(eml_path: Path, record_result):
    """Parsea un correo real y verifica los campos extraídos."""
    msg = utils.load_email_file(eml_path)
    subject = utils.decode_header_value(msg.get("Subject", ""))
    sender = utils.decode_header_value(msg.get("From", ""))

    alert = utils.parse_alert_email(msg)

    # Registrar SIEMPRE el resultado en el informe (aunque el test falle
    # después, así el informe muestra qué se detectó y qué no).
    record_result({
        "file": eml_path.name,
        "subject": subject,
        "sender": sender,
        "confidence": alert.confidence,
        "sources": dict(alert.sources),
        "values": {
            "title": alert.title,
            "old_price": alert.old_price,
            "new_price": alert.new_price,
            "discount_percent": alert.discount_percent,
            "link": alert.link,
            "cover_image_url": alert.cover_image_url,
        },
    })

    # ------------------------------------------------------------------
    # 1) Aserciones genéricas
    # ------------------------------------------------------------------
    assert alert.title and alert.title.strip(), "No se extrajo ningún título"
    assert alert.title != "Artículo en favoritos", "Título genérico de relleno"
    assert alert.new_price is not None, "No se extrajo el precio nuevo"
    assert alert.link, "No se extrajo el enlace al anuncio"
    assert "todocoleccion" in alert.link.lower(), f"Enlace sospechoso: {alert.link}"
    assert alert.confidence >= MIN_CONFIDENCE, (
        f"Confianza baja ({alert.confidence:.2f} < {MIN_CONFIDENCE}); "
        f"estrategias usadas: {alert.sources}"
    )

    # ------------------------------------------------------------------
    # 2) Valores esperados exactos (si hay .expected.json)
    # ------------------------------------------------------------------
    expected = _load_expected(eml_path)
    for key in ("old_price", "new_price", "discount_percent"):
        if key in expected:
            got = getattr(alert, key)
            assert got is not None, f"{key}: esperado {expected[key]}, no detectado"
            assert math.isclose(got, float(expected[key]), abs_tol=_NUM_TOL), (
                f"{key}: esperado {expected[key]}, obtenido {got}"
            )
    for key in ("title", "link", "cover_image_url"):
        if key in expected:
            got = getattr(alert, key)
            assert got == expected[key], f"{key}: esperado {expected[key]!r}, obtenido {got!r}"
    if "min_confidence" in expected:
        assert alert.confidence >= float(expected["min_confidence"]), (
            f"Confianza {alert.confidence:.2f} < mínima esperada "
            f"{expected['min_confidence']}"
        )
