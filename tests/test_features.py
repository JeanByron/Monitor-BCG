"""
test_features.py
================
Pruebas de las funciones añadidas:

- Umbrales por libro (database.thresholds): coincidencia por patrón
  normalizado, gana el patrón más largo.
- Historial de precios por libro (database.price_history).
- Metadatos y resumen diario (database.meta / summary_for_day).
- Corrección del enlace de la notificación: nunca una página/host de
  imágenes, siempre el anuncio.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import utils
from app.database import Database


@pytest.fixture()
def db():
    d = Database(path=":memory:")
    yield d
    d.close()


# ----------------------------------------------------------------------
# Umbrales por libro
# ----------------------------------------------------------------------
def test_threshold_patron_normalizado(db: Database):
    db.set_thresholds([("Plutarco", 30.0)])
    # Coincide sin tildes ni mayúsculas
    assert db.threshold_for("PLUTARCO - VIDAS PARALELAS - II") == 30.0
    assert db.threshold_for("Herodoto - Historia") is None


def test_threshold_gana_el_mas_largo(db: Database):
    db.set_thresholds([("gredos", 50.0), ("vidas paralelas", 25.0)])
    # Ambos coinciden; gana el patrón más específico (más largo)
    assert (
        db.threshold_for("PLUTARCO - VIDAS PARALELAS - BIBLIOTECA GREDOS") == 25.0
    )


def test_threshold_reemplazo_completo(db: Database):
    db.set_thresholds([("a", 10.0), ("b", 20.0)])
    db.set_thresholds([("c", 30.0)])
    assert db.get_thresholds() == [("c", 30.0)]


# ----------------------------------------------------------------------
# Historial de precios
# ----------------------------------------------------------------------
def test_price_history_agrupa_por_titulo_normalizado(db: Database):
    db.add_price_point("PLUTARCO - Vidas Paralelas II", 40.0)
    db.add_price_point("Plutarco - vidas paralelas II", 20.0)  # mismo libro
    titles = db.price_history_titles()
    assert len(titles) == 1
    clave, _titulo, n = titles[0]
    assert n == 2
    points = db.price_history_for(clave)
    assert [p[1] for p in points] == [40.0, 20.0]
    assert db.last_price("plutarco - VIDAS PARALELAS ii") == 20.0


def test_price_history_titulos_distintos(db: Database):
    db.add_price_point("Libro A", 10.0)
    db.add_price_point("Libro B", 20.0)
    assert len(db.price_history_titles()) == 2


# ----------------------------------------------------------------------
# Meta + resumen diario
# ----------------------------------------------------------------------
def test_clear_price_history(db: Database):
    db.add_price_point("Libro A", 10.0)
    db.add_price_point("Libro B", 20.0)
    assert db.clear_price_history() == 2
    assert db.price_history_titles() == []
    assert db.last_price("Libro A") is None


def test_contrasena_dpapi_roundtrip():
    """Cifrado DPAPI ligado al usuario de Windows: ida y vuelta."""
    import sys as _sys

    from app import config as config_mod
    if not _sys.platform.startswith("win"):
        assert config_mod.encrypt_password("secreto") is None
        return
    token = config_mod.encrypt_password("contraseña-de-prueba-ñ")
    assert token is not None and token != "contraseña-de-prueba-ñ"
    assert config_mod.decrypt_password(token) == "contraseña-de-prueba-ñ"
    # Token corrupto → None, sin lanzar
    assert config_mod.decrypt_password("no-es-base64-válido!!") is None


def test_config_guarda_cifrado(tmp_path):
    """config.json no debe contener jamás la contraseña en claro (Windows)."""
    import sys as _sys

    from app.config import Config

    if not _sys.platform.startswith("win"):
        return
    path = tmp_path / "config.json"
    cfg = Config(email_password="super-secreta-123")
    cfg.save(path)
    raw = path.read_text(encoding="utf-8")
    assert "super-secreta-123" not in raw
    assert "email_password_dpapi" in raw
    # Recarga: descifra a memoria
    cfg2 = Config.load(path)
    assert cfg2.email_password == "super-secreta-123"


def test_extraccion_precio_publicacion():
    """Cascada sobre el HTML de un anuncio: microdatos > og > JSON > texto."""
    assert utils.extract_price_from_listing_html(
        '<span itemprop="price" content="45.00">45 €</span>'
    ) == 45.0
    assert utils.extract_price_from_listing_html(
        '<meta property="product:price:amount" content="12,50">'
    ) == 12.5
    assert utils.extract_price_from_listing_html(
        '<script>{"@type":"Product","offers":{"price":"88.00"}}</script>'
    ) == 88.0
    assert utils.extract_price_from_listing_html(
        "<html><body><h1>Tomo Gredos</h1><p>Precio: 30,00 €</p></body></html>"
    ) == 30.0
    assert utils.extract_price_from_listing_html("<p>sin precios</p>") is None
    # Cifras absurdas descartadas
    assert utils.extract_price_from_listing_html(
        '<span itemprop="price" content="999999">x</span>'
    ) is None


def test_precio_con_url_clicable(db: Database):
    """Cada punto de la serie guarda la publicación de la que salió."""
    db.add_price_point("Libro X", 30.0, url="https://tc/x~x123")
    db.add_price_point("Libro X", 25.0)  # sin url (punto antiguo)
    points = db.price_history_for(db._title_key("Libro X"))
    assert points[0][2] == "https://tc/x~x123"
    assert points[1][2] is None


def test_tomo_links_crud(db: Database):
    lid = db.add_tomo_link(3, "https://www.todocoleccion.net/x~x123")
    db.add_tomo_link(3, "https://es.wallapop.com/item/y")
    db.add_tomo_link(9, "https://otro.com/z")
    assert len(db.get_tomo_links(3)) == 2
    db.update_tomo_link_price(lid, 45.0)
    fila = next(r for r in db.get_tomo_links(3) if r["id"] == lid)
    assert fila["ultimo_precio"] == 45.0
    assert fila["ultima_revision"]
    db.remove_tomo_link(lid)
    assert len(db.get_tomo_links(3)) == 1


def test_meta_roundtrip(db: Database):
    assert db.get_meta("x") is None
    db.set_meta("x", "2026-07-24")
    assert db.get_meta("x") == "2026-07-24"
    db.set_meta("x", "otro")
    assert db.get_meta("x") == "otro"


def test_rotacion_historial_1000_borra_500_antiguas(db: Database):
    """Al superar 1000 filas se van las 500 más antiguas; precios intactos."""
    db.add_price_point("Serie intocable", 10.0)
    for i in range(1001):
        db.add_history(f"Libro {i}", 40, 4, 90.0, None, "ignorado")
    rows = db.get_history(limit=2000)
    assert len(rows) == 501
    titulos = {r["titulo"] for r in rows}
    assert "Libro 1000" in titulos      # las nuevas sobreviven
    assert "Libro 0" not in titulos     # las antiguas rotaron
    assert "Libro 499" not in titulos
    assert "Libro 500" in titulos
    # price_history NO rota jamás
    assert db.last_price("Serie intocable") == 10.0


def test_clear_history_total_y_por_estado(db: Database):
    db.add_history("A", 40, 4, 90.0, None, "notificado")
    db.add_history("B", 40, 30, 25.0, None, "ignorado")
    db.add_history("C", None, 45, None, None, "lote")
    # Por estado: solo borra ese subconjunto
    assert db.clear_history("notificado") == 1
    restantes = [r["estado"] for r in db.get_history()]
    assert sorted(restantes) == ["ignorado", "lote"]
    # Completo
    assert db.clear_history() == 2
    assert db.get_history() == []


def test_summary_for_day(db: Database):
    from datetime import date

    db.add_history("Libro A", 40, 4, 90.0, "http://tc/a", "notificado")
    db.add_history("Libro B", 40, 30, 25.0, "http://tc/b", "ignorado")
    db.add_history("[LOTE ×7] Lote Gredos", None, 45, None, "http://tc/l", "lote")
    s = db.summary_for_day(date.today().isoformat())
    assert s["notificados"] == 1
    assert s["ignorados"] == 1
    assert s["lotes"] == 1
    assert s["mejor_titulo"] == "Libro A"
    assert s["mejor_descuento"] == 90.0


# ----------------------------------------------------------------------
# Enlace de la notificación: anuncio, nunca la imagen
# ----------------------------------------------------------------------
def test_extract_link_evita_paginas_de_imagen():
    links = [
        "https://images.todocoleccion.net/fotos/2026/portada123456789.jpg",
        "https://cloud10.todocoleccion.online/libros/fotos/gran-imagen-del-anuncio-999",
        "https://www.todocoleccion.net/libros-clasicos/plutarco~x123456789",
    ]
    best = utils.extract_link("", links)
    assert best == "https://www.todocoleccion.net/libros-clasicos/plutarco~x123456789"


def test_titulos_de_navegacion_rechazados():
    """'preferencias' y 'Mejorar oferta' jamás pueden ser título (bug real)."""
    for texto in (
        "preferencias",
        "Preferencias",
        "Mejorar oferta",
        "MEJORAR OFERTA",
        "Mejora tu oferta",
        "Configuración de Comunicaciones",
    ):
        assert utils._is_nav_text(texto), texto
        assert not utils._is_valid_title(texto), texto
    # Títulos legítimos no se ven afectados
    for texto in (
        "PLUTARCO - VIDAS PARALELAS - II - GREDOS",
        "HERÓDOTO - HISTORIA - LIBROS I AL IX",
    ):
        assert not utils._is_nav_text(texto), texto
        assert utils._is_valid_title(texto), texto


def test_parse_alert_email_nunca_titulo_navegacion():
    """Si el mejor candidato es navegación, cae al relleno genérico."""
    import email as email_mod

    raw = (
        "From: Todocoleccion <seguimientos@todocoleccion.net>\n"
        "Subject: Mejorar oferta\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "\n"
        "<html><body>"
        '<a href="https://www.todocoleccion.net/preferencias">preferencias</a>'
        "<span>250 €</span><span>125 €</span>"
        "</body></html>\n"
    )
    alert = utils.parse_alert_email(email_mod.message_from_string(raw))
    assert alert.title == "Artículo en favoritos"


def test_titulos_basura_html_css_rechazados():
    """REGRESIÓN (2026-07-24): 'center' llegó a notificarse como título."""
    for texto in ("center", "CENTER", "table", "footer", "nbsp", "arial"):
        assert utils._is_nav_text(texto), texto
        assert not utils._is_valid_title(texto), texto
    for texto in ("App Store", "Aplicación para iOS disponible en el App Store"):
        assert utils._is_nav_text(texto), texto


def test_alerta_sin_precios_con_regex_generica_no_es_fiable():
    """
    REGRESIÓN (2026-07-24): boletines con '100 % seguro' en el cuerpo se
    notificaban como descuento del 100 % sin precio alguno.
    """
    import email as email_mod

    raw = (
        "From: Todocoleccion <seguimientos@todocoleccion.net>\n"
        "Subject: Novedades para ti\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "\n"
        "<html><body>"
        "<p>Compra 100% segura con grandes descuentos y ofertas</p>"
        '<a href="https://www.todocoleccion.net/libros/algo~x123456789">Ver</a>'
        "</body></html>\n"
    )
    alert = utils.parse_alert_email(email_mod.message_from_string(raw))
    assert alert.discount_percent == 100.0  # la regex genérica lo pesca...
    assert not alert.is_reliable()          # ...pero NO es notificable

    # Con ambos precios sí es fiable
    fiable = utils.PriceAlert(
        title="Plutarco", old_price=40.0, new_price=4.0,
        discount_percent=90.0, link=None,
    )
    assert fiable.is_reliable()

    # Con frase semántica (sin precios) también
    semantico = utils.PriceAlert(
        title="Plutarco", old_price=None, new_price=None,
        discount_percent=90.0, link=None,
        sources={"discount_percent": "parser semántico (encabezado 'Descuento del XX%')"},
    )
    assert semantico.is_reliable()


def test_solo_asunto_decide_si_es_aviso_de_favoritos():
    """
    REGRESIÓN (2026-07-24): boletines con "descuento" en el CUERPO se
    colaban como avisos. Ahora la palabra clave debe estar en el ASUNTO.
    """
    keywords = ["bajada de precio", "favorito", "descuento"]
    # Cuerpo con palabras clave pero asunto de boletín → NO es aviso
    assert not utils.is_price_alert(
        "Lo mejor de la semana en Todocoleccion",
        "Grandes descuentos y ofertas en miles de artículos favoritos",
        keywords,
    )
    # Asunto real de favoritos → SÍ
    assert utils.is_price_alert(
        "Bajada de precio en tu artículo favorito", "", keywords
    )


def test_vendidos_y_boletines_excluidos_por_asunto():
    keywords = ["bajada de precio", "favorito", "descuento", "oferta"]
    excludes = [
        "se ha vendido", "vendido", "novedades", "recomendaciones",
        "ultima oportunidad",
    ]
    for asunto in (
        "El artículo que seguías se ha vendido",
        "¡Vendido! Ya tiene dueño",
        "Novedades con descuento para ti",
        "Recomendaciones: ofertas de la semana",
        "Última oportunidad: tu favorito con descuento",
    ):
        assert not utils.is_price_alert(asunto, "", keywords, excludes), asunto


def test_enlaces_de_tracking_y_cuenta_jamas(caplog):
    """
    REGRESIÓN (2026-07-24): las notificaciones abrían
    /api/sistema/track?... o /mitc/comunicaciones/edit. Sin URL con
    forma de anuncio → sin enlace.
    """
    tracking = (
        "https://www.todocoleccion.net/api/sistema/track?idmopen=56814"
        "&lang=1&randID=8401A5EE-61BF-4691-9887E5F5D16F3E5B"
    )
    prefs = "https://www.todocoleccion.net/mitc/comunicaciones/edit"
    assert utils._is_generic_link(tracking)
    assert utils._is_generic_link(prefs)
    # Solo enlaces basura → None (mejor sin enlace que enlace erróneo)
    assert utils.extract_link("", [tracking, prefs]) is None
    # Con un anuncio real presente, gana el anuncio
    ad = "https://www.todocoleccion.net/libros-clasicos/plutarco~x123456789"
    assert utils.extract_link("", [tracking, prefs, ad]) == ad
    # URL del dominio SIN forma de anuncio (portada, categoría) → None
    assert utils.extract_link("", ["https://www.todocoleccion.net/libros"]) is None


def test_enlace_de_anuncio_con_palabra_blanda_no_se_veta():
    """
    REGRESIÓN (2026-07-25): 'logo' casaba dentro de 'diáLOGOs' y los
    anuncios de Platón perdían su enlace. Con forma de anuncio, los
    tokens blandos no vetan; los duros (tracking/cuenta) siempre.
    """
    dialogos = (
        "https://www.todocoleccion.net/libros-segunda-mano-filosofia/"
        "dialogos-ii-gorgias-menexeno-eutidemo-menon-cratilo-platon"
        "~x625657334?utm_campaign=mail-seguimiento"
    )
    assert not utils._is_generic_link(dialogos)
    assert utils.extract_link("", [dialogos]) == dialogos
    # Los tokens DUROS vetan aunque haya forma de anuncio
    assert utils._is_generic_link(
        "https://www.todocoleccion.net/api/track-x123456789?idmopen=1"
    )
    # Sin forma de anuncio, los blandos siguen vetando
    assert utils._is_generic_link("https://instagram.com/todocoleccion")


def test_precio_actual_de_descartados():
    """'Precio actual: X €' gana a la primera cifra (que es la puja)."""
    body = (
        "Han hecho una oferta al vendedor de 100,00 €\n"
        "ARISTOTELES, Acerca de la generacion\n"
        "Precio actual:\n120,00 €\nMejorar oferta"
    )
    assert utils.extract_current_price(body) == 120.0
    # Sin la frase: primer importe del texto
    assert utils.extract_current_price("Lote a 45,50 € y otro a 60 €") == 45.5
    assert utils.extract_current_price("sin cifras aquí") is None


def test_subastas_de_seguimiento_aceptadas():
    """Correos de subastas de artículos seguidos: entran para registrar precio."""
    keywords = ["bajada de precio", "favorito", "descuento"]
    assert utils.is_price_alert(
        "Subastas que finalizan en las próximas horas",
        "Comienza la cuenta atrás\nFinaliza hoy a las 19:10h\nGredos Estrabón\n68,00 €",
        keywords,
    )
    # Una puja ajena ("Han hecho una oferta al vendedor") sigue fuera
    assert not utils.is_price_alert(
        "ARISTOTELES, Acerca de la generacion, Biblioteca Clasica Gredos",
        "Han hecho una oferta al vendedor de 100,00 €\nPrecio actual: 120,00 €",
        keywords,
    )


def test_is_generic_link_rechaza_hosts_de_imagenes():
    assert utils._is_generic_link("https://images.todocoleccion.net/foo")
    assert utils._is_generic_link("https://cloud10.todocoleccion.online/x.jpg")
    assert utils._is_generic_link("https://www.todocoleccion.net/lote/foto-grande/123")
    assert not utils._is_generic_link(
        "https://www.todocoleccion.net/libros/plutarco~x123456789"
    )


# ----------------------------------------------------------------------
# Lotes: espacio de claves propio, siembra, renombrado y reconocimiento
# ----------------------------------------------------------------------
def test_lotes_espacio_de_claves_separado(db: Database):
    db.add_price_point("Jenofonte — Helénicas", 25.0)
    db.add_lot_price_point("[LOTE ×3] Lote Gredos", 90.0, url="https://tc/l~x1")
    # Precios (tomos) no ve lotes; Lotes no ve tomos
    assert [t for _, t, _ in db.price_history_titles()] == ["Jenofonte — Helénicas"]
    assert [t for _, t, _ in db.lot_price_titles()] == ["[LOTE ×3] Lote Gredos"]
    assert db.last_lot_price("[LOTE ×3] Lote Gredos") == 90.0
    assert db.last_price("[LOTE ×3] Lote Gredos") is None


def test_lotes_vigilados_crud(db: Database):
    lote_id = db.add_lote("[LOTE ×2] A + B", "https://tc/lote~x2")
    db.update_lote_price(lote_id, 45.0)
    rows = db.get_lotes()
    assert len(rows) == 1 and rows[0]["ultimo_precio"] == 45.0
    db.remove_lote(lote_id)
    assert db.get_lotes() == []


def test_lotes_renombrar_migra_serie(db: Database):
    db.add_lot_price_point("[LOTE ×1] Lote sin identificar", 30.0)
    db.add_lote("[LOTE ×1] Lote sin identificar", "https://tc/l~x3")
    vieja = db.lot_key("[LOTE ×1] Lote sin identificar")
    nueva = db.rename_lot(vieja, "[LOTE ×1] Apuleyo — El asno de oro")
    assert nueva.startswith(Database.LOT_PREFIX)
    assert db.price_history_for(vieja) == []
    assert len(db.price_history_for(nueva)) == 1
    assert db.get_lotes()[0]["titulo"] == "[LOTE ×1] Apuleyo — El asno de oro"
    with pytest.raises(ValueError):
        db.rename_lot("clave normal", "x")
    with pytest.raises(ValueError):
        db.delete_lot_series("clave normal")


def test_lotes_siembra_desde_historial(tmp_path):
    ruta = tmp_path / "seed.db"
    d1 = Database(path=ruta)
    d1.add_history(
        titulo="[LOTE ×8] Claudio Eliano", precio_ant=None, precio_new=52.5,
        descuento=None, enlace="https://tc/l~x8", estado="lote",
    )
    # Simular una BD ANTERIOR a la función: sin el flag de siembra (en
    # una BD nueva el flag se pone al crearla — el monitor ya escribe
    # los puntos de lote directamente y no hay nada que sembrar).
    with d1._lock, d1._conn:
        d1._conn.execute("DELETE FROM meta WHERE clave = 'lotes_seed_v1'")
    d1.close()
    d2 = Database(path=ruta)  # reabrir dispara la siembra única
    titulos = [t for _, t, _ in d2.lot_price_titles()]
    assert titulos == ["[LOTE ×8] Claudio Eliano"]
    d2.add_history(  # una segunda apertura NO duplica (flag en meta)
        titulo="[LOTE ×2] Otro", precio_ant=None, precio_new=10.0,
        descuento=None, enlace=None, estado="lote",
    )
    d2.close()
    d3 = Database(path=ruta)
    assert len(d3.lot_price_titles()) == 1
    d3.close()


def test_match_tomos_multi_reconoce_varios():
    from app import collection

    tomos = collection.load_excel()
    texto = (
        "Lote de tres tomos de la Biblioteca Clásica Gredos:\n"
        "JENOFONTE - HELÉNICAS - TAPA DURA\n"
        "APULEYO: EL ASNO DE ORO\n"
        "MEDITACIONES (MARCO AURELIO) MUY BUEN ESTADO\n"
        "regalo revista de coleccionismo"
    )
    encontrados = collection.match_tomos_multi(tomos, texto)
    autores = {collection.normalize(t.autor) for t in encontrados}
    assert len(encontrados) == 3
    assert any("jenofonte" in a for a in autores)
    assert any("apuleyo" in a for a in autores)
    assert any("marco aurelio" in a for a in autores)
    assert collection.match_tomos_multi(tomos, "") == []
    assert collection.match_tomos_multi([], texto) == []


def test_extract_listing_text_solo_fuentes_del_anuncio():
    """
    Con metadatos presentes, SOLO se usan las fuentes ceñidas al
    anuncio (og:, JSON-LD Product, h1, #descripcion) — el resto de la
    página (carruseles de "relacionados") etiquetaba tomos que no van
    en el lote.
    """
    html = (
        '<html><head><meta property="og:title" content="Lote Gredos">'
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "Lote Gredos", '
        '"description": "Jenofonte Helénicas\\nApuleyo El asno de oro"}'
        "</script></head>"
        "<body><h1>Lote 2 tomos</h1>"
        '<div id="descripcion">tapa dura, buen estado</div>'
        '<div class="relacionados"><li>HERODOTO HISTORIA GREDOS</li></div>'
        "</body></html>"
    )
    texto = utils.extract_listing_text(html)
    assert "Lote Gredos" in texto
    assert "Jenofonte Helénicas" in texto and "Apuleyo" in texto
    assert "Lote 2 tomos" in texto and "tapa dura" in texto
    # El carrusel de relacionados NO entra
    assert "HERODOTO" not in texto
    # Sin metadatos ni estructura: respaldo = texto visible (sin js/css)
    plano = "<body><style>.x{color:red}</style><li>HELÉNICAS</li></body>"
    assert "HELÉNICAS" in utils.extract_listing_text(plano)
    assert "color:red" not in utils.extract_listing_text(plano)
    assert utils.extract_listing_text("") == ""


# ----------------------------------------------------------------------
# Guardado AUTOMÁTICO de enlaces y precios desde los correos
# ----------------------------------------------------------------------
def test_clean_ad_url_quita_campana():
    sucia = (
        "https://www.todocoleccion.net/libros/plutarco~x123456789"
        "?utm_campaign=mail-seguimientos&utm_source=descuento-precio"
    )
    assert utils.clean_ad_url(sucia) == (
        "https://www.todocoleccion.net/libros/plutarco~x123456789"
    )
    assert utils.clean_ad_url("") == ""


def test_add_tomo_link_if_new_deduplica(db: Database):
    base = "https://www.todocoleccion.net/libros/jenofonte~x111"
    primero = db.add_tomo_link_if_new(7, base + "?utm_source=a", 30.0)
    assert primero is not None
    # El MISMO anuncio en otro correo (otra campaña) no duplica fila,
    # pero sí refresca el precio
    assert db.add_tomo_link_if_new(7, base + "?utm_source=b", 25.0) is None
    filas = db.get_tomo_links(7)
    assert len(filas) == 1
    assert filas[0]["url"] == base          # guardada ya sin campaña
    assert filas[0]["ultimo_precio"] == 25.0
    # Otro anuncio del mismo tomo sí entra
    assert db.add_tomo_link_if_new(
        7, "https://www.todocoleccion.net/libros/jenofonte~x222", 40.0
    ) is not None
    assert len(db.get_tomo_links(7)) == 2
    assert db.add_tomo_link_if_new(7, "", 10.0) is None


def test_add_lote_if_new_deduplica(db: Database):
    url = "https://www.todocoleccion.net/lote/gredos~x999"
    assert db.add_lote_if_new("[LOTE ×5] A", url + "?utm_content=es", 80.0)
    assert db.add_lote_if_new("[LOTE ×5] A", url, 75.0) is None
    filas = db.get_lotes()
    assert len(filas) == 1 and filas[0]["ultimo_precio"] == 75.0
    assert filas[0]["url"] == url


def test_punto_antiguo_sin_mensaje_id_se_adopta(db: Database):
    """
    Volcar el histórico no puede duplicar las gráficas ya guardadas: un
    punto idéntico anterior a la columna `mensaje_id` se ADOPTA (se le
    pone el id del correo) en vez de insertar otro igual.
    """
    db.add_price_point("Libro X", 30.0, url="https://tc/x~x1")   # sin id
    assert sum(n for _, _, n in db.price_history_titles()) == 1
    # El correo que lo generó llega ahora con su Message-ID
    assert db.add_price_point(
        "Libro X", 30.0, url="https://tc/x~x1", mensaje_id="m1"
    ) is False
    assert sum(n for _, _, n in db.price_history_titles()) == 1
    assert db.email_inserted_status("m1")["precios"] == 1
    # Un precio distinto del mismo correo sí es un punto nuevo
    assert db.add_price_point(
        "Libro X", 22.0, url="https://tc/x~x1", mensaje_id="m2"
    ) is True
    assert sum(n for _, _, n in db.price_history_titles()) == 2


def test_escritura_concurrente_sin_bloqueos(db: Database):
    """
    La GUI lee mientras el hilo del monitor escribe: ningún método puede
    reentrar en el lock (un `with self._lock` anidado colgaría la app).
    """
    import threading

    errores: list[str] = []

    def escribe(n: int) -> None:
        try:
            for i in range(60):
                db.add_price_point(f"Serie {n}", float(i), mensaje_id=f"m{n}-{i}")
                db.add_lot_price_point(f"Lote {n}", float(i), mensaje_id=f"l{n}-{i}")
                db.add_history(f"T{n}", None, float(i), None, None,
                               "ignorado", mensaje_id=f"h{n}-{i}")
                db.add_tomo_link_if_new(n + 1, f"https://tc/{n}-{i}~x{i}", 1.0)
                db.add_lote_if_new(f"Lote {n}", f"https://tc/l{n}~x{i}", 2.0)
        except Exception as exc:  # noqa: BLE001
            errores.append(f"escritor {n}: {exc!r}")

    def lee() -> None:
        try:
            for _ in range(60):
                db.price_history_titles()
                db.lot_price_titles()
                db.get_history(limit=50)
                db.email_inserted_status("m0-1")
                db.get_lotes()
        except Exception as exc:  # noqa: BLE001
            errores.append(f"lector: {exc!r}")

    hilos = [threading.Thread(target=escribe, args=(i,)) for i in range(3)]
    hilos.append(threading.Thread(target=lee))
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
    assert not [h for h in hilos if h.is_alive()], "posible interbloqueo"
    assert errores == []
    assert sum(n for _, _, n in db.price_history_titles()) == 180


def test_resource_path_dev_y_empaquetado(tmp_path):
    """
    Los iconos se buscan junto al ejecutable y, si no están, DENTRO del
    .exe (`sys._MEIPASS`): sin ese respaldo, un build empaquetado se
    quedaba sin logo en los avisos.
    """
    import sys as _sys

    from app.config import app_dir, resource_path

    # Desarrollo: el archivo real de la raíz del proyecto
    icono = resource_path("assets/icon.png")
    assert icono == app_dir() / "assets/icon.png"

    # Empaquetado: no está junto al .exe pero sí dentro del paquete
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "solo-en-el-exe.png").write_bytes(b"x")
    _sys._MEIPASS = str(tmp_path)
    try:
        interno = resource_path("assets/solo-en-el-exe.png")
        assert interno.exists()
        assert str(tmp_path) in str(interno)
        assert not resource_path("assets/no-existe.png").exists()
    finally:
        del _sys._MEIPASS


def test_spec_de_empaquetado_es_valido():
    """
    El .spec se ejecuta como Python: un `icon=` duplicado (quedó uno al
    reorganizar las carpetas) rompía el empaquetado con SyntaxError.
    """
    spec = Path(__file__).resolve().parent.parent / "MonitorBCG.spec"
    texto = spec.read_text(encoding="utf-8")
    compile(texto, str(spec), "exec")
    assert "assets/icon.ico" in texto
    assert "config.json" not in texto.split("a = Analysis")[1]  # jamás dentro


# ----------------------------------------------------------------------
# Correos de "ofertas al vendedor" y carrusel de recomendados
# (2026-08-07: se notificó "Foto número 1 del pedido, 40 € → 8,91 €")
# ----------------------------------------------------------------------
def _correo_de_ofertas() -> str:
    """
    Reproduce el correo real que falló: DOS lotes de verdad y, debajo,
    el carrusel «Te puede interesar» con OTROS anuncios y su descuento.
    """
    return (
        "From: todocoleccion <info@todocoleccion.net>\n"
        "Subject: Haz una oferta al vendedor\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "\n"
        "<html><body>"
        "<p>El vendedor de estos lotes admite ofertas</p>"
        '<a href="https://www.todocoleccion.net/libros/historia-libros-iii-iv'
        '-herodoto-clasica-gredos~x701268605">'
        "HISTORIA. LIBROS III - IV. HERODOTO. CLASICA GREDOS</a>"
        "<span>40,00 &euro;</span>"
        '<img src="https://images.todocoleccion.net/f/1.jpg" '
        'alt="Foto número 1 del pedido">'
        '<a href="https://www.todocoleccion.net/libros/la-guerra-de-los-'
        'judios-flavio-josefo~x123456789">'
        "La guerra de los judíos - Flavio Josefo</a>"
        "<span>220,00 &euro;</span>"
        # --- a partir de aquí, OTROS anuncios ---
        "<p>Te puede interesar</p>"
        "<span>HISTORIA DE ESPAÑA</span>"
        "<span>-35%</span><s>8,91 &euro;</s><b>5,79 &euro;</b>"
        "<span>POLIBIO - HISTORIAS</span><span>63,00 &euro;</span>"
        "</body></html>"
    )


def test_recorta_el_carrusel_de_recomendados():
    """
    Todo lo que va tras «Te puede interesar» es de OTROS anuncios: sus
    precios y su porcentaje no tienen nada que ver con este correo.
    """
    texto = "x" * 500 + " Te puede interesar " + "8,91 € -35%"
    assert utils.recorta_carrusel(texto) == "x" * 500 + " "
    # Variantes del rótulo
    for marca in ("Te pueden interesar", "Productos relacionados",
                  "Otros lotes del vendedor"):
        largo = "y" * 500 + f" {marca} basura"
        assert utils.recorta_carrusel(largo) == "y" * 500 + " "


def test_no_recorta_si_no_queda_nada_delante():
    """Cortar tan pronto dejaría el correo sin su propio anuncio."""
    corto = "Te puede interesar esto: 40,00 €"
    assert utils.recorta_carrusel(corto) == corto
    assert utils.recorta_carrusel("") == ""
    assert utils.recorta_carrusel("sin marcador") == "sin marcador"


def test_una_oferta_al_vendedor_no_es_una_bajada_de_precio():
    """
    REGRESIÓN (2026-08-07): «Haz una oferta al vendedor» colaba por la
    palabra clave suelta «oferta» y acabó notificando datos de tres
    anuncios distintos.
    """
    from app.config import Config

    cfg = Config()
    for asunto in (
        "Haz una oferta al vendedor",
        "Han hecho una oferta al vendedor",
        "El vendedor admite ofertas",
        "Tu contraoferta",
    ):
        assert not utils.is_price_alert(
            asunto, "Descuento del 35%", cfg.subject_keywords,
            cfg.exclude_subject_keywords,
        ), asunto
    # …y un aviso de verdad sigue pasando
    assert utils.is_price_alert(
        "Bajada de precio en tu artículo favorito", "",
        cfg.subject_keywords, cfg.exclude_subject_keywords,
    )


def test_el_carrusel_no_contamina_los_precios():
    """El correo entero, de punta a punta: nada del carrusel entra."""
    import email as email_mod

    msg = email_mod.message_from_string(_correo_de_ofertas())
    alerta = utils.parse_alert_email(msg)
    # Ni el 8,91 € ni el -35% del "HISTORIA DE ESPAÑA" del carrusel
    assert alerta.new_price != 8.91
    assert alerta.old_price != 8.91
    assert alerta.discount_percent != 35.0
    # Ni el alt de la imagen como título
    assert "Foto número" not in (alerta.title or "")


def test_el_alt_de_una_imagen_nunca_es_un_titulo():
    for basura in ("Foto número 1 del pedido", "Foto del lote",
                   "Imagen del artículo", "Miniatura del producto"):
        assert utils._is_nav_text(basura), basura
    # Un título de verdad no se toca
    assert not utils._is_nav_text(
        "HISTORIA. LIBROS III - IV. HERODOTO. CLASICA GREDOS"
    )


def test_los_tres_numeros_tienen_que_cuadrar():
    """
    40 € → 8,91 € NO es un 35 %. Si no cuadran, cada dato viene de un
    anuncio distinto y no se puede notificar nada.
    """
    malo = utils.PriceAlert(
        title="x", old_price=40.0, new_price=8.91, discount_percent=35.0,
        link="https://www.todocoleccion.net/x~x1",
    )
    assert not malo.es_coherente()
    assert not malo.is_reliable()

    bueno = utils.PriceAlert(
        title="x", old_price=70.0, new_price=7.0, discount_percent=90.0,
        link="https://www.todocoleccion.net/x~x1",
    )
    assert bueno.es_coherente() and bueno.is_reliable()

    # Todocolección redondea: un par de puntos de diferencia es normal
    redondeo = utils.PriceAlert(
        title="x", old_price=40.0, new_price=26.0, discount_percent=35.0,
        link="https://www.todocoleccion.net/x~x1",
    )
    assert redondeo.es_coherente()

    # Sin los tres datos no hay nada que cotejar
    incompleto = utils.PriceAlert(
        title="x", old_price=None, new_price=26.0, discount_percent=35.0,
        link=None,
    )
    assert incompleto.es_coherente()


def test_las_exclusiones_nuevas_llegan_a_un_config_antiguo(tmp_path):
    """
    La lista vive en config.json: sin migración, quien ya tenía el
    archivo nunca vería las exclusiones que se añaden después — y son
    justo las que tapan el agujero recién descubierto.
    """
    import json

    from app.config import Config

    ruta = tmp_path / "config.json"
    ruta.write_text(
        json.dumps({"exclude_subject_keywords": ["puja", "vendido"]}),
        encoding="utf-8",
    )
    cfg = Config.load(ruta)
    assert "puja" in cfg.exclude_subject_keywords        # lo suyo se respeta
    assert "haz una oferta" in cfg.exclude_subject_keywords
    # Y queda guardado, sin repetirse en la siguiente carga
    otra = Config.load(ruta)
    assert otra.exclude_subject_keywords.count("haz una oferta") == 1


def test_un_correo_con_varios_anuncios_no_es_un_aviso_fiable():
    """
    Un aviso de bajada de precio habla de UN anuncio (comprobado en
    todos los correos reales guardados). Con varios, cualquier pareja de
    importes puede ser de productos distintos: el correo de ofertas daba
    «220 € → 40 €» juntando el Flavio Josefo con el Heródoto.
    """
    import email as email_mod

    msg = email_mod.message_from_string(_correo_de_ofertas())
    alerta = utils.parse_alert_email(msg)
    assert alerta.sources.get("varios_anuncios")
    assert not alerta.is_reliable()


def test_cuenta_los_anuncios_distintos():
    enlaces = [
        "https://www.todocoleccion.net/libros/uno~x701268605",
        "https://www.todocoleccion.net/libros/uno~x701268605?utm_source=x",
        "https://www.todocoleccion.net/libros/dos~x123456789",
        "https://www.todocoleccion.net/preferencias",      # no es un anuncio
    ]
    assert utils._anuncios_distintos(enlaces) == {"701268605", "123456789"}
    assert utils._anuncios_distintos([]) == set()


def test_titulo_desde_el_slug_del_anuncio():
    """
    Respaldo cuando el correo no deja el título a mano: sale de la
    propia dirección, así que SIEMPRE corresponde al anuncio.
    """
    assert utils.titulo_desde_url(
        "https://www.todocoleccion.net/libros-segunda-mano/historia-libros-"
        "iii-iv-herodoto-clasica-gredos-1979~x701268605?utm_source=x"
    ) == "Historia libros iii iv herodoto clasica gredos 1979"
    assert utils.titulo_desde_url("") == ""
    assert utils.titulo_desde_url("https://www.todocoleccion.net/") == ""
