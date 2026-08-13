"""
test_notifier.py
================
Pruebas de la cola de notificaciones SIN mostrar toasts reales:
se simulan los mecanismos (winotify/PowerShell) con monkeypatch.

Cubre:
- Orden FIFO de la cola (las ráfagas salen completas y en orden).
- Cadena de respaldo: si winotify y PowerShell fallan, cae al globo
  de bandeja (callback) y nunca lanza.
- La cola sobrevive a excepciones de un toast (no muere el hilo).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import notification
from app.utils import PriceAlert


def test_cola_fifo_sin_perdidas(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(notification, "_WINOTIFY_OK", False)
    monkeypatch.setattr(notification, "TOAST_SPACING_SECONDS", 0.01)
    monkeypatch.setattr(
        notification, "_ps_toast",
        lambda title, body, **kw: shown.append(title) or True,
    )
    n = notification.Notifier(enable_sound=False)
    for i in range(5):
        n._show_toast(f"toast-{i}", "cuerpo")
    n._queue.join()
    assert shown == [f"toast-{i}" for i in range(5)]


def test_cadena_cae_a_bandeja(monkeypatch):
    fallback_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notification, "_WINOTIFY_OK", False)
    monkeypatch.setattr(notification, "TOAST_SPACING_SECONDS", 0.01)
    monkeypatch.setattr(notification, "_ps_toast", lambda *a, **k: False)
    n = notification.Notifier(
        enable_sound=False,
        fallback=lambda t, m: fallback_calls.append((t, m)),
    )
    n._show_toast("Título", "Mensaje")
    n._queue.join()
    assert fallback_calls == [("Título", "Mensaje")]


def test_un_toast_que_explota_no_mata_la_cola(monkeypatch):
    shown: list[str] = []

    def flaky(title, body, **kw):
        if title == "malo":
            raise RuntimeError("boom")
        shown.append(title)
        return True

    monkeypatch.setattr(notification, "_WINOTIFY_OK", False)
    monkeypatch.setattr(notification, "TOAST_SPACING_SECONDS", 0.01)
    monkeypatch.setattr(notification, "_ps_toast", flaky)
    n = notification.Notifier(enable_sound=False)
    n._show_toast("malo", "x")
    n._show_toast("bueno", "y")
    n._queue.join()
    assert shown == ["bueno"]


def test_notify_price_drop_encola(monkeypatch):
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(notification, "_WINOTIFY_OK", False)
    monkeypatch.setattr(notification, "TOAST_SPACING_SECONDS", 0.01)
    monkeypatch.setattr(
        notification, "_ps_toast",
        lambda title, body, **kw: shown.append((title, body)) or True,
    )
    n = notification.Notifier(enable_sound=False)
    alert = PriceAlert(
        title="Plutarco — Vidas paralelas",
        old_price=40.0, new_price=4.0, discount_percent=90.0,
        link="https://www.todocoleccion.net/x~x123456789",
    )
    n.notify_price_drop(alert, extra_line="Tomo BCG nº 86 · ¡TE FALTA!")
    n._queue.join()
    assert len(shown) == 1
    _title, body = shown[0]
    assert "Plutarco" in body
    assert "90 %" in body
    assert "¡TE FALTA!" in body
