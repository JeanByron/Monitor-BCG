"""
test_lots.py
============
Batería de la detección de LOTES de libros (utils.count_books_in_text /
utils.detect_lot).

Cubre las DOS estrategias que quedan (solo evidencia explícita de
cantidad, y solo en el texto del anuncio):
1. Frase explícita "lote de N libros" (cifras y letras).
2. Cantidad + unidad ("5 TOMOS", "12 vols.") sin la palabra lote.

Y los anti-falsos-positivos, todos vistos en correos reales: años,
números de serie de la colección, bajadas de precio de un solo libro,
rangos romanos ("Anales, libros I-VI" es UN tomo) y el carrusel de
"también te puede interesar" (habla de otros anuncios).

Además, la integración con el monitor: guardado automático de enlaces
y precios, y reproceso sin duplicados (idempotencia por Message-ID).
"""

from __future__ import annotations

import email
from pathlib import Path

import pytest

from app import utils
# ----------------------------------------------------------------------
# Estrategias 1-3: texto
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        # 1) Frase explícita "lote de N ..."
        ("LOTE DE 7 LIBROS BIBLIOTECA CLÁSICA GREDOS", 7),
        ("Lote 5 tomos de la Biblioteca Clásica", 5),
        ("lote de siete libros en tapa dura", 7),
        ("Gran lote de doce volúmenes Gredos", 12),
        ("LOTE DE 25 EJEMPLARES VARIADOS", 25),
        # 2) Cantidad + unidad sin la palabra "lote"
        ("HERÓDOTO - HISTORIA - 5 TOMOS - GREDOS", 5),
        ("Colección de 8 libros de historia clásica", 8),
        ("OBRAS COMPLETAS EN 6 VOLS. PIEL", 6),
        ("cinco libros de Plutarco", 5),
    ],
)
def test_recuento_texto(texto: str, esperado: int):
    count, source = utils.count_books_in_text(texto)
    assert count == esperado, f"{texto!r}: esperado {esperado}, obtenido {count} ({source})"
    assert source


@pytest.mark.parametrize(
    "texto",
    [
        # 3) Un RANGO EN ROMANOS no es un recuento: así titula la propia
        # BCG el alcance de UN volumen (regresión del volcado real,
        # 2026-07-26: "Anales libros I-VI" es el tomo 19, no 6 libros).
        "HERÓDOTO - HISTORIA - LIBROS I AL IX - BIBLIOTECA CLÁSICA",
        "TITO LIVIO - DÉCADAS - TOMOS III-VII",
        "PLATÓN - DIÁLOGOS - Vols. I a VI",
        "ANALES, LIBROS I-VI, CORNELIO TACITO, EDITORIAL GREDOS",
        "ESTRABON, GEOGRAFIA, LIBROS VIII-X",
    ],
)
def test_rango_romano_no_es_lote(texto: str):
    count, source = utils.count_books_in_text(texto)
    assert count == 0, f"{texto!r}: falso lote de {count} ({source})"


def test_cantidad_explicita_gana_al_rango():
    """
    Con "N TOMOS" el recuento es ese, aunque el título traiga un rango
    romano mayor (antes salía "lote de 39" en HISTORIAS - 3 TOMOS).
    """
    count, source = utils.count_books_in_text(
        "HERÓDOTO - HISTORIA - LIBROS I AL IX - 5 TOMOS - GREDOS"
    )
    assert count == 5, source
    count, _ = utils.count_books_in_text(
        "HISTORIAS - 3 TOMOS - POLIBIO - BIBLIOTECA CLASICA"
    )
    assert count == 3


@pytest.mark.parametrize(
    "texto",
    [
        # Sin evidencia de lote: no debe detectar nada
        "PLUTARCO - VIDAS PARALELAS - II - BIBLIOTECA CLÁSICA GREDOS / 869",
        "Bajada de precio en tu artículo favorito",
        "Edición de 1983, Editorial Gredos, Madrid",
        "PORFIRIO - VIDA DE PLOTINO - ENÉADAS I-II",  # rango sin unidad delante
    ],
)
def test_sin_falsos_positivos(texto: str):
    count, _source = utils.count_books_in_text(texto)
    assert count == 0, f"{texto!r}: falso positivo con recuento {count}"


def test_numeros_absurdos_descartados():
    """Años y números de serie no son tamaños de lote."""
    count, _ = utils.count_books_in_text("colección del año 1983 libros antiguos")
    assert count == 0


# ----------------------------------------------------------------------
# detect_lot sobre correos completos
# ----------------------------------------------------------------------
def _make_email(subject: str, html_body: str) -> email.message.Message:
    raw = (
        "From: Todocoleccion <seguimientos@todocoleccion.net>\n"
        f"Subject: {subject}\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "MIME-Version: 1.0\n"
        "\n"
        f"<html><body>{html_body}</body></html>\n"
    )
    return email.message_from_string(raw)


def test_detect_lot_correo_con_lote():
    msg = _make_email(
        "Bajada de precio: LOTE DE 7 LIBROS BIBLIOTECA CLASICA GREDOS",
        '<a href="https://www.todocoleccion.net/libros/lote-gredos~x123456789">'
        "LOTE DE 7 LIBROS BIBLIOTECA CLASICA GREDOS</a>"
        "<span>45 €</span>",
    )
    lot = utils.detect_lot(msg)
    assert lot is not None
    assert lot.book_count == 7
    assert "lote" in lot.source
    assert lot.link and "todocoleccion" in lot.link


def test_detect_lot_correo_individual_no_detecta():
    """Un aviso de precio de un solo tomo no debe generar LotAlert."""
    msg = _make_email(
        "Bajada de precio en tu artículo favorito",
        '<a href="https://www.todocoleccion.net/libros/plutarco~x123456789">'
        "Plutarco. Vidas Paralelas II. Biblioteca Clásica Gredos</a>"
        "<span>4 €</span>",
    )
    lot = utils.detect_lot(msg)
    assert lot is None


def test_boletin_de_recomendaciones_no_es_lote():
    """
    REGRESIÓN (2026-07-24): un boletín con N anuncios DISTINTOS se
    notificaba como falso "lote de N libros" (recuento estructural,
    ya eliminado). Sin evidencia en el texto → no hay lote.
    """
    items = "".join(
        f'<a href="https://www.todocoleccion.net/libros/articulo-{i}~x10000000{i}">'
        f"Anuncio distinto número {i}</a>"
        for i in range(6)
    )
    msg = _make_email("Tenemos lotes que te pueden interesar", items)
    assert utils.detect_lot(msg) is None


def test_detect_lot_nunca_lanza():
    """Un correo vacío/raro no debe lanzar excepciones."""
    msg = email.message_from_string("Subject: \n\n")
    assert utils.detect_lot(msg) is None


# ----------------------------------------------------------------------
# Integración: el monitor guarda solo enlaces y precios (2026-07-26)
# ----------------------------------------------------------------------
class _FakeImap:
    """IMAP mínimo: sirve siempre el mismo correo y acepta el STORE."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.marcados: list[str] = []

    def uid(self, cmd, uid, *extra):
        arg = extra[0] if extra else None
        if cmd == "SEARCH":
            # Un único correo en la carpeta, con UID 1. Hace falta para
            # las pruebas que entran por `_check_new_mail`, que es el
            # camino de verdad (ahí se relee la colección).
            return "OK", [b"1"]
        if cmd == "FETCH" and "HEADER" in (arg or ""):
            import email as _email

            msg = _email.message_from_bytes(self.raw)
            cab = (
                f"From: {msg.get('From', '')}\r\n"
                f"Subject: {msg.get('Subject', '')}\r\n\r\n"
            ).encode()
            return "OK", [(b"1 (X)", cab)]
        if cmd == "FETCH":
            return "OK", [(b"1 (X)", self.raw)]
        if cmd == "STORE":
            self.marcados.append(uid)
        return "OK", [b""]


class _FakeNotifier:
    def __init__(self) -> None:
        self.avisos: list[str] = []
        self.extras: list[str] = []

    def notify_price_drop(self, *a, **k) -> None:
        self.avisos.append("precio")
        self.extras.append(k.get("extra_line") or "")

    def notify_lot(self, *a, **k) -> None:
        self.avisos.append("lote")

    def notify_info(self, *a, **k) -> None:
        self.avisos.append("info")


def _monitor_con(raw: bytes, asunto: str):
    """Monitor con IMAP simulado sobre un aviso real, con otro asunto."""
    from app import collection
    from app.config import Config
    from app.database import Database
    from app.imap_monitor import ImapMonitor

    raw = raw.replace(b"Subject: =?UTF-8?", b"X-Subject-Orig: =?UTF-8?", 1)
    raw = f"Subject: {asunto}\r\n".encode() + raw
    db = Database(path=":memory:")
    tomos = collection.load_excel()
    db.replace_tomos(
        [(t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
         for t in tomos]
    )
    mon = ImapMonitor(Config(), db, _FakeNotifier())
    mon._imap = _FakeImap(raw)
    mon._tomos = collection.tomos_from_rows(db.get_tomos())
    return mon, db


AVISO_REAL = (
    Path(__file__).resolve().parent / "emails"
    / "DÉCIMO MAGNO AUSONIO - OBRAS - I - BIBLIOTECA CLÁS.eml"
)


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_oferta_identificada_guarda_enlace_y_precio():
    """
    Toda oferta cruzada con la colección deja su publicación vigilada en
    la ficha del tomo (Colección) y su punto en la serie (Precios), sin
    que el usuario pegue nada a mano.
    """
    mon, db = _monitor_con(
        AVISO_REAL.read_bytes(),
        "DECIMO MAGNO AUSONIO - OBRAS - BIBLIOTECA CLASICA GREDOS 146",
    )
    mon._process_uid("1")

    enlaces = db.get_tomo_links(146)
    assert len(enlaces) == 1
    assert "~x" in enlaces[0]["url"]
    assert "utm_" not in enlaces[0]["url"]   # sin cola de campaña
    assert enlaces[0]["ultimo_precio"] == 7.0
    series = db.price_history_titles()
    assert [t for _, t, _ in series] == ["Ausonio — Obras · Vol. I"]
    # Un segundo aviso del MISMO anuncio no duplica la publicación
    mon2, _ = _monitor_con(
        AVISO_REAL.read_bytes(),
        "DECIMO MAGNO AUSONIO - OBRAS - BIBLIOTECA CLASICA GREDOS 146",
    )
    mon2.db = db
    mon2._process_uid("1")
    assert len(db.get_tomo_links(146)) == 1
    db.close()


# ----------------------------------------------------------------------
# Precio objetivo: avisa por el PRECIO en €, al margen del descuento
# ----------------------------------------------------------------------
ASUNTO_AUSONIO = "DECIMO MAGNO AUSONIO - OBRAS - BIBLIOTECA CLASICA GREDOS 146"
# El aviso real: 70 € → 7 €, 90 % de descuento, tomo 146.
PRECIO_DEL_AVISO = 7.0


def _monitor_con_objetivo(objetivo, umbral=95.0):
    """
    Monitor sobre el aviso real, con el umbral de descuento SUBIDO por
    encima del 90 % que trae el correo: así lo único que puede disparar
    el aviso es el precio objetivo.
    """
    mon, db = _monitor_con(AVISO_REAL.read_bytes(), ASUNTO_AUSONIO)
    mon.config.min_discount_percent = umbral
    if objetivo is not None:
        db.set_tomo_target(146, objetivo)
    return mon, db


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_sin_objetivo_un_descuento_por_debajo_del_umbral_no_avisa():
    """Control: con el umbral al 95 %, un 90 % no basta."""
    mon, db = _monitor_con_objetivo(None)
    mon._check_new_mail()
    assert mon.notifier.avisos == []
    db.close()


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_el_precio_objetivo_avisa_aunque_el_descuento_no_llegue():
    """
    Lo que pidió el usuario: si el precio cae en su franja, avisa —
    INDEPENDIENTEMENTE del descuento. Aquí el 90 % no llega al umbral
    del 95 %, así que si notifica es por el objetivo y por nada más.
    """
    mon, db = _monitor_con_objetivo(10.0)
    mon._check_new_mail()
    assert mon.notifier.avisos == ["precio"]
    assert "precio objetivo alcanzado" in mon.notifier.extras[0]
    db.close()


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_el_precio_objetivo_incluye_su_propio_valor():
    """La franja es «ese precio O MENOR»: el límite exacto cuenta."""
    mon, db = _monitor_con_objetivo(PRECIO_DEL_AVISO)
    mon._check_new_mail()
    assert mon.notifier.avisos == ["precio"]
    db.close()


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_por_encima_del_objetivo_no_avisa():
    mon, db = _monitor_con_objetivo(PRECIO_DEL_AVISO - 0.01)
    mon._check_new_mail()
    assert mon.notifier.avisos == []
    db.close()


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_el_objetivo_puesto_con_el_monitor_en_marcha_vale_ya():
    """
    REGRESIÓN (2026-08-09): la colección se cargaba UNA vez, en `run()`.
    Poner un precio objetivo desde la ficha del tomo con el programa
    abierto no surtía efecto hasta reiniciarlo. Ahora se relee en cada
    vuelta de correo.
    """
    mon, db = _monitor_con_objetivo(None)
    # El monitor ya tiene su copia de la colección, SIN objetivo…
    assert all(t.precio_objetivo is None for t in mon._tomos)
    # …y el usuario lo pone ahora, sin reiniciar nada.
    db.set_tomo_target(146, 10.0)
    mon._check_new_mail()
    assert mon.notifier.avisos == ["precio"]
    assert "precio objetivo alcanzado" in mon.notifier.extras[0]
    db.close()


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_lote_detectado_queda_vigilado_en_la_pestana_lotes():
    mon, db = _monitor_con(
        AVISO_REAL.read_bytes(),
        "LOTE DE 8 LIBROS BIBLIOTECA CLASICA GREDOS",
    )
    mon._process_uid("1")

    lotes = db.get_lotes()
    assert len(lotes) == 1
    assert lotes[0]["titulo"].startswith("[LOTE ×8]")
    assert lotes[0]["ultimo_precio"] == 7.0
    assert "utm_" not in lotes[0]["url"]
    assert len(db.lot_price_titles()) == 1
    # El lote JAMÁS contamina la serie de tomos (botón Precios)
    assert db.price_history_titles() == []
    db.close()


def _foto(db) -> dict:
    """Cuántos datos hay en cada tabla que alimenta la interfaz."""
    return {
        "historial": len(db.get_history(limit=999)),
        "precios": sum(n for _, _, n in db.price_history_titles()),
        "lotes_puntos": sum(n for _, _, n in db.lot_price_titles()),
        "enlaces": sum(len(db.get_tomo_links(o)) for o in range(1, 424)),
        "lotes_vigilados": len(db.get_lotes()),
    }


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_reprocesar_un_correo_no_duplica_nada():
    """
    Un correo puede reprocesarse (el usuario lo marca no leído, volcado
    masivo del backlog): cada dato que genera entra UNA vez y no se
    vuelve a notificar. Idempotencia por Message-ID.
    """
    import email as _email

    from app.utils import message_key

    raw = AVISO_REAL.read_bytes()
    mon, db = _monitor_con(
        raw, "DECIMO MAGNO AUSONIO - OBRAS - BIBLIOTECA CLASICA GREDOS 146"
    )
    for _ in range(3):
        mon._process_uid("1")

    assert _foto(db) == {
        "historial": 1, "precios": 1, "lotes_puntos": 0,
        "enlaces": 1, "lotes_vigilados": 0,
    }
    assert mon.notifier.avisos == ["precio"]     # solo el primer pase
    # El chequeo por correo ve el dato en cada tabla
    msg_id = message_key(_email.message_from_bytes(
        raw.replace(b"Subject: =?UTF-8?", b"X-Subject-Orig: =?UTF-8?", 1)
    ))
    estado = db.email_inserted_status(msg_id)
    assert estado == {
        "historial": 1, "precios": 1, "lotes": 0,
        "enlaces": 1, "lotes_vigilados": 0,
    }
    assert db.email_already_inserted(msg_id)
    assert not db.email_already_inserted("id-que-no-existe")
    assert db.email_inserted_status("") == {
        "historial": 0, "precios": 0, "lotes": 0,
        "enlaces": 0, "lotes_vigilados": 0,
    }
    db.close()


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_reprocesar_un_lote_no_duplica_nada():
    mon, db = _monitor_con(
        AVISO_REAL.read_bytes(), "LOTE DE 8 LIBROS BIBLIOTECA CLASICA GREDOS"
    )
    for _ in range(3):
        mon._process_uid("1")
    foto = _foto(db)
    assert foto["historial"] == 2          # fila del lote + fila de la alerta
    assert foto["lotes_puntos"] == 1
    assert foto["lotes_vigilados"] == 1
    assert foto["precios"] == 0            # un lote nunca entra en Precios
    assert mon.notifier.avisos == ["lote", "precio"]
    db.close()


def test_message_key_estable_y_con_respaldo():
    import email as _email

    from app.utils import message_key

    con_id = _email.message_from_string(
        "From: a@b.c\nSubject: x\nMessage-ID: <abc123@tc.net>\n\ncuerpo"
    )
    assert message_key(con_id) == "abc123@tc.net"
    # Sin Message-ID: huella estable del propio correo
    sin_id = _email.message_from_string(
        "From: a@b.c\nSubject: Bajada de precio\nDate: Mon, 1 Jan 2026\n\nx"
    )
    k1 = message_key(sin_id)
    k2 = message_key(_email.message_from_string(
        "From: a@b.c\nSubject: Bajada de precio\nDate: Mon, 1 Jan 2026\n\nx"
    ))
    assert k1 == k2 and k1.startswith("sha1:")
    otro = _email.message_from_string(
        "From: a@b.c\nSubject: Otra cosa\nDate: Mon, 1 Jan 2026\n\nx"
    )
    assert message_key(otro) != k1


def test_lote_no_cuenta_el_carrusel_de_recomendados():
    """
    REGRESIÓN (volcado real 2026-07-26): el cuerpo de un aviso trae
    otros anuncios recomendados; contando ese texto, un tomo suelto
    salía como lote fantasma ("HISTORIA - LIBROS I-II - HERÓDOTO"
    aparecía como lote de 9 por un "LOS NUEVE LIBROS DE LA HISTORIA"
    anunciado más abajo). Solo cuenta el texto del ANUNCIO.
    """
    msg = _make_email(
        "HISTORIA - LIBROS I - II - HERODOTO - Biblioteca Clasica Gredos",
        '<a href="https://www.todocoleccion.net/libros/herodoto~x123456789">'
        "HISTORIA - LIBROS I - II - HERODOTO</a><span>12 €</span>"
        "<h3>También te puede interesar</h3>"
        '<a href="https://www.todocoleccion.net/libros/otro~x987654321">'
        "LOS NUEVE LIBROS DE LA HISTORIA - HERODOTO - 9 TOMOS</a>",
    )
    assert utils.detect_lot(msg) is None

    # Un lote de verdad SÍ se detecta, con el número del propio anuncio
    real = _make_email(
        "HISTORIAS - 3 TOMOS - POLIBIO - BIBLIOTECA CLASICA GREDOS",
        '<a href="https://www.todocoleccion.net/libros/polibio~x123456789">'
        "HISTORIAS - 3 TOMOS - POLIBIO</a><span>59 €</span>"
        "<h3>También te puede interesar</h3>"
        '<a href="https://www.todocoleccion.net/libros/otro~x987654321">'
        "COMPLETA, CINCO TOMOS</a>",
    )
    lot = utils.detect_lot(real)
    assert lot is not None and lot.book_count == 3, lot and lot.book_count


# ----------------------------------------------------------------------
# Publicaciones VENDIDAS: se les retira el precio
# ----------------------------------------------------------------------
def test_reconocer_un_anuncio_vendido():
    """
    Un anuncio vendido conserva su precio en la página, así que hay que
    reconocerlo por otras señas. Y "vendedor" NO es "vendido".

    Los TRES sitios vigilados publican la disponibilidad con el
    vocabulario de schema.org (medido sobre las 220 publicaciones
    reales: Wallapop 49/50, IberLibro 19/19, Todocolección 131/151), y
    ese dato manda; las frases solo deciden cuando no está.
    """
    vendidos = (
        # Todocolección y Wallapop: JSON-LD
        '{"@type":"Product","offers":{"availability":"https://schema.org/SoldOut"}}',
        '<script>{"availability":"https://schema.org/OutOfStock"}</script>',
        # IberLibro: itemprop con href
        '<link itemprop="availability" href="http://schema.org/SoldOut"/>',
        # Sin metadato: las frases de cada sitio
        '<div id="descripcion">Artículo vendido</div>',
        '<div id="descripcion">Este ejemplar ya se ha vendido</div>',
        '<div id="descripcion">El artículo ya no está disponible</div>',
    )
    for html in vendidos:
        assert utils.listing_sold(html), html[:46]

    en_venta = (
        '{"@type":"Product","offers":{"availability":"http://schema.org/InStock"}}',
        '<link itemprop="availability" href="http://schema.org/InStock"/>',
        '<div id="descripcion">5 tomos. El vendedor no acepta cambios.</div>',
        '<div id="descripcion">Todos los derechos reservados</div>',
        # El metadato manda sobre el carrusel de recomendados, que trae
        # anuncios vendidos AJENOS
        '<script>{"availability":"https://schema.org/InStock"}</script>'
        '<h3>También te puede interesar</h3><a>ARTÍCULO VENDIDO</a>',
        # Página que no carga: puede ser la red, no una venta
        "",
    )
    for html in en_venta:
        assert not utils.listing_sold(html), html[:46]

    assert utils.listing_availability("<p>sin metadato</p>") == ""


def test_el_precio_de_un_anuncio_vendido_se_retira():
    """
    Al actualizar precios, el anuncio vendido pierde su precio y sus
    puntos salen de la serie: si no, la gráfica seguiría enseñando un
    precio que ya no se puede pagar. Los de OTROS vendedores no se tocan.
    """
    from app.database import Database

    db = Database(path=":memory:")
    titulo = "Heródoto — Historia · Libros I-II"
    clave = Database._title_key(titulo)
    vendida = "https://www.todocoleccion.net/x~x111"
    otra = "https://www.todocoleccion.net/y~x222"
    db.add_price_point(titulo, 30.0, url=vendida, mensaje_id="m1")
    db.add_price_point(titulo, 25.0, url=otra, mensaje_id="m2")
    link_id = db.add_tomo_link_if_new(3, vendida, 30.0)

    quitados = db.delete_price_points(clave, vendida + "?utm_source=x")
    db.mark_link_sold("tomo_links", link_id)

    assert quitados == 1
    restantes = db.price_history_for(clave)
    assert [p[1] for p in restantes] == [25.0]      # sobrevive el otro
    fila = db.get_tomo_links(3)[0]
    assert fila["vendido"] == 1 and fila["ultimo_precio"] is None
    db.close()


def test_quitar_un_lote_deja_los_demas_en_paz():
    """
    "Quitar" borra la serie y TODAS las publicaciones con ese título;
    las de otros lotes no se rozan (se perdieron lotes registrados,
    2026-07-31).
    """
    from app.database import Database

    db = Database(path=":memory:")
    db.add_lote_if_new("[LOTE ×2] Uno", "https://tc/a~x1", 10.0)
    db.add_lote_if_new("[LOTE ×2] Uno", "https://tc/b~x2", 12.0)
    db.add_lote_if_new("[LOTE ×3] Otro", "https://tc/c~x3", 30.0)
    db.add_lot_price_point("[LOTE ×2] Uno", 10.0, url="https://tc/a~x1")
    db.add_lot_price_point("[LOTE ×3] Otro", 30.0, url="https://tc/c~x3")

    clave = db.lot_key("[LOTE ×2] Uno")
    for r in [x for x in db.get_lotes() if db.lot_key(x["titulo"]) == clave]:
        db.remove_lote(r["id"])
    db.delete_lot_series(clave)

    quedan = db.get_lotes()
    assert [r["titulo"] for r in quedan] == ["[LOTE ×3] Otro"]
    assert [t for _, t, _ in db.lot_price_titles()] == ["[LOTE ×3] Otro"]
    db.close()


# ----------------------------------------------------------------------
# Anuncios de VARIOS volúmenes: su precio no es el de un tomo suelto
# ----------------------------------------------------------------------
def test_reconocer_anuncios_de_varios_volumenes():
    """
    Medido sobre los anuncios reales del usuario (2026-08-02): el precio
    de "DIÁLOGOS TOMO I, II Y III" (330 €) acabó en la gráfica de
    "Platón — Diálogos I". Pero "Historias I. Libros XIV-XIX" es UN
    tomo: su número de volumen y los libros que contiene.
    """
    varios = (
        "PLATÓN - DIÁLOGOS TOMO I, II Y III - EDITORIAL GREDOS - 1981, 1983",
        "JULIANO-DISCURSOS I-V Y VI-XII-1979 1ER TOMO Y 1982 2DO TOMO",
        "CORNELIO TÁCITO - ANALES - LIBROS I-IV + XI-XVI - GREDOS",
        "HERÓDOTO - HISTORIA LIBROS I-II, LIBROS III-IV Y LIBROS V-VI 3 VOLUMENES",
        "Elio Arístides, Discursos II, III y IV (Biblioteca Clásica Gredos)",
    )
    for texto in varios:
        assert utils.varios_volumenes(texto), texto[:46]

    uno_solo = (
        "Historias I. Libros XIV - XIX BIBLIOTECA CLÁSICA GREDOS",
        "CORNELIO TÁCITO. ANALES. LIBROS XI-XVI. GREDOS",
        "HERÓDOTO - HISTORIA - LIBROS I-II - BIBLIOTECA CLÁSICA GREDOS",
        "DIÓN CASIO - HISTORIA ROMANA - LIBROS L-LX",
        "PLUTARCO. VIDAS PARALELAS II. GREDOS",
        "Homero. Ilíada. Biblioteca Clásica Gredos",
        "",
    )
    for texto in uno_solo:
        assert not utils.varios_volumenes(texto), texto[:46]


@pytest.mark.skipif(not AVISO_REAL.exists(), reason="falta el aviso real")
def test_el_precio_de_varios_tomos_no_entra_en_la_serie_de_uno():
    """El aviso se notifica igual, pero ni serie ni publicación vigilada."""
    mon, db = _monitor_con(
        AVISO_REAL.read_bytes(),
        "PLATON - DIALOGOS TOMO I, II Y III - EDITORIAL GREDOS",
    )
    mon._process_uid("1")
    assert db.get_history(limit=5)          # el aviso sí queda registrado
    assert db.price_history_titles() == []  # la gráfica del tomo, intacta
    assert sum(len(db.get_tomo_links(o)) for o in range(1, 424)) == 0
    db.close()

    # El mismo aviso de UN solo tomo sí entra
    mon2, db2 = _monitor_con(
        AVISO_REAL.read_bytes(),
        "HERODOTO - HISTORIA - LIBROS I-II - BIBLIOTECA CLASICA GREDOS",
    )
    mon2._process_uid("1")
    assert len(db2.price_history_titles()) == 1
    db2.close()


def test_cabecera_con_codificacion_desconocida():
    """Un charset que Python no conoce no puede tumbar el procesado."""
    from email.header import Header

    crudo = "=?unknown-8bit?q?HER=D3DOTO_-_HISTORIA?="
    assert "HISTORIA" in utils.decode_header_value(crudo)
    assert utils.decode_header_value(str(Header("Normal", "utf-8"))) == "Normal"
