"""
test_dataset.py
===============
Pruebas del gestor del conjunto de pruebas (dataset.py).

Se ejecutan sobre un directorio temporal (fixture `tmp_path`), de modo
que nunca tocan el dataset real de `tests/emails/`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from app import utils
from app.dataset import PROTECTED_PREFIX, DatasetManager


def make_raw(i: int, base: datetime, with_message_id: bool = True) -> bytes:
    """Crea los bytes de un correo mínimo con fecha y contenido únicos."""
    msg = EmailMessage()
    msg["From"] = "Todocoleccion <seguimientos@todocoleccion.net>"
    msg["Subject"] = f"Bajada de precio nº {i}"
    msg["Date"] = format_datetime(base + timedelta(days=i))
    if with_message_id:
        msg["Message-ID"] = f"<correo-{i}@todocoleccion.net>"
    msg.set_content(f"Libro {i}: antes 20 €, ahora 5 €.")
    return bytes(msg)


@pytest.fixture
def ds(tmp_path):
    """DatasetManager pequeño (límite 10, rota 4) sobre carpeta temporal."""
    return DatasetManager(root=tmp_path, max_emails=10, rotate_count=4)


BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# 1) Archivo automático y deduplicación
# ----------------------------------------------------------------------
def test_archiva_y_deduplica_por_message_id(ds):
    raw = make_raw(1, BASE)
    path = ds.archive_email(raw)
    assert path is not None and path.exists()
    assert path.parent == ds.emails_dir

    # El mismo correo (mismo Message-ID) no se archiva dos veces
    assert ds.archive_email(raw) is None
    # Ni siquiera si el contenido cambia pero el Message-ID se mantiene
    modificado = raw.replace(b"Libro 1", b"Libro 1 editado")
    assert ds.archive_email(modificado) is None
    assert len(list(ds.emails_dir.glob("*.eml"))) == 1


def test_deduplica_por_contenido_sin_message_id(ds):
    raw = make_raw(2, BASE, with_message_id=False)
    assert ds.archive_email(raw) is not None
    assert ds.archive_email(raw) is None  # mismo contenido → duplicado
    assert len(list(ds.emails_dir.glob("*.eml"))) == 1


def test_deduplica_contra_archivos_anadidos_a_mano(ds):
    raw = make_raw(3, BASE)
    # Simular un .eml copiado a mano con un nombre cualquiera
    (ds.emails_dir / "correo_manual.eml").write_bytes(raw)
    assert ds.archive_email(raw) is None


# ----------------------------------------------------------------------
# 2) Cola de validación
# ----------------------------------------------------------------------
def test_cola_de_validacion(ds):
    p1 = ds.archive_email(make_raw(1, BASE))
    p2 = ds.archive_email(make_raw(2, BASE))
    assert set(ds.pending_validation()) == {p1, p2}

    # Validar uno: crear su expected.json → sale de la cola
    p1.with_name(p1.stem + ".expected.json").write_text("{}", encoding="utf-8")
    assert ds.pending_validation() == [p2]


# ----------------------------------------------------------------------
# 4) Rotación con protegidos y registro en log.txt
# ----------------------------------------------------------------------
def test_rotacion_elimina_los_mas_antiguos(ds):
    # Un caso protegido MUY antiguo: jamás debe rotar
    protegido = ds.emails_dir / f"{PROTECTED_PREFIX}caso_historico.eml"
    protegido.write_bytes(make_raw(0, BASE - timedelta(days=999)))

    # Archivar 10 correos (justo el límite) y validar los 4 más antiguos
    paths = [ds.archive_email(make_raw(i, BASE)) for i in range(1, 11)]
    for p in paths[:4]:
        p.with_name(p.stem + ".expected.json").write_text("{}", encoding="utf-8")

    # El nº 11 supera el límite y dispara la rotación: quedan 11 - 4 = 7
    paths.append(ds.archive_email(make_raw(11, BASE)))
    restantes = [
        p for p in ds.emails_dir.glob("*.eml")
        if not p.name.startswith(PROTECTED_PREFIX)
    ]
    assert len(restantes) == 7
    # Los 4 eliminados son los más antiguos (días 1-4) y se fueron con su JSON
    nombres = {p.name for p in restantes}
    for viejo in paths[:4]:
        assert viejo.name not in nombres
        assert not viejo.with_name(viejo.stem + ".expected.json").exists()
    # El protegido sigue intacto pese a ser el más antiguo de todos
    assert protegido.exists()
    # Y la operación quedó registrada en log.txt
    log = ds.log_path.read_text(encoding="utf-8")
    assert log.count("ROTACIÓN") == 4
    assert ds.get_stats()["rotated_out"] == 4


# ----------------------------------------------------------------------
# 5) y 6) Estadísticas y cobertura
# ----------------------------------------------------------------------
def _alert(sources: dict) -> utils.PriceAlert:
    return utils.PriceAlert(
        title="t", old_price=20.0, new_price=5.0, discount_percent=75.0,
        link="https://www.todocoleccion.net/lote/x", sources=sources,
    )


def test_estadisticas_y_cobertura(ds):
    ds.record_processed(_alert({
        "link": "HTML especializado (botón 'Ver')",
        "old_price": "HTML especializado (tachado)",
        "discount_percent": "parser semántico (encabezado 'Descuento del XX%')",
    }))
    ds.record_processed(_alert({
        "old_price": "heurística de texto",
        "new_price": "regex (único precio)",
        "title": "asunto/heurística de texto",
        "discount_percent": "calculado desde precios",  # no cuenta en cobertura
    }))

    p = ds.archive_email(make_raw(1, BASE))
    ds.mark_validated(p)
    p.with_name(p.stem + ".expected.json").write_text("{}", encoding="utf-8")

    stats = ds.get_stats()
    assert stats["total_processed"] == 2
    assert stats["archived"] == 1
    assert stats["validated"] == 1
    assert stats["active_regression_cases"] == 1
    assert stats["last_training_date"] is not None
    assert stats["coverage"] == {
        "html_especializado": 1,
        "parser_semantico": 1,
        "regex": 1,
        "heuristica": 1,
    }

    # El archivo dataset_stats.json existe y es JSON válido
    on_disk = json.loads(ds.stats_path.read_text(encoding="utf-8"))
    assert on_disk["total_processed"] == 2


def test_stats_corruptas_se_regeneran(ds):
    ds.stats_path.write_text("{esto no es json", encoding="utf-8")
    ds.record_processed(_alert({}))  # no debe lanzar excepción
    assert ds.get_stats()["total_processed"] == 1
