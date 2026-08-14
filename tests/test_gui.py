"""
test_gui.py
===========
Prueba de humo de la interfaz: abre CADA ventana con datos reales de la
colección y comprueba lo que se ha roto alguna vez.

Corre sin pantalla (`QT_QPA_PLATFORM=offscreen`); si falta PySide6, el
módulo entero se salta. No toca la red ni la base de datos del usuario:
todo va contra SQLite en memoria.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="la GUI necesita PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import collection  # noqa: E402
from app.database import Database  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from app import gui

    instancia = QApplication.instance() or QApplication([])
    instancia.setStyleSheet(gui.GREDOS_QSS)
    yield instancia


@pytest.fixture(scope="module")
def db():
    """BD en memoria con la colección real y algo de historia."""
    base = Database(path=":memory:")
    tomos = collection.load_excel()
    base.replace_tomos(
        [(t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
         for t in tomos]
    )
    canon = next(t for t in tomos if t.orden == 2).canonical_title()
    base.add_price_point(canon, 30.0, url="https://tc/a~x1", mensaje_id="m1")
    base.add_price_point(canon, 22.0, url="https://tc/b~x2", mensaje_id="m2")
    base.add_tomo_link_if_new(2, "https://tc/a~x1", 30.0)
    base.add_history("Alerta", 40.0, 22.0, 45.0, "https://tc/a~x1",
                     "notificado", mensaje_id="m1")
    base.add_lot_price_point("[LOTE ×5] Lote de prueba", 90.0,
                             url="https://tc/l~x9", mensaje_id="m3")
    base.add_lote_if_new("[LOTE ×5] Lote de prueba", "https://tc/l~x9", 90.0)
    yield base
    base.close()


def _abrir(dialogo):
    dialogo.show()
    QApplication.processEvents()
    return dialogo


def test_historial_busca_y_ordena(app, db):
    from app import gui

    d = _abrir(gui.HistoryDialog(db))
    assert d.table.rowCount() == 1
    d.ed_search.setText("no-existe")
    QApplication.processEvents()
    assert d.table.isRowHidden(0)
    d.ed_search.setText("")
    QApplication.processEvents()
    assert not d.table.isRowHidden(0)
    for i in range(d.cmb_sort.count()):       # todos los criterios de orden
        d.cmb_sort.setCurrentIndex(i)
        QApplication.processEvents()
    d.close()


def test_precios_pinta_puntos_clicables(app, db):
    from app import gui

    d = _abrir(gui.PriceHistoryDialog(db))
    assert d.table.rowCount() == 1              # el lote NO sale aquí
    d.table.selectRow(0)
    QApplication.processEvents()
    d.chart.repaint()
    assert len(d.chart._coords) == 2
    assert "2 precio(s)" in d.lbl_info.text()
    d.close()


def test_precios_ordena_por_precio(app, db):
    """
    Filtro "Precio": mayor o menor, en Precios y en Lotes (2026-08-01).
    """
    from app import gui

    canon = collection.load_excel()
    barato = next(t for t in canon if t.orden == 5).canonical_title()
    db.add_price_point(barato, 3.0, url="https://tc/c~x9", mensaje_id="mx")

    d = _abrir(gui.PriceHistoryDialog(db))
    assert d.table.rowCount() == 2
    d.cmb_price.setCurrentText("Precio mayor")
    QApplication.processEvents()
    assert d.table.item(0, 1).text() == "22 €"
    d.cmb_price.setCurrentText("Precio menor")
    QApplication.processEvents()
    assert d.table.item(0, 1).text() == "3 €"
    # El buscador sigue funcionando con la lista ordenada
    d.ed_search.setText("no-existe")
    QApplication.processEvents()
    assert "0 de 2" in d.lbl_count.text()
    d.close()
    db.delete_price_points(Database._title_key(barato), "https://tc/c~x9")


def test_lotes_ordena_por_precio(app, db):
    from app import gui

    db.add_lote_if_new("[LOTE ×2] Barato", "https://tc/b~x7", 15.0)
    db.add_lot_price_point("[LOTE ×2] Barato", 15.0, url="https://tc/b~x7")
    try:
        d = _abrir(gui.LotesDialog(db))
        d.cmb_price.setCurrentText("Precio mayor")
        QApplication.processEvents()
        assert d.table.item(0, 1).text() == "90 €"
        d.cmb_price.setCurrentText("Precio menor")
        QApplication.processEvents()
        assert d.table.item(0, 1).text() == "15 €"
        d.close()
    finally:
        # La BD es del módulo entero: se deja como estaba
        for r in db.get_lotes():
            if r["titulo"] == "[LOTE ×2] Barato":
                db.remove_lote(r["id"])
        db.delete_lot_series(db.lot_key("[LOTE ×2] Barato"))


def test_lotes_abre_con_el_boton_editar_activo(app, db):
    """
    REGRESIÓN: la clase base selecciona la primera fila en su _reload(),
    antes de existir el botón; como la selección no cambia después,
    "Editar títulos" se quedaba apagado al abrir la ventana.
    """
    from app import gui

    d = _abrir(gui.LotesDialog(db))
    assert d.table.rowCount() == 1
    assert d.btn_edit.isEnabled()
    # Mensaje de error en el rojo de la app
    d._set_lots_info("no reconocido", error=True)
    assert "#c0392b" in d.lbl_lots_info.styleSheet()
    d._set_lots_info("normal")
    assert "#c0392b" not in d.lbl_lots_info.styleSheet()
    # URL inválida: avisa y no añade nada
    d.ed_url.setText("esto-no-es-una-url")
    d._add_lot()
    assert "http" in d.lbl_lots_info.text()
    assert len(db.get_lotes()) == 1
    d.close()


def test_coleccion_filtros_y_casillas(app, db):
    from app import gui

    d = _abrir(gui.CollectionDialog(db))
    assert d.table.rowCount() >= 400
    for i in range(d.cmb_filter.count()):
        d.cmb_filter.setCurrentIndex(i)
        QApplication.processEvents()
    d.cmb_filter.setCurrentIndex(0)
    QApplication.processEvents()
    # Ordenar por la casilla "Deseado" no debe reventar
    d.table.sortItems(d._COL_WISHED, Qt.SortOrder.DescendingOrder)
    d.table.sortItems(0, Qt.SortOrder.AscendingOrder)
    QApplication.processEvents()
    # Marcar persiste en la BD, y en EL tomo marcado: la casilla lleva
    # el NÚMERO, porque tres pares de tomos comparten el orden y antes
    # se marcaban de dos en dos (2026-07-29).
    item = d.table.item(0, d._COL_WISHED)
    numero = item.data(Qt.ItemDataRole.UserRole)
    item.setCheckState(Qt.CheckState.Checked)
    QApplication.processEvents()
    marcados = [r["numero"] for r in db.get_tomos() if r["deseado"]]
    assert marcados == [numero]
    item.setCheckState(Qt.CheckState.Unchecked)
    QApplication.processEvents()
    d.close()


def test_ficha_del_tomo(app, db):
    from app import gui

    d = _abrir(gui.TomoDialog(db, 2))
    assert d.links_table.rowCount() == 1
    assert len(d._search_urls) == 4
    for _, url in d._search_urls:            # sin "Gredos" ni autor colectivo
        assert "gredos" not in url.lower()
        assert "vvaa" not in url.lower()
    d.ed_target.setText("12,50")
    d._save_target()
    assert "Guardado" in d.lbl_target_info.text()
    d.ed_target.setText("")
    d._save_target()
    d.ed_link.setText("no-es-url")
    d._add_link()
    assert "http" in d.lbl_links_info.text()
    d.close()
    # Un número inexistente no puede reventar la ventana
    _abrir(gui.TomoDialog(db, 99999)).close()


def test_selector_de_tomos_del_lote(app, db):
    from app import gui

    tomos = collection.tomos_from_rows(db.get_tomos())
    d = _abrir(gui.TomoPickerDialog(tomos, {3, 9}, None))
    # La lista de arriba muestra lo YA registrado, por número
    assert [int(d.lot_table.item(i, 0).text())
            for i in range(d.lot_table.rowCount())] == [3, 9]
    assert "2 tomo(s)" in d.lbl_count.text()
    # Buscar por nº, autor u obra
    d.ed_search.setText("herodoto")
    QApplication.processEvents()
    visibles = [i for i in range(d.table.rowCount())
                if not d.table.isRowHidden(i)]
    assert 0 < len(visibles) < 40
    d.ed_search.setText("")
    QApplication.processEvents()
    # Quitar desde la lista de arriba
    d._remove_from_lot(0, 1)
    QApplication.processEvents()
    assert d.lot_table.rowCount() == 1
    d.accept()
    assert [t.orden for t in d.selected] == [9]


def test_resto_de_ventanas_abren(app, db):
    from app.config import Config
    from app import gui

    for fabrica in (
        lambda: gui.ThresholdsDialog(db),
        lambda: gui.ConfigDialog(Config()),
        lambda: gui.GredosMessageBox(None, "Título", "Texto de prueba"),
    ):
        d = _abrir(fabrica())
        d.close()
        QApplication.processEvents()


# ----------------------------------------------------------------------
# Barra de progreso dorada + ventana de Textos
# ----------------------------------------------------------------------
def test_barra_de_progreso_dorada(app):
    from app import gui

    barra = gui.GlowProgress()
    barra.show()
    QApplication.processEvents()
    assert barra._anim.state() == gui.QVariantAnimation.State.Running

    barra.avanzar("Extrayendo el texto", 226, 645)
    assert barra.maximum() == 645 and barra.value() == 226
    assert "35 %" in barra._etiqueta()
    barra.repaint()                       # el pintado no revienta

    barra.arrancar("Abriendo el PDF")     # sin total: indeterminada
    assert barra.maximum() == 0 and barra._etiqueta() == "Abriendo el PDF"
    barra._set_fase(0.7)                  # la banda en vaivén
    barra.repaint()

    # Oculta = animación parada (no quemar CPU con la ventana cerrada)
    barra.parar()
    QApplication.processEvents()
    assert barra.isHidden()
    assert barra._anim.state() != gui.QVariantAnimation.State.Running


def test_textos_lista_en_orden_y_con_barra(app, db):
    from app import gui

    d = _abrir(gui.TextosDialog(db))
    assert d.table.rowCount() == 423
    # Del 1 al 423: la tabla heredaba el indicador descendente
    assert d.table.item(0, 0).text().startswith("1")
    assert d.table.item(d.table.rowCount() - 1, 0).text() == "422"
    # Y ningún número con la cola ".0" de openpyxl
    assert not any(
        d.table.item(i, 0).text().endswith(".0")
        for i in range(d.table.rowCount())
    )
    assert d.progress.isHidden()          # solo se ve mientras analiza
    d.progress.arrancar("Extrayendo el texto", 645)
    d.progress.avanzar("Extrayendo el texto", 300, 645)
    QApplication.processEvents()
    assert not d.progress.isHidden() and d.progress.value() == 300
    d.close()
    QApplication.processEvents()


def test_aviso_distingue_cerrar_de_cancelar(app):
    """
    La ✕ NO es el segundo botón: quien cierra el aviso quiere dejarlo,
    no seguir por el otro camino (bucle del OCR, 2026-07-28).
    """
    from app import gui

    estado: dict = {}
    aviso = gui.GredosMessageBox(
        None, "Título", "Texto", cancellable=True,
        accept_text="Sí", cancel_text="No", estado=estado,
    )
    assert estado["salida"] == "cerrar"       # por defecto: ni una cosa ni otra
    aviso._cancelar()
    assert estado["salida"] == "cancelar"
    QApplication.processEvents()

    estado2: dict = {}
    otro = gui.GredosMessageBox(
        None, "Título", "Texto", cancellable=True, estado=estado2
    )
    otro._aceptar()
    assert estado2["salida"] == "aceptar"
    QApplication.processEvents()


def test_ocr_sin_rescate_no_se_ofrece_dos_veces(app, db, monkeypatch):
    """
    Un reconocimiento que no saca texto dejaba `paginas_ocr` vacío y la
    ventana volvía a ofrecer lo mismo sin parar (2026-07-28).
    """
    from app import gui, pdftext

    analisis = pdftext.Analisis(archivo="x.pdf", sha1="0")
    analisis.paginas_pdf = 645
    analisis.estado = "nativo"
    analisis.palabras = 90_000
    analisis.paginas_sin_texto = [1, 604, 605]
    analisis.ocr_intentado = True             # ya se intentó, sin suerte
    analisis.ocr_fallo = "son láminas o páginas en blanco"

    assert analisis.completable_con_ocr       # sigue faltándole texto…
    ofrecidos = []
    avisos = []
    monkeypatch.setattr(
        gui.GredosMessageBox, "show_info",
        staticmethod(lambda *a, **k: avisos.append(a)),
    )
    d = _abrir(gui.TextosDialog(db))
    monkeypatch.setattr(
        d, "_ofrecer_ocr", lambda res: ofrecidos.append(res) or "seguir"
    )
    d._tomo_actual = d._tomos[0]
    d._fila_actual = 0
    d._ruta_actual = "x.pdf"
    monkeypatch.setattr(pdftext, "guardar", lambda *a: Path("x.jsonl"))
    d._analisis_listo(analisis, "")
    assert ofrecidos == []                    # …pero NO se vuelve a ofrecer
    d.close()
    QApplication.processEvents()


def test_analisis_cancelado_no_abre_ningun_aviso(app, db, monkeypatch):
    from app import gui

    avisos = []
    monkeypatch.setattr(
        gui.GredosMessageBox, "show_info",
        staticmethod(lambda *a, **k: avisos.append(a)),
    )
    d = _abrir(gui.TextosDialog(db))
    d._tomo_actual = d._tomos[0]
    d.progress.arrancar("Extrayendo el texto", 100)
    d._analisis_listo(None, gui.CANCELADO)
    assert avisos == []
    assert d.progress.isHidden()
    assert "cancelado" in d.lbl_status.text().lower()
    d.close()
    QApplication.processEvents()


# ----------------------------------------------------------------------
# Enlaces de las notificaciones (bcgmonitor://…)
# ----------------------------------------------------------------------
def test_enlace_de_notificacion_se_interpreta():
    from app import deeplink

    assert deeplink.enlace("historial") == "bcgmonitor://historial"
    assert deeplink.seccion_de(["bcgmonitor://historial"]) == "historial"
    assert deeplink.seccion_de(["bcgmonitor:precios"]) == "precios"
    # Destino desconocido: al menos, traer la ventana al frente
    assert deeplink.seccion_de(["bcgmonitor://loquesea"]) == "ventana"
    assert deeplink.seccion_de(["--tray"]) is None
    assert deeplink.seccion_de([]) is None


def test_el_resumen_del_dia_lleva_al_historial(app, db, monkeypatch):
    """
    Al pulsar el resumen del día, la aplicación sale de la bandeja y
    abre el Historial (2026-08-02).
    """
    from app.config import Config
    from app import gui

    avisos = []

    class _Notificador:
        def __init__(self, **_kw):
            pass

        def notify_info(self, titulo, cuerpo, link=None):
            avisos.append((titulo, cuerpo, link))

    monkeypatch.setattr(gui, "Notifier", _Notificador)
    config = Config()
    config.daily_summary_enabled = True
    config.daily_summary_time = "00:00"
    win = gui.MainWindow(config, db)
    db.set_meta("last_summary_date", "")
    win._maybe_daily_summary()
    QApplication.processEvents()

    assert avisos, "el resumen no se envió"
    titulo, cuerpo, link = avisos[0]
    assert "Resumen del día" in titulo
    assert link == "bcgmonitor://historial"
    assert "historial" in cuerpo.lower()
    win.close()


def test_abrir_seccion_saca_la_ventana_y_abre_el_dialogo(app, db):
    from app.config import Config
    from app import gui

    win = gui.MainWindow(Config(), db)
    abiertos = []
    win.open_history = lambda: abiertos.append("historial")
    win.open_lotes = lambda: abiertos.append("lotes")
    win.abrir_seccion("historial")
    win.abrir_seccion("lotes")
    win.abrir_seccion("ventana")             # solo trae la ventana al frente
    import time

    time.sleep(0.35)                    # los diálogos van con un respiro
    QApplication.processEvents()
    # El orden entre ellos da igual: lo que importa es que cada sección
    # abra SU ventana y que "ventana" no abra ninguna.
    assert sorted(abiertos) == ["historial", "lotes"]
    assert win.isVisible()
    win.close()


# ----------------------------------------------------------------------
# Buscador dentro del texto de los tomos (RAG-2 / RAG-3)
# ----------------------------------------------------------------------
@pytest.fixture()
def indice_falso(tmp_path, monkeypatch):
    """
    Un índice de dos tomos en un directorio temporal.

    NUNCA el real: son 250 MB del usuario y una prueba no debe tocarlo.
    """
    import json

    from app import rag

    textos = tmp_path / "TextosTomos"
    textos.mkdir()
    monkeypatch.setattr(rag, "TEXTOS_DIR", textos)
    def hoja(pdf, cuerpo, **extra):
        reg = {"tipo": "pagina", "pdf": pdf, "impresa": None,
               "seccion": "texto", "titulo": "", "obra": "", "versos": [],
               "capitulos": [], "llamadas": [], "notas": "", "cuerpo": cuerpo}
        reg.update(extra)
        return reg

    def escribe(nombre, cabecera, hojas):
        with (textos / nombre).open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(cabecera, ensure_ascii=False) + "\n")
            for reg in hojas:
                fh.write(json.dumps(reg, ensure_ascii=False) + "\n")

    escribe(
        "150 - Homero - Ilíada.jsonl",
        {"tipo": "tomo", "orden": 150, "numero": "150", "autor": "Homero",
         "obras": "Ilíada", "canonico": "Homero — Ilíada",
         "indice_nombres": {"Aquiles": ["I 1", "XXII 330"]}},
        [hoja(10, "Canta, oh diosa, la cólera del Pélida Aquiles.",
              impresa=23, obra="Canto I",
              notas="Nota del traductor sobre el epíteto.")],
    )
    # Un segundo tomo con la misma palabra: sin él no se puede probar
    # que la lista de tomos salga COMPLETA.
    escribe(
        "075 - Jenofonte - Obras menores.jsonl",
        {"tipo": "tomo", "orden": 75, "numero": "75", "autor": "Jenofonte",
         "obras": "Obras menores", "canonico": "Jenofonte — Obras menores",
         "indice_nombres": {}},
        [hoja(4, "Recordaba el ejemplo de Aquiles ante los lacedemonios.",
              impresa=88, obra="Agesilao")],
    )

    indice = rag.Indice(tmp_path / "textos.db")
    indice.indexar()
    monkeypatch.setattr(rag, "indice_compartido", lambda: indice)
    yield indice
    indice.close()


def test_buscar_textos_encuentra_y_cita(app, indice_falso):
    """La lista de resultados son TOMOS, con su recuento."""
    from app import gui

    d = _abrir(gui.BuscarTextosDialog("cólera"))
    QApplication.processEvents()
    assert d.table.rowCount() == 1
    assert "Homero — Ilíada" in d.table.item(0, 0).text()
    assert "1 pasaje(s)" in d.table.item(0, 1).text()
    d.close()
    QApplication.processEvents()


def test_la_ventana_del_tomo_lee_el_pasaje(app, indice_falso):
    """
    Los pasajes de un tomo se leen en SU ventana, no desplegados en la
    tabla de búsqueda: ahí la localización y el texto quedaban cortados
    en una línea (2026-08-05). Basta con seleccionar para leer la
    página entera.
    """
    from app import gui

    w = _abrir(gui.PasajesDeTomoDialog(
        indice_falso, "Homero — Ilíada", "cólera", incluir_notas=False,
    ))
    QApplication.processEvents()
    assert w.table.rowCount() == 1
    # La cita SIEMPRE con la página impresa, que es la del libro
    assert "pág. 23" in w.table.item(0, 0).text()
    # Y al seleccionar, abajo sale la página entera
    assert "página 23" in w.lbl_donde.text()
    # La coincidencia está a media página: no hay contexto que perder,
    # así que la página va a la IZQUIERDA del pliego.
    assert "cólera" in w.visor_izq.toPlainText()
    assert "1 pasaje(s)" in w.lbl_info.text()
    w.close()
    QApplication.processEvents()


def test_la_ventana_del_tomo_sin_coincidencias(app, indice_falso):
    from app import gui

    w = _abrir(gui.PasajesDeTomoDialog(
        indice_falso, "Homero — Ilíada", "trirreme",
    ))
    QApplication.processEvents()
    assert w.table.rowCount() == 0
    assert "Ningún pasaje" in w.lbl_info.text()
    assert w.visor_izq.toPlainText() == ""
    assert w.visor_der.toPlainText() == ""
    w.close()
    QApplication.processEvents()


def test_buscar_textos_lista_todos_los_tomos(app, indice_falso):
    """
    REGRESIÓN: pidiendo pasajes sueltos había un tope de candidatos y
    los tomos que caían fuera no salían nunca (buscando «lacedemonios»
    faltaban las obras menores de Jenofonte). La vista por tomos es
    completa.
    """
    from app import gui

    d = _abrir(gui.BuscarTextosDialog("Aquiles"))
    QApplication.processEvents()
    tomos = [
        d.table.item(f, 0).data(Qt.ItemDataRole.UserRole)[1]
        for f in range(d.table.rowCount())
        if d.table.item(f, 0).data(Qt.ItemDataRole.UserRole)[0] == "tomo"
    ]
    assert set(tomos) == {"Homero — Ilíada", "Jenofonte — Obras menores"}
    d.close()
    QApplication.processEvents()


def test_el_doble_clic_abre_la_ventana_del_tomo(app, indice_falso, monkeypatch):
    """
    Con muchos tomos no se carga el texto de ninguno hasta el doble
    clic: era el motivo de agrupar por tomo.
    """
    from app import gui

    abiertos = []
    monkeypatch.setattr(
        gui.BuscarTextosDialog, "abrir_tomo",
        lambda self, canonico: abiertos.append(canonico),
    )
    d = _abrir(gui.BuscarTextosDialog("Aquiles"))
    QApplication.processEvents()
    fila = d._fila_del_tomo("Homero — Ilíada")
    assert fila is not None
    d._doble_clic(fila, 0)
    assert abiertos == ["Homero — Ilíada"]
    d.close()
    QApplication.processEvents()


def test_buscar_textos_notas_solo_si_se_piden(app, indice_falso):
    from app import gui

    d = _abrir(gui.BuscarTextosDialog("epíteto"))
    QApplication.processEvents()
    assert d.table.rowCount() == 0          # por defecto, sin notas
    d.cb_notas.setChecked(True)
    QApplication.processEvents()
    assert d.table.rowCount() == 1          # el tomo que la lleva
    assert "1 pasaje(s)" in d.table.item(0, 1).text()
    d.close()
    QApplication.processEvents()


def test_buscar_textos_sin_consulta_no_lista_nada(app, indice_falso):
    from app import gui

    d = _abrir(gui.BuscarTextosDialog(""))
    QApplication.processEvents()
    assert d.table.rowCount() == 0
    assert d.tbl_nombres.isHidden()
    assert "tomos" in d.lbl_status.text()      # el recuento del índice
    d.close()
    QApplication.processEvents()


def test_pasaje_muestra_la_hoja_entera(app, indice_falso):
    from app import gui

    hoja = indice_falso.hoja_completa("Homero — Ilíada", 10)
    assert "Tomo 150" in gui.localizacion_de_hoja(hoja)
    assert "página 23" in gui.localizacion_de_hoja(hoja)
    html = gui.html_de_hoja(hoja, "cólera")
    assert "cólera" in html and "epíteto" in html      # cuerpo y notas
    assert "NOTAS" in html                             # las notas, al pie


def test_la_ventana_principal_abre_el_buscador_de_textos(app, db, monkeypatch):
    from app.config import Config
    from app import gui

    abiertos = []
    monkeypatch.setattr(gui.MainWindow, "open_buscar_textos",
                        lambda self: abiertos.append(self.ed_textos.text()))
    win = gui.MainWindow(Config(), db)
    win.ed_textos.setText("los lacedemonios")
    win.ed_textos.returnPressed.emit()
    QApplication.processEvents()
    assert abiertos == ["los lacedemonios"]
    win.close()


# ----------------------------------------------------------------------
# Tipografía de la aplicación
# ----------------------------------------------------------------------
def test_la_fuente_es_georgia_con_reservas(app):
    """
    Georgia en toda la aplicación, con dos reservas MEDIDAS: Georgia no
    trae griego politónico (ἀ ᾳ ῥ ὧ), que abunda en el corpus, ni los
    glifos de la propia interfaz (▸ ▾ ⧉ ✔).
    """
    from app import gui

    f = gui.fuente(11)
    assert f.family() == "Georgia"
    assert f.families()[0] == "Georgia"
    assert "Palatino Linotype" in f.families()      # griego politónico
    assert "Segoe UI Symbol" in f.families()        # ▸ ▾ ⧉ ✔
    assert "Georgia" in gui.FONT_STACK


def test_la_fuente_va_suavizada(app):
    """
    Sin `PreferNoHinting`, Windows deforma cada letra para encajarla en
    la rejilla de píxeles: en una serif de remates finos eso se ve como
    trazos desiguales.
    """
    from PySide6.QtGui import QFont
    from app import gui

    f = gui.fuente()
    assert f.hintingPreference() == QFont.HintingPreference.PreferNoHinting
    assert f.styleStrategy() & QFont.StyleStrategy.PreferAntialias
    assert f.styleStrategy() & QFont.StyleStrategy.PreferQuality


def test_la_fuente_admite_lo_que_hace_falta(app):
    """Español completo, superíndices de nota y comillas latinas."""
    from PySide6.QtGui import QRawFont
    from app import gui

    if _sin_fuentes_del_sistema():
        pytest.skip("sin las fuentes de Windows en este entorno")
    raw = QRawFont.fromFont(gui.fuente(12))
    for car in "áéíóúÁÉÍÓÚñÑüÜ¿¡«»—…ªº¹²³⁴⁵⁶⁷⁸⁹":
        assert raw.supportsCharacter(ord(car)), f"Georgia no tiene {car!r}"


def test_buscar_textos_ordena_alfabeticamente(app, indice_falso):
    """
    Por el canónico, que empieza por el autor, y SIN tildes: si no,
    «Ésquilo» acabaría detrás de «Zenón».
    """
    from app import gui

    d = _abrir(gui.BuscarTextosDialog("Aquiles"))
    QApplication.processEvents()
    assert d.cmb_orden.currentText() == "Alfabético"
    tomos = [
        d.table.item(f, 0).data(Qt.ItemDataRole.UserRole)[1]
        for f in range(d.table.rowCount())
        if d.table.item(f, 0).data(Qt.ItemDataRole.UserRole)[0] == "tomo"
    ]
    assert tomos == ["Homero — Ilíada", "Jenofonte — Obras menores"]

    # Y el otro criterio sigue a mano: primero donde más sale
    d.cmb_orden.setCurrentText("Más coincidencias")
    QApplication.processEvents()
    cuentas = [
        t["pasajes"] for t in d._tomos_hit
    ]
    assert cuentas == sorted(cuentas, reverse=True)
    d.close()
    QApplication.processEvents()


# ----------------------------------------------------------------------
# Griego: Georgia no tiene politónico
# ----------------------------------------------------------------------
def _sin_fuentes_del_sistema() -> bool:
    """
    Sin pantalla, Qt no carga las fuentes de Windows: `QTextLayout` da
    un fallo de acceso y `QRawFont` miente. Estas pruebas solo valen
    con las fuentes de verdad.
    """
    from PySide6.QtGui import QFontDatabase

    return "Georgia" not in QFontDatabase.families()


def test_georgia_no_tiene_griego_politonico(app):
    """
    El dato que obliga a todo lo demás. Georgia trae el griego MODERNO
    (λόγος) pero no el politónico (ἀ ᾳ ῥ ὧ), que es el de los tomos.
    """
    from PySide6.QtGui import QFont, QRawFont
    from app import gui

    if _sin_fuentes_del_sistema():
        pytest.skip("sin las fuentes de Windows en este entorno")
    raw = QRawFont.fromFont(QFont("Georgia", 12))
    for car in "λόγοςΑΩ":                       # moderno: sí lo tiene
        assert raw.supportsCharacter(ord(car))
    for car in "ἀᾳῥὧ":                          # politónico: no
        assert not raw.supportsCharacter(ord(car))
    # Por eso hay una fuente aparte, con politónico completo
    griega = QRawFont.fromFont(gui.fuente_griega(12))
    for car in "ἀᾳῥὧλόγος":
        assert griega.supportsCharacter(ord(car))


def test_una_palabra_griega_no_se_parte_en_dos_tipografias(app):
    """
    REGRESIÓN: dejando que Qt resolviera letra a letra, «ἀλλήλων» salía
    con la ἀ de Palatino y «λλήλων» de Georgia — dos tipografías dentro
    de la misma palabra. Cuatro de cada cinco palabras politónicas.
    """
    from PySide6.QtGui import QTextLayout
    from app import gui

    if _sin_fuentes_del_sistema():
        pytest.skip("sin las fuentes de Windows en este entorno")

    def familias(texto, f):
        lay = QTextLayout(texto, f)
        lay.beginLayout()
        lay.createLine()
        lay.endLayout()
        return {r.rawFont().familyName() for r in lay.glyphRuns()}

    for palabra in ("ἀλλήλων", "ἀμφότεροι", "πρᾶξις", "ὧδε", "λόγος"):
        assert len(familias(palabra, gui.fuente_griega(12))) == 1


def test_parte_el_texto_por_rachas_griegas(app):
    from app import gui

    partes = gui.partir_por_griego("Los ἀλλήλων de Aristóteles")
    assert partes == [
        ("Los ", False), ("ἀλλήλων", True), (" de Aristóteles", False),
    ]
    # Una racha se lleva lo que va ENTRE letras griegas (comas, espacios)
    assert gui.partir_por_griego("el πρᾶξις, ὧδε, aparece")[1] == (
        "πρᾶξις, ὧδε", True
    )
    # Sin griego, un solo trozo
    assert gui.partir_por_griego("nada de griego") == [
        ("nada de griego", False)
    ]
    assert gui.partir_por_griego("") == []


def test_detecta_un_pasaje_griego(app):
    from app import gui

    assert gui.es_griego("ἀλλήλων καὶ λόγος")
    assert gui.es_griego("el λόγος de Platón")
    assert not gui.es_griego("Aristóteles dice")
    assert not gui.es_griego("123 · 456")


def test_el_visor_pinta_el_griego_con_su_fuente(app):
    from app import gui

    hoja = {
        "canonico": "Aristóteles — Retórica", "numero": "142", "hoja": 5,
        "impresa": 88, "obra": "Libro I", "versos": "",
        "cuerpo": "Los ἀλλήλων de la πρᾶξις.", "notas": "",
    }
    html = gui.html_de_hoja(hoja, "πρᾶξις")
    assert gui.FUENTE_GRIEGA in html
    # El resaltado sigue funcionando SOBRE el griego
    assert gui.ORO_MARCA in html
    # …y el castellano no se toca
    assert "Los " in html


# ----------------------------------------------------------------------
# Luz que sigue al ratón: bordes de ventana y de los campos de texto
# ----------------------------------------------------------------------
def test_un_solo_rastreador_de_cursor_y_se_apaga(app, db):
    """
    Un temporizador por campo de texto sería tirar CPU: hay UNO para
    toda la aplicación, y solo corre mientras alguien escucha.
    """
    from app.config import Config
    from app import gui

    r = gui.rastreador_de_cursor()
    assert r is gui.rastreador_de_cursor()          # siempre el mismo
    antes = r._oyentes
    win = gui.MainWindow(Config(), db)
    win.show()
    QApplication.processEvents()
    assert r._timer.isActive()
    assert r._oyentes > antes
    win.close()
    QApplication.processEvents()
    assert r._oyentes == antes
    if antes == 0:
        assert not r._timer.isActive()      # sin nadie mirando, nada vivo


def test_el_borde_se_enciende_solo_cerca_del_raton(app, db):
    from PySide6.QtCore import QPoint
    from app.config import Config
    from app import gui

    win = gui.MainWindow(Config(), db)
    win.show()
    QApplication.processEvents()
    marco = win.findChild(gui.LeatherFrame)
    assert marco is not None
    assert marco._cerca is None
    marco._apuntar_cursor(marco.mapToGlobal(QPoint(40, 40)))
    assert marco._cerca is not None                  # el ratón encima
    marco._apuntar_cursor(QPoint(-9000, -9000))
    assert marco._cerca is None                      # lejos, apagado
    win.close()
    QApplication.processEvents()


def test_los_campos_de_texto_llevan_la_misma_luz(app, db):
    """
    Todos los campos son `GlowLineEdit`: si alguno se crea con
    `QLineEdit` a secas, se queda sin la luz y canta.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLineEdit
    from app.config import Config
    from app import gui

    for dialogo in (gui.ConfigDialog(Config()), gui.CollectionDialog(db),
                    gui.HistoryDialog(db), gui.TomoDialog(db, 2)):
        dialogo.show()
        QApplication.processEvents()
        campos = dialogo.findChildren(QLineEdit)
        # Los que trae Qt por dentro (el editor de un QComboBox, p. ej.)
        # no cuentan: se miran los que crea la aplicación.
        propios = [c for c in campos if c.parent() is not None
                   and not c.objectName().startswith("qt_")]
        assert propios, type(dialogo).__name__
        assert all(isinstance(c, gui.GlowLineEdit) for c in propios), \
            f"{type(dialogo).__name__}: hay QLineEdit sin luz"
        uno = propios[0]
        uno._apuntar_cursor(uno.mapToGlobal(QPoint(5, 5)))
        assert uno._cerca is not None
        dialogo.close()
        QApplication.processEvents()


def test_con_el_foco_puesto_el_campo_no_se_repinta_encima(app):
    """
    Con el foco, el QSS ya pinta el filete en oro vivo: superponer la
    luz solo lo emborronaría.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap
    from app import gui

    campo = gui.GlowLineEdit()
    campo.show()
    QApplication.processEvents()
    campo._apuntar_cursor(campo.mapToGlobal(QPoint(5, 5)))
    campo.setFocus()
    QApplication.processEvents()
    campo.render(QPixmap(campo.size()))       # no debe reventar
    campo.close()


# ----------------------------------------------------------------------
# Composición de la página: texto de libro, no volcado del PDF
# ----------------------------------------------------------------------
def test_la_prosa_se_recompone_en_parrafos(app):
    """
    El texto guardado es FIEL al PDF: renglones cortados a lo ancho de
    la caja. Volcarlo así se lee fatal; hay que rehacer el párrafo.
    """
    from app import gui

    crudo = (
        "Silosón regresó, según dicen, junto a Darío y le recordó su\n"
        "abrigo y le pidió a cambio Samos; luego Darío se enorgullecía\n"
        "de ello, porque creía que había devuelto algo grande.\n"
    )
    bloques, marcas = gui.componer_pagina(crudo)
    assert marcas == []
    assert len(bloques) == 1
    tipo, texto = bloques[0]
    assert tipo == "parrafo"
    assert "su abrigo" in texto          # el renglón partido, ya unido
    assert "\n" not in texto


def test_las_referencias_del_margen_salen_del_texto(app):
    """
    «b», «c», «403d», «9» son la paginación del editor: en el libro van
    al margen, no en mitad de la frase (así salían, 2026-08-08).
    """
    from app import gui

    crudo = "b\nc\n403d\ne\n9\nA Alipio, hermano de Cesareo\nSilosón regresó."
    bloques, marcas = gui.componer_pagina(crudo)
    assert marcas == ["b", "c", "403d", "e", "9"]
    tipos = [t for t, _ in bloques]
    assert "titulo" in tipos             # el encabezado de la carta, aparte
    titulo = next(x for t, x in bloques if t == "titulo")
    assert titulo == "A Alipio, hermano de Cesareo"
    assert all("403d" not in x for _t, x in bloques)


def test_el_verso_no_se_junta(app):
    """En los poetas el renglón ES el verso: unirlo sería destruirlo."""
    from app import gui

    poema = (
        "Canta, oh diosa, la cólera del Pélida Aquiles\n"
        "maldita, que causó a los aqueos incontables dolores\n"
        "y precipitó al Hades muchas valientes vidas\n"
        "de héroes, y a ellos mismos los hizo presa de perros\n"
    )
    bloques, _marcas = gui.componer_pagina(poema)
    assert [t for t, _ in bloques] == ["verso"] * 4
    assert bloques[0][1].startswith("Canta, oh diosa")


def test_quita_el_espacio_antes_de_la_coma(app):
    from app import gui

    bloques, _ = gui.componer_pagina("A Alipio , hermano de Cesareo.")
    assert bloques[0][1] == "A Alipio, hermano de Cesareo."


def test_las_notas_van_al_pie_con_su_numero(app):
    from app import gui

    hoja = {
        "canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
        "obra": "", "versos": "", "cuerpo": "El texto del autor.",
        "notas": "61 Deuteronomio 32, 9.\n62 Véase la introducción.",
    }
    assert gui._partir_notas(hoja["notas"]) == [
        ("61", "Deuteronomio 32, 9."), ("62", "Véase la introducción."),
    ]
    html = gui.html_de_hoja(hoja, "")
    assert "NOTAS" in html
    assert "<sup" in html and ">61</sup>" in html


def test_la_ventana_del_tomo_no_abre_otra_encima(app, indice_falso):
    """
    Se quitaron «Ver a solas» y el doble clic: abrir otra ventana encima
    no aportaba nada (2026-08-08).
    """
    from PySide6.QtWidgets import QPushButton
    from app import gui

    w = _abrir(gui.PasajesDeTomoDialog(
        indice_falso, "Homero — Ilíada", "cólera", incluir_notas=False,
    ))
    botones = [
        b.text() for b in w.findChildren(QPushButton)
        if b.text() and b.objectName() not in ("winbtn", "winbtn_close")
    ]
    assert botones == ["Cerrar"]     # «Copiar cita» y «Ver a solas», retirados
    assert not hasattr(w, "_abrir_a_solas")
    w.close()
    QApplication.processEvents()


def test_cada_lista_de_resultados_lleva_su_titulo(app, indice_falso):
    """Eran dos tablas sin rótulo y no se sabía cuál era cuál."""
    from app import gui

    d = _abrir(gui.BuscarTextosDialog("Aquiles"))
    QApplication.processEvents()
    assert not d.lbl_tomos.isHidden()
    assert "EN EL TEXTO DE LOS TOMOS" in d.lbl_tomos.text()
    assert "ÍNDICE DE NOMBRES" in d.lbl_nombres.text()
    d.close()
    QApplication.processEvents()


# ----------------------------------------------------------------------
# Pliego: dos páginas, como un libro abierto (2026-08-08)
# ----------------------------------------------------------------------
def test_el_pliego_pone_la_par_a_la_izquierda(app, indice_falso):
    """
    El lado se elige por dónde cae la coincidencia, no por la paridad:
    perder el contexto se nota al leer, y que la página caiga a un lado
    o al otro, no (2026-08-08).
    """
    from app import gui

    w = _abrir(gui.PasajesDeTomoDialog(
        indice_falso, "Homero — Ilíada", "cólera", incluir_notas=False,
    ))
    # Coincidencia arriba del todo: la página va a la DERECHA y a la
    # izquierda queda la anterior, para no leer sin saber de qué venía.
    arriba = {"hoja": 10, "cuerpo": "cólera al principio. " + "x " * 200}
    assert w._donde_cae(arriba) < 0.1
    assert w._pliego(arriba) == (9, 10)
    # Coincidencia abajo: ya trae su contexto delante
    abajo = {"hoja": 10, "cuerpo": "x " * 200 + "y la cólera al final."}
    assert w._donde_cae(abajo) > 0.5
    assert w._pliego(abajo) == (10, 11)
    # La primera hoja del tomo no tiene anterior
    assert w._pliego({"hoja": 1, "cuerpo": "cólera y más"}) == (1, 2)
    w.close()
    QApplication.processEvents()


def test_el_pliego_ensena_las_dos_paginas(app, indice_falso):
    """
    Se muestran SIEMPRE las dos, aunque el pasaje esté solo en una; si
    del otro lado no hay nada, esa página va en blanco.
    """
    from app import gui

    w = _abrir(gui.PasajesDeTomoDialog(
        indice_falso, "Homero — Ilíada", "cólera", incluir_notas=False,
    ))
    QApplication.processEvents()
    izq = w.visor_izq.toPlainText()
    der = w.visor_der.toPlainText()
    # La coincidencia cae a media página → esta va a la izquierda…
    assert "cólera" in izq
    # …y a la derecha la siguiente, que en este corpus no existe: la
    # página se enseña igual, en blanco.
    assert "no hay más páginas" in der
    # El rótulo dice de qué página es cada lado
    assert "página 23" in w.lbl_donde.text()
    w.close()
    QApplication.processEvents()


# ----------------------------------------------------------------------
# Realce de lo buscado: más grande y con destello dorado (2026-08-08)
# ----------------------------------------------------------------------
def test_lo_buscado_se_realza_mas_grande(app):
    """
    Con el mismo cuerpo y una veladura floja había que rastrear la
    palabra por el párrafo.
    """
    from app import gui

    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "",
            "cuerpo": "Y llegaron a las Termópilas al caer la tarde."}
    html = gui.html_de_hoja(hoja, "termópilas")
    assert f"font-size:{gui._CUERPO_MARCA}pt" in html
    assert gui._CUERPO_MARCA > gui._CUERPO_PAGINA
    assert gui.ORO_MARCA in html


def test_el_halo_respira_y_no_toca_el_documento(app):
    """
    La animación se PINTA ENCIMA. Antes se probaron un foco que
    recorría las letras y un filete subrayado, y el usuario descartó
    los dos (2026-08-08); ahora es un halo con estrellas, y el
    documento no se toca en ningún momento.
    """
    from PySide6.QtGui import QTextCursor
    from app import gui

    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "",
            "cuerpo": "Y llegaron a las Termópilas al caer la tarde."}
    visor = gui.VisorDePagina()
    visor.resize(560, 200)
    visor.show()
    QApplication.processEvents()
    visor.mostrar(gui.html_de_hoja(hoja, "termópilas"), "termópilas")
    inicio, largo = visor._marcas[0]
    assert visor.toPlainText()[inicio:inicio + largo] == "Termópilas"

    # El halo se abre y se cierra
    visor._fase = 0.0
    assert visor.brillo() < 0.05
    visor._fase = 0.5
    assert visor.brillo() > 0.95

    # Y el texto sigue SIN subrayar: el formato del documento no cambia
    cursor = QTextCursor(visor.document())
    cursor.setPosition(inicio)
    cursor.setPosition(inicio + 1, QTextCursor.MoveMode.KeepAnchor)
    assert not cursor.charFormat().fontUnderline()
    visor.close()


def test_el_halo_cae_justo_sobre_la_palabra(app):
    """
    Si el rectángulo no coincide con el texto, el halo y las estrellas
    salen desplazados.
    """
    from PySide6.QtGui import QFontMetricsF
    from app import gui

    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "",
            "cuerpo": "Y llegaron a las Termópilas al caer la tarde."}
    visor = gui.VisorDePagina()
    visor.resize(560, 200)
    visor.show()
    QApplication.processEvents()
    visor.mostrar(gui.html_de_hoja(hoja, "termópilas"), "termópilas")
    rects = visor._rectangulos()
    assert len(rects) == 1
    # En NEGRITA, que es como va el realce: en redonda mide 15 px menos
    ancho = QFontMetricsF(
        gui.fuente(gui._CUERPO_MARCA, negrita=True)
    ).horizontalAdvance("Termópilas")
    assert abs(rects[0].width() - ancho) < 8
    visor.close()


def test_la_animacion_se_ve(app):
    """Entre apagado y encendido tiene que cambiar lo pintado."""
    from PySide6.QtGui import QImage
    from app import gui

    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "",
            "cuerpo": "Y llegaron a las Termópilas al caer la tarde."}
    visor = gui.VisorDePagina()
    visor.resize(560, 200)
    visor.show()
    QApplication.processEvents()
    visor.mostrar(gui.html_de_hoja(hoja, "termópilas"), "termópilas")

    def foto() -> QImage:
        img = QImage(visor.viewport().size(), QImage.Format.Format_ARGB32)
        img.fill(0)
        visor.viewport().render(img)
        return img

    visor._fase = 0.0
    apagado = foto()
    visor._fase = 0.5
    encendido = foto()
    distintos = sum(
        1 for y in range(0, apagado.height(), 3)
        for x in range(0, apagado.width(), 3)
        if apagado.pixel(x, y) != encendido.pixel(x, y)
    )
    assert distintos > 40, "no se aprecia la animación"
    visor.close()


def test_el_destello_se_para_con_la_pagina_oculta(app):
    from app import gui

    visor = gui.VisorDePagina()
    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "", "cuerpo": "las Termópilas."}
    visor.show()
    QApplication.processEvents()
    visor.mostrar(gui.html_de_hoja(hoja, "termópilas"), "termópilas")
    assert visor._timer.isActive()
    visor.hide()
    QApplication.processEvents()
    assert not visor._timer.isActive()      # oculta no se anima
    # Sin nada que buscar, tampoco
    visor.show()
    visor.mostrar(gui.html_de_hoja(hoja, ""), "")
    assert visor._marcas == [] and not visor._timer.isActive()
    visor.close()


def test_el_realce_griego_no_apila_dos_tamanos(app):
    """
    REGRESIÓN (2026-08-08): si la coincidencia ERA griega, el estilo
    llevaba dos `font-size` —el del griego y el del realce— y ganaba el
    último: Palatino perdía su compensación, se salía de la línea y se
    comía los acentos.
    """
    from app import gui

    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "",
            "cuerpo": "el λόγος de Platón y la cólera de Aquiles"}
    for consulta in ("λόγος", "cólera"):
        html = gui.html_de_hoja(hoja, consulta)
        for span in html.split('<span style="')[1:]:
            estilo = span.split('"')[0]
            assert estilo.count("font-size") <= 1, estilo
    # Y el griego marcado conserva su escala: la del realce, reducida
    html = gui.html_de_hoja(hoja, "λόγος")
    esperado = round(gui._CUERPO_MARCA * gui._ESCALA_GRIEGA, 1)
    assert f"font-size:{esperado}pt" in html


def test_el_realce_es_un_salto_pequeno(app):
    """Con 12,5 pt la tilde quedaba cortada por arriba."""
    from app import gui

    assert gui._CUERPO_MARCA - gui._CUERPO_PAGINA <= 0.6


def test_la_pagina_izquierda_empieza_en_frase(app):
    from app import gui

    bloques = [("parrafo", "taba diciendo. Y entonces llegaron a la ciudad.")]
    assert gui._empieza_en_frase(bloques) == [
        ("parrafo", "Y entonces llegaron a la ciudad.")
    ]
    # Si empezar en frase costara media página, se deja como está
    caro = [("parrafo", "x" * 200 + ". Y llegaron.")]
    assert gui._empieza_en_frase(caro) == caro
    # Un rótulo ya es un buen comienzo
    con_titulo = [("titulo", "A Alipio"), ("parrafo", "y entonces.")]
    assert gui._empieza_en_frase(con_titulo) == con_titulo


def test_la_pagina_derecha_acaba_en_punto(app):
    from app import gui

    bloques = [("parrafo", "Llegaron a la ciudad. Y al día siguiente par")]
    assert gui._acaba_en_frase(bloques) == [
        ("parrafo", "Llegaron a la ciudad.")
    ]
    # Ya cerrada, no se toca
    cerrada = [("parrafo", "Llegaron a la ciudad.")]
    assert gui._acaba_en_frase(cerrada) == cerrada
    # Una página que es una sola frase larguísima no se queda en nada
    larga = [("parrafo", "Sí. " + "x" * 300)]
    assert gui._acaba_en_frase(larga) == larga
    # En verso no se parte el renglón: se descartan renglones enteros
    verso = [("verso", "canta, oh diosa, la cólera."), ("verso", "y el héroe")]
    assert gui._acaba_en_frase(verso) == [("verso", "canta, oh diosa, la cólera.")]


def test_las_marcas_de_margen_no_pasan_por_notas(app):
    """
    El analizador dejaba caer «b c d e 94a» en las notas y salían al pie
    como si fueran una nota de algo.
    """
    from app import formato, gui

    assert formato.es_ruido_de_margen("b c d e 94a")
    assert formato.es_ruido_de_margen("20C")
    assert not formato.es_ruido_de_margen("Deuteronomio 32, 9.")
    assert gui._partir_notas("b c d e 94a") == []
    assert gui._partir_notas("61 Deuteronomio 32, 9.") == [
        ("61", "Deuteronomio 32, 9.")
    ]


def test_las_estrellas_nacen_en_los_bordes(app):
    """
    Cuatro de cada cinco salen en los BORDES de lo resaltado: ahí
    enmarcan la palabra sin ponerse encima de las letras, que es donde
    estorbarían para leer.
    """
    from app import gui

    sitios = [gui.VisorDePagina._sitio()["x"] for _ in range(2000)]
    bordes = sum(1 for x in sitios if x < 0.15 or x > 0.85)
    assert 0.72 < bordes / len(sitios) < 0.88
    # Y no se salen más de un pelín de la palabra
    assert -0.06 < min(sitios) and max(sitios) < 1.06


def test_las_estrellas_son_al_azar_pero_no_tiemblan(app):
    """
    El sitio se sortea SOLO cuando la estrella se apaga y vuelve a
    nacer. Sorteándolo en cada fotograma, en vez de brillar temblarían.
    """
    from app import gui

    hoja = {"canonico": "X", "numero": "1", "hoja": 5, "impresa": 10,
            "obra": "", "versos": "", "notas": "",
            "cuerpo": "Y llegaron a las Termópilas al caer la tarde."}
    visor = gui.VisorDePagina()
    visor.resize(560, 200)
    visor.show()
    QApplication.processEvents()
    visor.mostrar(gui.html_de_hoja(hoja, "termópilas"), "termópilas")
    assert visor._estrellas, "no se sembró ninguna estrella"

    # Recién nacidas, para que ninguna acabe su vida en estos dos ticks
    for estrella in visor._estrellas:
        estrella["fase"] = 0.1
    sitios = [(e["x"], e["y"]) for e in visor._estrellas]
    visor._latir()
    visor._latir()
    assert [(e["x"], e["y"]) for e in visor._estrellas] == sitios
    # …y tras una vida entera, cambian
    for _ in range(80):
        visor._latir()
    assert [(e["x"], e["y"]) for e in visor._estrellas] != sitios

    # Dos visores distintos no reparten igual: es azar de verdad
    otro = gui.VisorDePagina()
    otro.resize(560, 200)
    otro.show()
    QApplication.processEvents()
    otro.mostrar(gui.html_de_hoja(hoja, "termópilas"), "termópilas")
    assert [(e["x"], e["y"]) for e in otro._estrellas] != sitios
    visor.close()
    otro.close()


# ----------------------------------------------------------------------
# Pasaje del día: botón ancho y su ventana (2026-08-08)
# ----------------------------------------------------------------------
def test_el_boton_del_dia_es_ancho_y_brilla(app, db):
    """
    Va SOLO y con aire: no es una acción más de la botonera, es una
    invitación a leer. Junta los dos brillos de la aplicación —la
    veladura de la fila seleccionada y las estrellas del resaltado—.
    """
    from app.config import Config
    from app import gui

    win = gui.MainWindow(Config(), db)
    win.show()
    QApplication.processEvents()
    boton = win.btn_dia
    assert isinstance(boton, gui.BotonDelDia)
    assert "PASAJE DEL DÍA" in boton.text()
    # Ocupa casi todo el ancho de la ventana
    assert boton.width() > win.width() * 0.8
    assert boton.height() >= gui.BotonDelDia._ALTO
    # Y tiene sus estrellas, animándose
    assert len(boton._estrellas) == gui.BotonDelDia._CUANTAS
    assert boton._timer.isActive()
    # Ninguna se mueve mientras vive: se sortea el sitio al NACER, no
    # en cada fotograma (si no, temblarían). Se parte de fases recién
    # nacidas para que ninguna acabe su vida en este tick.
    for estrella in boton._estrellas:
        estrella["fase"] = 0.1
    sitios = [(e["x"], e["y"]) for e in boton._estrellas]
    boton._latir()
    assert [(e["x"], e["y"]) for e in boton._estrellas] == sitios
    win.close()
    QApplication.processEvents()
    assert not boton._timer.isActive()      # oculto no se anima


def test_la_ventana_del_pasaje_del_dia(app, indice_falso):
    from app import gui

    ficha = indice_falso.pasaje_del_dia("2026-08-08")
    assert ficha, "el índice de prueba no dio pasaje del día"
    from PySide6.QtWidgets import QLabel

    d = _abrir(gui.PasajeDelDiaDialog(ficha))
    QApplication.processEvents()
    # El título arriba y la página entera debajo. El RESUMEN se retiró
    # el 2026-08-09: era una frase entresacada del pasaje que volvía a
    # salir unos renglones más abajo, dentro de la propia página, y se
    # leía como si anunciara algo que luego no era lo primero.
    rotulos = [w.text() for w in d.findChildren(QLabel) if w.text()]
    assert ficha["titulo"] in rotulos
    assert ficha["descripcion"] not in rotulos
    assert d.visor.toPlainText().strip()
    d.close()
    QApplication.processEvents()


def test_el_pasaje_del_dia_cambia_a_medianoche(app, indice_falso, monkeypatch):
    """
    El pasaje va por FECHA, así que a las doce cambia; pero la ventana
    puede quedarse abierta (la app vive en la bandeja), y entonces hay
    que cambiarlo en el sitio. Aquí se dispara el reloj a mano en vez de
    esperar a medianoche.
    """
    from datetime import datetime, timedelta

    from app import gui, rag

    monkeypatch.setattr(rag, "indice_compartido", lambda: indice_falso)
    d = _abrir(gui.PasajeDelDiaDialog(
        indice_falso.pasaje_del_dia("2026-08-08")))
    QApplication.processEvents()
    # El reloj apunta al primer segundo del día siguiente, no a un
    # sondeo cada minuto.
    assert d._reloj.isActive() and d._reloj.isSingleShot()
    # Se mide sobre `interval()` y rearmando aquí mismo, NO sobre
    # `remainingTime()`: sin un bucle de eventos en marcha —en las
    # pruebas no lo hay— Qt no refresca lo que queda, así que el reloj
    # se queda quieto mientras el de pared avanza y la resta salía casi
    # un segundo corta. Con la ventana de verdad no pasa.
    antes = datetime.now()
    d._armar_para_medianoche()
    objetivo = antes + timedelta(milliseconds=d._reloj.interval())
    medianoche = (antes + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    # Lo que de verdad importa: NUNCA antes de medianoche (si no,
    # `date.today()` seguiría dando el día de ayer), y por poco.
    assert medianoche < objetivo <= medianoche + timedelta(seconds=2)

    # Al saltar el día, trae el de hoy y se vuelve a armar.
    d._cambia_el_dia()
    QApplication.processEvents()
    hoy = indice_falso.pasaje_del_dia(datetime.now().date().isoformat())
    assert d.lbl_titulo.text() == (hoy.get("titulo") or "Pasaje del día")
    assert d._ficha["id"] == hoy["id"]
    assert d._reloj.isActive()
    d.close()
    QApplication.processEvents()


def test_sin_indice_el_boton_lo_dice_en_vez_de_fallar(app, db, monkeypatch):
    from app.config import Config
    from app import gui, rag

    monkeypatch.setattr(
        rag, "indice_compartido",
        lambda: (_ for _ in ()).throw(rag.RagError("sin FTS5")),
    )
    win = gui.MainWindow(Config(), db)
    win.open_pasaje_del_dia()
    assert "FTS5" in win.lbl_message.text()
    win.close()


def test_el_boton_del_dia_se_apaga_al_salir_el_raton(app, db):
    """
    REGRESIÓN (2026-08-08): `GlowButton.leaveEvent` solo apaga el
    calor, no borra la posición del ratón, así que el destello se
    quedaba clavado donde se sacó el cursor y el botón parecía
    encendido siempre.
    """
    from PySide6.QtCore import QEvent, QPointF
    from app.config import Config
    from app import gui

    win = gui.MainWindow(Config(), db)
    win.show()
    QApplication.processEvents()
    boton = win.btn_dia
    boton._mouse = QPointF(40, 10)
    boton._heat = 1.0
    boton.leaveEvent(QEvent(QEvent.Type.Leave))
    assert boton._mouse is None
    win.close()
    QApplication.processEvents()


def test_el_boton_del_dia_va_encima_de_la_botonera(app, db):
    from app.config import Config
    from app import gui

    win = gui.MainWindow(Config(), db)
    win.show()
    QApplication.processEvents()
    assert win.btn_dia.y() < win.btn_start.y()
    # Y la ventana da de sí para verlo todo
    # Medido: por debajo de 534x570 se recorta contenido
    assert win.height() >= 578 and win.width() >= 545
    win.close()
    QApplication.processEvents()


def test_el_pasaje_del_dia_empieza_y_acaba_en_frase(app, indice_falso):
    """
    La misma regla que el pliego del buscador: la página empieza tras
    un punto y cierra en punto.
    """
    from app import gui

    ficha = indice_falso.pasaje_del_dia("2026-08-08")
    assert ficha
    d = _abrir(gui.PasajeDelDiaDialog(ficha))
    QApplication.processEvents()
    texto = d.visor.toPlainText().strip()
    assert texto
    # Cierra en punto (o en lo que cierre una oración)
    assert texto.rstrip().endswith((".", "?", "!", "»", "…"))
    d.close()
    QApplication.processEvents()


def test_el_pasaje_del_dia_cierra_en_punto_y_aparte(app):
    """
    Más exigente que el pliego: aquí solo se enseña UNA página, así que
    el final del texto es el final de la lectura y tiene que ser el de
    un párrafo entero, no un punto en mitad de él.
    """
    from app import gui

    bloques = [
        ("parrafo", "Primero. Y sigue la cosa."),
        ("parrafo", "Segundo párrafo que se queda a med"),
    ]
    assert gui._acaba_en_parrafo(bloques) == [bloques[0]]
    # Si ya cierra, no se toca
    cerrado = [("parrafo", "Primero."), ("parrafo", "Segundo entero.")]
    assert gui._acaba_en_parrafo(cerrado) == cerrado
    # Y NO parte el párrafo por un punto de en medio, como sí hace la
    # regla del pliego
    medio = [("parrafo", "Una frase. Y otra que se corta aqu")]
    assert gui._acaba_en_parrafo(medio) == medio
    assert gui._acaba_en_frase(medio) == [("parrafo", "Una frase.")]


def test_mensaje_cuando_el_nombre_del_indice_no_lleva_a_ningun_tomo(
    app, indice_falso
):
    """
    Al pulsar un nombre del índice que no abre ningún tomo hay que
    distinguir DOS cosas; antes se decían igual —«la palabra no aparece
    en el texto indexado del tomo»— y encima podía ser falso.

    (1) La palabra está, pero solo en notas y el filtro la esconde.
    (2) No está: el índice de nombres de la BCG es común a toda la obra,
        así que la cita puede corresponder a otro volumen. Caso real:
        «mirmidones» en «Ovidio — Metamorfosis · Libros XI-XV», cuyo
        índice cita VII 654, un verso impreso en los tomos 365 y 400.
    """
    from app import gui

    d = _abrir(gui.BuscarTextosDialog("Aquiles"))
    QApplication.processEvents()

    d._consulta = "epíteto"                  # solo en las notas del tomo
    assert not d.cb_notas.isChecked()
    aviso = d._por_que_no_esta("Homero — Ilíada")
    assert "Con notas" in aviso

    d._consulta = "mirmidones"               # no está en ninguna parte
    aviso = d._por_que_no_esta("Homero — Ilíada", "VII 654; IX 12")
    assert "otro volumen" in aviso
    assert "VII 654" in aviso                # la cita concreta, para buscarla
    assert "Con notas" not in aviso
    d.close()
    QApplication.processEvents()
