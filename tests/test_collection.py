"""
test_collection.py
==================
Pruebas de la colección BCG (collection.py + tabla `tomos`):

- Importación del Excel real (BDtomos/titulosBCG.xlsx).
- Cruce título de oferta → tomo: por número explícito y por autor+obra.
- Anti-falsos-positivos: sin evidencia suficiente → None.
- Persistencia en SQLite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import collection
from app.database import Database


@pytest.fixture(scope="module")
def tomos() -> list[collection.Tomo]:
    return collection.load_excel()


@pytest.fixture()
def db_tomos(tomos):
    """Base en memoria con la colección real importada."""
    from app.database import Database

    base = Database(path=":memory:")
    base.replace_tomos(
        [(t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
         for t in tomos]
    )
    yield base
    base.close()


def test_importa_excel_real(tomos):
    assert len(tomos) >= 400  # la colección ronda los 423 tomos
    primero = tomos[0]
    assert primero.orden == 1
    assert "calistenes" in collection.normalize(primero.autor)
    # Números con edición entre corchetes se parsean bien
    assert primero.numero.startswith("1")
    # Páginas numéricas
    assert primero.paginas and primero.paginas > 0


def test_match_por_numero_explicito(tomos):
    tomo = collection.match_tomo(
        tomos, "OBRAS - BIBLIOTECA CLÁSICA GREDOS / 3 - TAPA DURA"
    )
    assert tomo is not None
    assert tomo.orden == 3
    assert "herodoto" in collection.normalize(tomo.autor)


def test_match_por_autor_y_obra(tomos):
    tomo = collection.match_tomo(
        tomos, "JENOFONTE - HELÉNICAS - GREDOS TAPA DURA 1977"
    )
    assert tomo is not None
    assert "jenofonte" in collection.normalize(tomo.autor)

    tomo2 = collection.match_tomo(
        tomos, "APULEYO: EL ASNO DE ORO - ED. GREDOS"
    )
    assert tomo2 is not None
    assert "apuleyo" in collection.normalize(tomo2.autor)


def test_tres_pares_de_tomos_comparten_orden(tomos):
    """
    El número de orden NO identifica a un tomo: tres pares lo comparten
    (200 Aristóteles/Museo, 250 Plinio/Basilio, 415 Estrabón/Ovidio). El
    NÚMERO sí, y el título canónico también.
    """
    from collections import Counter

    repetidos = {o for o, n in Counter(t.orden for t in tomos).items() if n > 1}
    assert repetidos == {200, 250, 415}
    assert len({t.numero for t in tomos}) == len(tomos)
    assert len({t.canonical_title() for t in tomos}) == len(tomos)


def test_marcar_un_tomo_no_marca_a_su_gemelo(db_tomos):
    """
    Marcar Ovidio (415.2) marcaba también a Estrabón (415[27]), y lo
    mismo pasaba con el precio objetivo, la descripción y los enlaces
    (2026-07-29).
    """
    db = db_tomos
    db.set_tomo_flag("415.2", "poseido", True)
    db.set_tomo_target("415.2", 12.0)
    db.set_tomo_description("415.2", "Las metamorfosis de los dioses.", "mito")
    db.add_tomo_link_if_new("415.2", "https://www.todocoleccion.net/a~x1", 20.0)

    filas = {r["numero"]: r for r in db.get_tomos() if r["orden"] == 415}
    assert filas["415.2"]["poseido"] and not filas["415[27]"]["poseido"]
    assert filas["415.2"]["precio_objetivo"] == 12.0
    assert filas["415[27]"]["precio_objetivo"] is None
    assert filas["415[27]"]["descripcion"] is None
    assert len(db.get_tomo_links("415.2")) == 1
    assert db.get_tomo_links("415[27]") == []


def test_tomos_titulados_solo_obras_se_emparejan(tomos):
    """
    Diez tomos se llaman solo "Obras" o "Biblioteca": palabras tan
    corrientes que la lista de significativas las descartaba, y con
    ella el tomo quedaba imposible de identificar — ni desde un correo
    ni desde el nombre de un PDF (Luciano ×4, Ausonio ×2, Prudencio ×2,
    Terencio, Pseudo Apolodoro; 2026-07-29).
    """
    casos = {
        "Obras II - Luciano de Samosata": 113,
        "LUCIANO - OBRAS IV - GREDOS": 172,
        "PRUDENCIO. OBRAS I. BIBLIOTECA CLÁSICA GREDOS": 240,
        "TERENCIO - OBRAS - TAPA DURA": 368,
    }
    for texto, orden in casos.items():
        tomo = collection.match_tomo(tomos, texto)
        assert tomo is not None and tomo.orden == orden, texto
    # Sigue haciendo falta el autor: "Obras" a secas no identifica nada
    assert collection.match_tomo(tomos, "OBRAS COMPLETAS - TAPA DURA") is None


def test_sin_evidencia_no_etiqueta(tomos):
    # Autor sin obra, obra sin autor, o nada: mejor None que etiquetar mal
    assert collection.match_tomo(tomos, "Lote variado de novelas modernas") is None
    assert collection.match_tomo(tomos, "HELÉNICAS siglo XX historia contemporánea") is None
    assert collection.match_tomo(tomos, "") is None
    assert collection.match_tomo([], "Jenofonte Helénicas") is None


def test_match_difuso_orden_alterado(tomos):
    """Palabras en otro orden y con ruido: la semejanza debe bastar."""
    tomo = collection.match_tomo(
        tomos, "VIDAS Y HAZAÑAS DE ALEJANDRO DE MACEDONIA - PSEUDO CALISTENES - TAPA DURA"
    )
    assert tomo is not None and tomo.orden == 1

    tomo2 = collection.match_tomo(
        tomos, "MEDITACIONES (MARCO AURELIO) ED. GREDOS MUY BUEN ESTADO"
    )
    assert tomo2 is not None
    assert "marco aurelio" in collection.normalize(tomo2.autor)


def test_titulo_canonico(tomos):
    tomo = collection.match_tomo(tomos, "JENOFONTE - HELÉNICAS - GREDOS")
    assert tomo is not None
    canon = tomo.canonical_title()
    assert canon.startswith("Jenofonte")
    assert "Helénicas" in canon


def test_etiquetados_sobreviven_reimporte(tomos):
    db = Database(path=":memory:")
    rows = [
        (t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
        for t in tomos
    ]
    db.replace_tomos(rows)
    db.set_tomo_flag(1, "poseido", True)
    db.set_tomo_flag(5, "poseido", True)
    db.set_tomo_flag(7, "deseado", True)
    assert db.flag_count("poseido") == 2
    assert db.flag_count("deseado") == 1
    # Reimportar NO borra ninguno de los dos etiquetados
    db.replace_tomos(rows)
    assert db.flag_count("poseido") == 2
    assert db.flag_count("deseado") == 1
    owned = {r["orden"] for r in db.get_tomos() if r["poseido"]}
    wished = {r["orden"] for r in db.get_tomos() if r["deseado"]}
    assert owned == {1, 5}
    assert wished == {7}
    db.set_tomo_flag(1, "poseido", False)
    assert db.flag_count("poseido") == 1
    # Columnas desconocidas rechazadas (nunca SQL dinámico arbitrario)
    import pytest as _pytest
    with _pytest.raises(ValueError):
        db.set_tomo_flag(1, "otra_cosa", True)
    db.close()


def test_rangos_raros_y_apendice():
    t = lambda orden: collection.Tomo("x", orden, "A", "B", None, "")  # noqa: E731
    assert not collection.is_rare(t(359))
    assert collection.is_rare(t(360))
    assert collection.is_rare(t(415))
    assert not collection.is_rare(t(416))
    assert collection.is_appendix(t(416))
    assert collection.is_appendix(t(420))
    assert not collection.is_appendix(t(421))
    assert not collection.is_rare(t(None))


def test_precio_objetivo_sobrevive_reimporte(tomos):
    db = Database(path=":memory:")
    rows = [
        (t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
        for t in tomos
    ]
    db.replace_tomos(rows)
    db.set_tomo_target(3, 12.5)
    db.set_tomo_target(9, 30.0)
    db.replace_tomos(rows)  # reimportar NO borra los objetivos
    targets = {
        r["orden"]: r["precio_objetivo"]
        for r in db.get_tomos() if r["precio_objetivo"] is not None
    }
    assert targets == {3: 12.5, 9: 30.0}
    db.set_tomo_target(3, None)
    targets = {
        r["orden"]: r["precio_objetivo"]
        for r in db.get_tomos() if r["precio_objetivo"] is not None
    }
    assert targets == {9: 30.0}
    db.close()


def test_titulos_duplicados_desambiguados(tomos):
    """
    197 tomos comparten autor+obras (57 grupos): el título canónico
    lleva sufijo de volumen y es ÚNICO en toda la colección.
    """
    canonicos = [t.canonical_title() for t in tomos]
    assert len(canonicos) == len(set(canonicos)), "canónicos repetidos"
    herodoto = [t for t in tomos if "herodoto" in collection.normalize(t.autor)
                and collection.normalize(t.obras) == "historia"]
    assert len(herodoto) == 5
    assert {t.canonical_title() for t in herodoto} == {
        "Heródoto — Historia · Libros I-II",
        "Heródoto — Historia · Libros III-IV",
        "Heródoto — Historia · Libros V-VI",
        "Heródoto — Historia · Libro VII",
        "Heródoto — Historia · Libros VIII-IX",
    }
    # Los títulos únicos NO llevan sufijo
    jenofonte = collection.match_tomo(tomos, "JENOFONTE - HELÉNICAS - GREDOS")
    assert jenofonte.canonical_title() == "Jenofonte — Helénicas"


def test_match_distingue_volumenes(tomos):
    """El volumen del anuncio decide el tomo correcto del grupo."""
    t1 = collection.match_tomo(tomos, "HERODOTO - HISTORIA LIBROS V-VI - GREDOS")
    assert t1 is not None and t1.orden == 39, t1 and t1.orden
    t2 = collection.match_tomo(tomos, "HERODOTO HISTORIA LIBRO VII GREDOS TAPA DURA")
    assert t2 is not None and t2.orden == 82, t2 and t2.orden
    t3 = collection.match_tomo(
        tomos, "TUCIDIDES HISTORIA DE LA GUERRA DEL PELOPONESO LIBROS VII-VIII"
    )
    assert t3 is not None and t3.orden == 173, t3 and t3.orden


def test_tomo_label(tomos):
    label = collection.tomo_label(tomos[0])
    assert "Tomo BCG" in label and "nº 1" in label


def test_persistencia_sqlite(tomos):
    db = Database(path=":memory:")
    n = db.replace_tomos([
        (t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
        for t in tomos
    ])
    assert n == len(tomos)
    assert db.tomos_count() == len(tomos)
    rows = db.get_tomos()
    assert rows[0]["orden"] == 1
    # Reimportar sustituye, no duplica
    db.replace_tomos([(1, "1", "Autor", "Obra", 100, "")])
    assert db.tomos_count() == 1
    db.close()


# ----------------------------------------------------------------------
# Volúmenes: agrupación y etiquetado de TODA la colección
# ----------------------------------------------------------------------
def test_numero_sin_cola_decimal(tomos):
    """openpyxl devuelve floats: '233.0' no es un número de colección."""
    assert not [t.numero for t in tomos if t.numero.endswith(".0")]
    assert any(t.numero == "233" for t in tomos)


def test_split_volume():
    from app.collection import split_volume

    assert split_volume("Tragedias II") == ("Tragedias", "II")
    assert split_volume("Discursos I") == ("Discursos", "I")
    assert split_volume("Discursos XXXVI-LX") == ("Discursos", "XXXVI-LX")
    assert split_volume("Enéadas III-IV") == ("Enéadas", "III-IV")
    assert split_volume(
        "Obras morales y de costumbres (Moralia) II"
    ) == ("Obras morales y de costumbres (Moralia)", "II")
    # Sin ordinal final: intacto
    assert split_volume("Anábasis") == ("Anábasis", "")
    assert split_volume("Discursos políticos") == ("Discursos políticos", "")
    assert split_volume("") == ("", "")


def test_volumenes_de_una_obra_comparten_titulo_base(tomos):
    """
    El ordinal puede venir en las Notas ("Vol. III.") o dentro del
    título ("Tragedias III"): todos los volúmenes de la misma obra
    comparten base y llevan sufijo propio (bug 2026-07-26: solo el
    primero quedaba etiquetado y el resto caía a "nº 234").
    """
    def grupo(autor_frag: str, obra_frag: str) -> list:
        return sorted(
            (t for t in tomos
             if autor_frag in collection.normalize(t.autor)
             and obra_frag in collection.normalize(t.obras)),
            key=lambda t: t.orden,
        )

    aristides = grupo("elio aristides", "discursos")
    assert [t.canonical_title() for t in aristides] == [
        "Elio Aristides — Discursos · Vol. I",
        "Elio Aristides — Discursos · Vol. II",
        "Elio Aristides — Discursos · Vol. III",
        "Elio Aristides — Discursos · Vol. IV",
        "Elio Aristides — Discursos · Vol. V",
    ]
    euripides = grupo("euripides", "tragedias")
    assert [t.canonical_title() for t in euripides] == [
        "Eurípides — Tragedias · Vol. I",
        "Eurípides — Tragedias · Vol. II",
        "Eurípides — Tragedias · Vol. III",
    ]
    moralia = grupo("plutarco", "moralia")
    assert len(moralia) == 13
    assert moralia[0].canonical_title().endswith("(Moralia) · Vol. I")
    assert moralia[-1].canonical_title().endswith("(Moralia) · Vol. XIII")
    # Rangos de discursos/tratados: base común + alcance como sufijo
    dion = grupo("dion de prusa", "discursos")
    assert [t.canonical_title() for t in dion] == [
        "Dion de Prusa — Discursos · I-XI",
        "Dion de Prusa — Discursos · XII-XXXV",
        "Dion de Prusa — Discursos · XXXVI-LX",
        "Dion de Prusa — Discursos · LXI-LXXX",
    ]


def test_ningun_tomo_agrupado_cae_al_numero(tomos):
    """
    Todo tomo de un grupo debe recibir una etiqueta de VOLUMEN real;
    "nº X" es el último recurso y ya no debería hacer falta en ningún
    tomo de la colección (423 tomos, 2026-07-26).
    """
    respaldo = [t for t in tomos if t.sufijo.startswith("nº ")]
    assert respaldo == [], [t.orden for t in respaldo]
    # Y los canónicos siguen siendo únicos
    canon = [t.canonical_title() for t in tomos]
    assert len(canon) == len(set(canon))


def test_obras_distintas_no_se_fusionan(tomos):
    """Discursos políticos ≠ Discursos privados (no comparten base)."""
    politicos = [t for t in tomos
                 if collection.normalize(t.obras) == "discursos politicos"]
    privados = [t for t in tomos
                if collection.normalize(t.obras) == "discursos privados"]
    assert politicos and privados
    bases = {t.titulo_base for t in politicos} | {t.titulo_base for t in privados}
    assert "Discursos" not in bases


def test_match_distingue_volumen_del_titulo(tomos):
    """El ordinal dentro del título también cuenta como volumen."""
    t2 = collection.match_tomo(tomos, "EURIPIDES - TRAGEDIAS II - GREDOS")
    assert t2 is not None and t2.orden == 11, t2 and t2.orden
    t3 = collection.match_tomo(tomos, "EURIPIDES TRAGEDIAS III TAPA DURA")
    assert t3 is not None and t3.orden == 22, t3 and t3.orden


# ----------------------------------------------------------------------
# Autores colectivos: nunca en las búsquedas externas
# ----------------------------------------------------------------------
def test_is_collective_author():
    for autor in ("VVAA", "VV.AA.", "AA.VV.", "AAVV", "Varios autores",
                  "VVAA (sofistas)", "VVAA (oradores menores)",
                  "Anónimo", "Anónimos", "Desconocido", ""):
        assert collection.is_collective_author(autor), autor
    for autor in ("Jerónimo", "Virgilio", "Marco Aurelio",
                  "Aristóteles", "Elio Aristides", "Vacca"):
        assert not collection.is_collective_author(autor), autor


def test_author_for_search():
    assert collection.author_for_search("VVAA") == ""
    assert collection.author_for_search("Anónimo") == ""
    # De la aclaración se conserva el descriptor: es parte del título
    # real del tomo ("Sofistas. Testimonios y fragmentos")
    assert collection.author_for_search("VVAA (sofistas)") == "sofistas"
    assert collection.author_for_search(
        "VVAA (oradores menores)"
    ) == "oradores menores"
    assert collection.author_for_search("Virgilio") == "Virgilio"


def test_ninguna_busqueda_lleva_autor_colectivo(tomos):
    """
    Los 38 tomos sin autor real (VVAA en sus variantes, Anónimo) no
    pueden mandar "VVAA" al buscador: estropea la consulta.
    """
    malas = ("vvaa", "aavv", "anonimo", "varios autores", "desconocido")
    colectivos = 0
    for t in tomos:
        if collection.is_collective_author(t.autor):
            colectivos += 1
        consulta = " ".join(
            p for p in (collection.author_for_search(t.autor), t.obras) if p
        )
        assert consulta.strip(), t.orden          # jamás vacía
        norm = collection.normalize(consulta)
        assert not any(m in norm for m in malas), (t.orden, consulta)
    assert colectivos >= 35


def test_autor_vacio_o_nulo_no_revienta():
    """El campo Autor puede llegar vacío desde el Excel."""
    assert collection.is_collective_author(None)
    assert collection.is_collective_author("")
    assert collection.author_for_search(None) == ""
    assert collection.author_for_search("") == ""
    assert collection.split_volume(None) == ("", "")
