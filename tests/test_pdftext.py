"""
test_pdftext.py
===============
Análisis del PDF de un tomo (app/pdftext.py).

Las piezas puras (calidad del texto, secciones, guiones, índice de
nombres, nombre de archivo → tomo) se prueban SIEMPRE. Las que
necesitan un PDF de verdad solo corren si hay alguno en
`BDtomos/ContenidoTomos`, porque esos archivos son del usuario y no
están en el repositorio.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import collection, pdftext  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
# Las muestras del usuario: la carpeta de trabajo "Libros" y la antigua
# BDtomos/ContenidoTomos. Ninguna está en el repositorio.
PDF_DIRS = [RAIZ / "Libros", RAIZ / "BDtomos" / "ContenidoTomos"]
PDFS = sorted(
    p for carpeta in PDF_DIRS if carpeta.exists() for p in carpeta.glob("*.pdf")
)
sin_pdfs = pytest.mark.skipif(not PDFS, reason="no hay PDF de muestra")


def _pagina(cuerpo="", notas="", seccion="texto", pdf=1):
    """Una página ya extraída, como la produce el lector."""
    return {"tipo": "pagina", "pdf": pdf, "impresa": pdf, "seccion": seccion,
            "titulo": "", "obra": "", "cuerpo": cuerpo, "notas": notas}


# ----------------------------------------------------------------------
# Calidad del texto y secciones
# ----------------------------------------------------------------------
def test_calidad_distingue_escaneo_de_texto():
    assert pdftext._calidad("")[0] == "sin_texto"
    assert pdftext._calidad("   \n  ")[0] == "sin_texto"
    bueno = (
        "En el principio de la obra el autor relata que los hombres de la "
        "ciudad no querían la guerra, pero el rey les obligó a ello y por "
        "eso se dice que las naves partieron con el viento del norte. "
    ) * 6
    estado, legible = pdftext._calidad(bueno)
    assert estado == "nativo" and legible > 22


def test_calidad_detecta_ocr_sucio():
    sucio = "".join(
        f"l{i}: @#~¬ ##$ %%& ~~ ¬¬ xyz{i} qwrt plsk\n" for i in range(60)
    )
    assert pdftext._calidad(sucio)[0] == "ocr_sucio"


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        ("ÍNDICE DE NOMBRES", "indice_nombres"),
        ("INDICE DE NOMBRES", "indice_nombres"),      # sin tilde (OCR)
        ("Índice onomástico", "indice_nombres"),
        ("ÍNDICE GENERAL", "indice_general"),
        ("Índice", "indice_general"),
        ("INTRODUCCIÓN", "introduccion"),
        ("Prólogo", "introduccion"),
        ("BIBLIOGRAFÍA", "bibliografia"),
        ("ABREVIATURAS", "abreviaturas"),
        ("NOTAS", "notas_finales"),
        ("CANTO XXIV", "texto"),
        ("LAS NUBES", "texto"),
    ],
)
def test_clasificacion_de_secciones(titulo, esperado):
    assert pdftext._tipo_seccion(titulo) == esperado


def test_junta_guiones_de_final_de_linea():
    unido = pdftext._junta_guiones(["los lacede-", "monios eran", "guerreros"])
    assert "lacedemonios" in unido
    # No debe unir cuando la siguiente línea empieza en mayúscula
    intacto = pdftext._junta_guiones(["fin de frase-", "Nombre Propio"])
    assert "frase-" in intacto


def test_nombre_de_archivo_a_texto_buscable():
    limpio = pdftext.nombre_para_buscar(
        "470.Homero-Iliada-EditorialGredos-Madrid-1991"
    )
    assert "Homero" in limpio and "Iliada" in limpio
    assert "Gredos" not in limpio and "1991" not in limpio


def test_emparejado_por_nombre_de_archivo():
    """
    Los nombres de archivo traen numeraciones AJENAS a la colección (la
    Ilíada venía como "470" y es el tomo 150): hay que cruzar por autor
    y obra, nunca por ese número.
    """
    tomos = collection.load_excel()
    casos = {
        "470.Homero-Iliada-EditorialGredos-Madrid-1991": 150,
        "Plutarco. - Vidas Paralelas I. Teseo-Romulo [G] [1985]": 77,
        "Aristofanes_Comedias_II": 391,
    }
    for nombre, orden in casos.items():
        tomo = collection.match_tomo(tomos, pdftext.nombre_para_buscar(nombre))
        assert tomo is not None and tomo.orden == orden, nombre


def test_ruta_de_salida_por_tomo():
    tomo = collection.Tomo("3", 3, "Heródoto", "Historia", 400, "Libros I-II.")
    ruta = pdftext.ruta_salida(tomo)
    assert ruta.name.startswith("003 - Heródoto")
    assert ruta.suffix == ".jsonl"


# ----------------------------------------------------------------------
# Índice de nombres: la concordancia del traductor
# ----------------------------------------------------------------------
def test_indice_por_paginas_con_entradas_partidas():
    """Índice que cita PÁGINAS, con entradas que siguen en otra línea."""
    lineas = [
        "ÍNDICE DE NOMBRES",
        "Aquiles, 12, 45-47",
        "   80, 92",                       # continuación de Aquiles
        "Lacedemonios: 120, 121",
        "Zeus, 7",
        "Ulises, 33",
        "Héctor, 40",
        "Príamo, 51",
        "Ares, 60",
        "Atenea, 71",
    ]
    registros = [
        _pagina(seccion="indice_nombres", cuerpo="\n".join(lineas)),
        _pagina(cuerpo="Menelao, 999", pdf=2),      # fuera del índice
    ]
    mapa = pdftext.indice_de_nombres(registros)
    assert mapa["Aquiles"] == ["12", "45-47", "80", "92"]
    assert mapa["Lacedemonios"] == ["120", "121"]
    assert "Menelao" not in mapa


def test_indice_por_canto_y_verso():
    """
    La Ilíada no cita páginas sino CANTO y verso ("Amazonas, III 189;
    VI 186"). Por eso el analizador sacaba UNA sola entrada de las más
    de mil que tiene el tomo.
    """
    lineas = [
        "ÍNDICE DE NOMBRES",
        "Amazonas, III 189; VI 186;",
        "XXIV 804.",
        "Amidón, II 849; XVI 288.",
        "Amíntor, IX 448; X 266.",
        "Anceo, II 609; XXIII 635.",
        "Aquiles, I 1, 121, 131.",
        "Ares, V 30; XX 51.",
        "Atenea, I 194; II 279.",
        "Zeus, I 5; XV 4.",
    ]
    mapa = pdftext.indice_de_nombres(
        [_pagina(seccion="indice_nombres", cuerpo="\n".join(lineas))]
    )
    assert mapa["Amazonas"] == ["III 189", "VI 186", "XXIV 804"]
    assert mapa["Amidón"] == ["II 849", "XVI 288"]
    # Los versos sueltos heredan el canto de su grupo
    assert mapa["Aquiles"] == ["I 1", "I 121", "I 131"]


def test_indice_compuesto_en_cuerpo_menor():
    """
    En algunos tomos el índice va en letra MÁS PEQUEÑA que el texto y
    el separador de notas al pie se lo llevaba entero: hay que mirar
    también ahí (Jenofonte: 12,9 pt frente a 15,4 del cuerpo).
    """
    entradas = [
        "Acarnania, acarnanios, 2, 20, 24.",
        "Agesilao, 1, 5-7, 10-11, 13.",
        "lacedemonios, 4, 7, 9.",
        "educación, 3, 8.",
        "Esparta, 2, 6.",
        "Fliunte, 2, 21.",
        "foceos, 2, 6.",
        "Grecia, 1, 4.",
    ]
    mapa = pdftext.indice_de_nombres([_pagina(
        cuerpo="ÍNDICE DE NOMBRES PROPIOS Y MATERIAS",
        notas="\n".join(entradas),
    )])
    assert mapa["lacedemonios"] == ["4", "7", "9"]
    assert set(mapa["Agesilao"]) == {"1", "5-7", "10-11", "13"}


def test_indice_descriptivo_con_la_referencia_al_final():
    """
    Otros tomos explican cada nombre y ponen la localización DETRÁS de
    la explicación ("Abante (1): compañero de Perseo…, V 126"). Exigir
    la cifra pegada al nombre sacaba 1 entrada de 1.302 (Ovidio,
    Metamorfosis XI-XV, 2026-07-29).
    """
    lineas = [
        "ÍNDICE DE NOMBRES",
        "Ábaris: guerrero de Fineo, muerto por",
        "Perseo, V 86.",
        "Abante (1): compañero de Perseo en la",
        "lucha contra Fineo, V 126.",
        "Acantis: hija de Hipodamante, IX 87.",
        "Acasto: rey de Yolco, hijo de Pelias, XI 409.",
        "Aquelóo: río de Etolia, VIII 549.",
        "Áyax: hijo de Telamón, XII 624.",
        "Circe: maga hija del Sol, XIV 10.",
    ]
    mapa = pdftext.indice_de_nombres(
        [_pagina(seccion="indice_nombres", cuerpo="\n".join(lineas))]
    )
    assert mapa["Ábaris"] == ["V 86"]
    assert mapa["Abante (1)"] == ["V 126"]
    assert mapa["Circe"] == ["XIV 10"]


def test_indice_por_libro_capitulo_y_parrafo():
    """
    Estrabón se cita por LIBRO, capítulo y párrafo, y su índice no pone
    coma tras el nombre ("Sábata XVI, 4,2"). Además el rótulo va con las
    letras espaciadas, como en todo escaneo de esa época (2026-07-31).
    """
    assert pdftext._tipo_seccion(
        "Í N D I C E  D E  T O P Ó N I M O S  Y É T N I C O S"
    ) == "indice_nombres"

    lineas = [
        "Í N D I C E  D E  T O P Ó N I M O S  Y É T N I C O S",
        "Sábata XVI, 4,2",
        "sabeos XVI, 4, 2; 4, 19; 4, 21",
        "Saccopodes XVI, 1, 19",
        "Safo XVII, 1,33",
        "Salda, puerto XVII, 3, 12",
        "Sandrocoto XV, 1, 36; 1, 53; 1,",
        "57; 2,9",
        "XV I,4, 14",                    # continuación que partió el OCR
        "persas XV, 1,6; 1,7; 1, 10",
    ]
    mapa = pdftext.indice_de_nombres(
        [_pagina(seccion="indice_nombres", cuerpo="\n".join(lineas))]
    )
    assert mapa["Sábata"] == ["XVI 4, 2"]
    assert mapa["sabeos"] == ["XVI 4, 19", "XVI 4, 2", "XVI 4, 21"]
    assert mapa["Salda, puerto"] == ["XVII 3, 12"]        # el nombre lleva coma
    assert mapa["Sandrocoto"][-1] == "XV 2, 9"            # sigue en otra línea
    # Un romano suelto NUNCA es una entrada
    assert not any(pdftext._es_romano(n) for n in mapa)


def test_bibliografia_no_cuela_entradas_falsas():
    """
    La bibliografía tiene la MISMA forma que un índice (autor, coma,
    números) y colaba entradas falsas en tomos que ni siquiera llevan
    índice de nombres: sin rótulo que lo ancle, no se acepta nada.
    """
    lineas = [
        "1. Principales ediciones anteriores a 1800",
        "Bolonia, U. Rugerius y D. Bertochus, 1474.",
        "Florencia, S. J. de Ripoli, 1481.",
        "Venecia, C. de Pensis, 1500 y 1501.",
        "París, B. Ascensius, 1500.",
        "Amberes, C. Plantino, 1566.",
        "Leiden, F. Raphelengius, 1595.",
        "Basilea, T. Guarinus, 1560.",
        "Ginebra, H. Estienne, 1573.",
    ]
    biblio = _pagina(seccion="bibliografia", cuerpo="\n".join(lineas))
    assert pdftext.indice_de_nombres([biblio]) == {}


def test_los_anos_no_son_referencias():
    """Aun dentro del índice, un año de edición no es una página."""
    assert pdftext._referencias("1474, 1500") == []
    assert pdftext._referencias("12, 45") == ["12", "45"]
    # Con canto delante, un número alto SÍ vale (versos de la Ilíada)
    assert pdftext._referencias("XXIV 1804") == ["XXIV 1804"]


def test_el_indice_continua_en_las_paginas_siguientes():
    primera = [
        "ÍNDICE DE NOMBRES", "Aquiles, I 1.", "Ares, V 30.",
        "Atenea, I 194.", "Ayante, II 527.", "Briseida, I 184.",
        "Calcante, I 69.", "Crises, I 11.", "Diomedes, II 563.",
    ]
    segunda = [
        "Héctor, VI 440.", "Helena, III 121.", "Hera, I 55.",
        "Ida, VIII 47.", "Iris, II 786.", "Menelao, III 21.",
        "Néstor, I 247.", "Odiseo, II 244.", "Patroclo, I 307.",
    ]
    registros = [
        _pagina(seccion="indice_nombres", pdf=1, cuerpo="\n".join(primera)),
        _pagina(pdf=2, cuerpo="\n".join(segunda)),   # sin rótulo ni sección
        _pagina(pdf=3, cuerpo="Aquí ya empieza otro capítulo del libro."),
    ]
    mapa = pdftext.indice_de_nombres(registros)
    assert "Héctor" in mapa and "Patroclo" in mapa    # sigue en la página 2
    assert mapa["Héctor"] == ["VI 440"]


# ----------------------------------------------------------------------
# Con PDF reales (si los hay)
# ----------------------------------------------------------------------
def _paginas_y_texto(ruta: Path) -> tuple[int, bool]:
    """Páginas y si hay capa de texto, SIN extraer el tomo entero."""
    import fitz

    doc = fitz.open(ruta)
    try:
        n = len(doc)
        muestra = "".join(
            doc[i].get_text() for i in range(n // 4, min(n // 4 + 12, n))
        )
        return n, len(muestra.strip()) > 500
    finally:
        doc.close()


@sin_pdfs
def test_analiza_un_pdf_real_sin_copiarlo(tmp_path, monkeypatch):
    """
    Analizar de verdad es lento (segundos por tomo), así que se hace
    con el PDF más corto que haya de muestra.
    """
    monkeypatch.setattr(pdftext, "TEXTOS_DIR", tmp_path)
    con_texto = [(p, *_paginas_y_texto(p)) for p in PDFS]
    candidatos = [(n, p) for p, n, hay in con_texto if hay]
    if not candidatos:
        pytest.skip("ninguna muestra tiene capa de texto")
    _paginas, ruta = min(candidatos)

    tomos = collection.load_excel()
    res = pdftext.analizar(ruta, tomos)
    assert res.utilizable
    assert res.palabras > 10_000
    assert res.registros and res.secciones
    assert res.resumen()

    tomo = res.tomo_detectado
    assert tomo is not None, f"no se emparejó {ruta.name}"
    destino = pdftext.guardar(res, tomo)
    assert destino.exists()
    # El PDF NO se copia: en la carpeta solo queda el texto extraído
    assert list(tmp_path.glob("*.pdf")) == []
    # Y el seguimiento lo ve
    estado = pdftext.estado_de_los_tomos()
    cabecera = pdftext.estado_del_tomo(estado, tomo)
    assert cabecera is not None
    assert cabecera["palabras"] == res.palabras


@sin_pdfs
def test_escaneo_sin_texto_se_detecta_y_no_es_utilizable():
    escaneos = [p for p in PDFS if not _paginas_y_texto(p)[1]]
    if not escaneos:
        pytest.skip("ninguna muestra es un escaneo")
    res = pdftext.analizar(escaneos[0], collection.load_excel())
    assert not res.utilizable
    assert res.estado == "sin_texto"
    assert any("escaneo" in d for d in res.dificultades)
    assert "ESCANEO" in res.resumen()


@sin_pdfs
def test_indice_de_nombres_de_un_tomo_real():
    """
    Sobre el PDF real que más nombres tenga: la concordancia debe salir
    con centenares de entradas, no con una.
    """
    tomos = collection.load_excel()
    # Del más corto al más largo, y parar en el primero que traiga
    # índice: analizar los seis enteros son minutos.
    candidatos = sorted(
        (n, p) for p in PDFS for n, hay in [_paginas_y_texto(p)] if hay
    )
    nombres: dict = {}
    for _paginas, ruta in candidatos:
        nombres = pdftext.analizar(ruta, tomos).nombres
        if len(nombres) > 100:
            break
    if len(nombres) <= 100:
        pytest.skip("ninguna muestra trae un índice de nombres extenso")
    assert len(nombres) > 100, len(nombres)
    assert all(v for v in nombres.values())          # todas con referencias
    largos = [n for n in nombres if len(n) > 60]
    assert not largos, largos[:3]


# ----------------------------------------------------------------------
# OCR parcial: rescatar solo las páginas que no traen texto
# ----------------------------------------------------------------------
def _analisis(paginas=100, sin_texto=(), estado="nativo", palabras=50_000):
    a = pdftext.Analisis(archivo="x.pdf", sha1="0")
    a.paginas_pdf = paginas
    a.paginas_sin_texto = list(sin_texto)
    a.estado = estado
    a.palabras = palabras
    return a


def test_cobertura_y_umbral_del_ocr_parcial():
    """
    Con pocas páginas sueltas se ofrece reconocerlas; si falta más de
    una cuarta parte, es mejor buscar otra copia que reconocer medio
    tomo.
    """
    casi_entero = _analisis(paginas=417, sin_texto=range(1, 18))   # 4 %
    assert casi_entero.cobertura == pytest.approx(95.9, abs=0.1)
    assert casi_entero.completable_con_ocr

    medio_roto = _analisis(paginas=1402, sin_texto=range(1, 396))  # 28 %
    assert not medio_roto.completable_con_ocr

    entero = _analisis(paginas=100)
    assert entero.cobertura == 100.0
    assert not entero.completable_con_ocr          # no falta nada

    escaneo = _analisis(paginas=257, sin_texto=range(1, 258),
                        estado="sin_texto", palabras=0)
    assert not escaneo.completable_con_ocr         # ese no se rescata


def test_el_resumen_avisa_de_las_paginas_que_faltan():
    a = _analisis(paginas=417, sin_texto=range(1, 18))
    resumen = a.resumen()
    assert "17" in resumen and "sin reconocer" in resumen


def test_ocr_sin_tesseract_explica_como_instalarlo():
    disponible, mensaje = pdftext.ocr_disponible("ruta/que/no/existe.exe")
    if disponible:
        pytest.skip("este equipo tiene Tesseract instalado")
    assert "Tesseract" in mensaje
    assert "Spanish" in mensaje                    # el idioma que hace falta
    # Y completar_con_ocr no se queda a medias: avisa
    with pytest.raises(RuntimeError):
        pdftext.completar_con_ocr(
            Path("x.pdf"), _analisis(sin_texto=[1]),
            ruta_tesseract="ruta/que/no/existe.exe",
        )


def test_ocr_no_hace_nada_si_no_faltan_paginas():
    a = _analisis()
    assert pdftext.completar_con_ocr(Path("x.pdf"), a) is a


# ----------------------------------------------------------------------
# OCR: qué pasa cuando reconoce pero no rescata nada
# ----------------------------------------------------------------------
class _PaginaFalsa:
    def __init__(self, texto=""):
        self.texto = texto

    def get_textpage_ocr(self, **_kw):
        return object()

    def get_text(self, **_kw):
        return self.texto


class _DocFalso:
    def __init__(self, texto=""):
        self.texto = texto

    def __getitem__(self, _i):
        return _PaginaFalsa(self.texto)

    def close(self):
        pass


def _fitz_falso(monkeypatch, texto=""):
    import types

    modulo = types.ModuleType("fitz")
    modulo.open = lambda *_a, **_k: _DocFalso(texto)
    monkeypatch.setitem(sys.modules, "fitz", modulo)


def _con_ocr(monkeypatch, idiomas={"spa"}):
    monkeypatch.setattr(pdftext, "ocr_disponible", lambda *_a: (True, "t.exe"))
    monkeypatch.setattr(pdftext, "idiomas_ocr", lambda: set(idiomas))


def _registros(numeros):
    return [_pagina(pdf=n) for n in numeros]


def test_ocr_que_no_rescata_nada_queda_marcado_y_explicado(monkeypatch):
    """
    Sin esta marca la ventana volvía a ofrecer el reconocimiento una y
    otra vez: `paginas_ocr` vacío se leía como "aún no se ha intentado"
    (Ilíada, 2026-07-28).
    """
    _con_ocr(monkeypatch)
    _fitz_falso(monkeypatch, texto="")          # páginas en blanco
    a = _analisis(paginas=645, sin_texto=[1, 604, 605])
    a.registros = _registros([1, 604, 605])

    res = pdftext.completar_con_ocr(Path("x.pdf"), a)
    assert res.ocr_intentado                    # se intentó: no repetir
    assert res.paginas_ocr == []
    assert "blanco" in res.ocr_fallo or "láminas" in res.ocr_fallo
    assert res.paginas_sin_texto == [1, 604, 605]


def test_ocr_cae_al_ingles_si_falta_el_espanol(monkeypatch):
    """
    Tesseract se instala solo con inglés: pedirle «spa» fallaba en TODAS
    las páginas y el usuario solo veía que el botón no hacía nada.
    """
    _con_ocr(monkeypatch, idiomas={"eng", "osd"})
    _fitz_falso(monkeypatch, texto="Texto reconocido de una página entera " * 4)
    a = _analisis(paginas=100, sin_texto=[7])
    a.registros = _registros([7])

    res = pdftext.completar_con_ocr(Path("x.pdf"), a)
    assert res.paginas_ocr == [7]
    assert any("no está instalado" in d for d in res.dificultades)
    assert any("Spanish" in d for d in res.dificultades)


def test_ocr_sin_ningun_idioma_dice_como_arreglarlo(monkeypatch):
    _con_ocr(monkeypatch, idiomas={"osd"})
    _fitz_falso(monkeypatch)
    a = _analisis(paginas=100, sin_texto=[7])
    a.registros = _registros([7])
    with pytest.raises(RuntimeError, match="Spanish"):
        pdftext.completar_con_ocr(Path("x.pdf"), a)


# ----------------------------------------------------------------------
# Progreso y cancelación del análisis
# ----------------------------------------------------------------------
def _pdf_de_prueba(destino: Path, paginas: int = 3) -> Path:
    """Un PDF mínimo con texto de sobra para que el análisis lo acepte."""
    import fitz

    parrafo = (
        "En el principio de la obra el autor relata que los hombres de la "
        "ciudad no querían la guerra, pero el rey les obligó a ello y por "
        "eso se dice que las naves partieron con el viento del norte. "
    ) * 4
    doc = fitz.open()
    for _ in range(paginas):
        pagina = doc.new_page()
        pagina.insert_textbox(fitz.Rect(60, 60, 540, 760), parrafo, fontsize=11)
    doc.save(destino)
    doc.close()
    return destino


def test_el_analisis_va_contando_su_avance(tmp_path):
    ruta = _pdf_de_prueba(tmp_path / "Homero - Iliada.pdf", paginas=4)
    pasos = []
    res = pdftext.analizar(
        ruta, collection.load_excel(),
        progreso=lambda fase, hechas, total: pasos.append((fase, hechas, total)),
    )
    fases = [p[0] for p in pasos]
    assert "Abriendo el PDF" in fases
    assert "Extrayendo el texto" in fases
    # La fase medible llega hasta la última página del PDF
    extraccion = [p for p in pasos if p[0] == "Extrayendo el texto"]
    assert extraccion[-1][1:] == (4, 4)
    assert len(res.registros) == 4


# ----------------------------------------------------------------------
# Ediciones digitales (EPUB pasado a PDF): Aristófanes y compañía
# ----------------------------------------------------------------------
def _pdf_ebook(destino: Path, paginas: int = 8) -> Path:
    """
    Imita el formato en que circulan varios tomos: hoja A4, sin folio
    impreso, pie "Página N" en TODAS y los números de verso en el margen
    derecho, en letra menor que el texto.
    """
    import fitz

    verso = 0
    doc = fitz.open()
    for n in range(1, paginas + 1):
        pagina = doc.new_page(width=595, height=841)
        cuerpo = f"Escena {n}. " + (
            "El coro de los caballeros entra en escena y responde al "
            "morcillero que no se calla, porque la ciudad no quiere la "
            "guerra pero el consejo la ha votado y por eso se dice que "
            "las naves partieron con el viento del norte. "
        ) * 3
        # Lejos del margen: lo que se repite arriba del todo en todas
        # las hojas es un ENCABEZADO, no texto.
        pagina.insert_textbox(fitz.Rect(72, 95, 470, 700), cuerpo, fontsize=14.4)
        for salto in range(4):            # numeración de versos al margen
            verso += 5
            pagina.insert_text(
                fitz.Point(505, 120 + salto * 150), str(verso), fontsize=10.8
            )
        pagina.insert_text(fitz.Point(265, 800), f"Página {n}", fontsize=14.4)
    doc.save(destino)
    doc.close()
    return destino


def test_marcador_de_pagina_fuera_del_texto():
    assert pdftext._sin_marcador("Zeus habló\nPágina 201").strip() == "Zeus habló"
    assert pdftext._sin_marcador("Pagina 7") == ""
    # El mismo pie con la marca del conversor delante
    assert pdftext._sin_marcador("www.lectulandia.com - Página 135") == ""
    # Una frase que empiece por "página" NO es el pie del conversor
    assert "página siguiente" in pdftext._sin_marcador("en la página siguiente")


def test_cola_de_notas_troceada_por_obras():
    """
    La cola puede venir en varios bloques, uno por obra (el Juliano
    trae cinco): quedarse con el último dejaba 685 "hojas de texto" de
    las que 650 son notas.
    """
    secciones = [
        {"desde": 7, "hasta": 211, "tipo": "texto", "titulo": "CONTRA LOS GALILEOS"},
        {"desde": 212, "hasta": 358, "tipo": "notas_finales", "titulo": "Notas contra los galileos"},
        {"desde": 359, "hasta": 790, "tipo": "notas_finales", "titulo": "Notas cartas"},
        {"desde": 791, "hasta": 799, "tipo": "notas_finales", "titulo": "Notas testimonios"},
        {"desde": 800, "hasta": 867, "tipo": "notas_finales", "titulo": "Notas leyes"},
    ]
    assert pdftext.hojas_de_texto(secciones, 867) == 211
    # Y el rótulo de cada bloque cuenta como notas, no como una obra más
    assert pdftext._tipo_seccion("Notas cartas y fragmentos") == "notas_finales"


def test_edicion_digital_se_reconoce_y_se_limpia(tmp_path):
    """
    Sin reconocer el formato: el pie entraba en el texto de cada hoja,
    los versos del margen se tomaban por folios (desfase de 949 en
    Aristófanes) y las hojas cortas contaban como escaneos.
    """
    ruta = _pdf_ebook(tmp_path / "Aristofanes - Comedias I.pdf", paginas=8)
    res = pdftext.analizar(ruta, collection.load_excel())

    assert res.formato == "ebook"
    assert res.desfase_folio is None          # no hay página impresa
    assert any("edición digital" in d for d in res.dificultades)
    # Ni rastro del pie en el texto guardado
    assert not any("Página" in r["cuerpo"] for r in res.registros)
    # Los versos se guardan aparte y TODOS: en prosa son los parágrafos
    # y con solo el primero y el último no se localiza un pasaje. Van
    # como texto porque la referencia puede llevar letra ("229D").
    assert res.registros[0]["versos"] == ["5", "10", "15", "20"]
    assert res.registros[1]["versos"] == ["25", "30", "35", "40"]
    # …y no se cuelan como notas al pie ni en el cuerpo
    assert not any(r["notas"].strip() for r in res.registros)
    assert "5" not in res.registros[0]["cuerpo"].split()


def test_hojas_cortas_no_son_escaneos(tmp_path):
    """
    Portadillas ("LAS NUBES"), rótulos y notas sueltas ocupan media
    línea: contándolas como imagen salían 393 páginas «sin reconocer»
    de un tomo entero (Aristófanes II, 2026-07-29).
    """
    import fitz

    ruta = _pdf_ebook(tmp_path / "Aristofanes - Comedias II.pdf", paginas=6)
    doc = fitz.open(ruta)
    portadilla = doc.new_page(width=595, height=841)     # hoja corta real
    portadilla.insert_text(fitz.Point(72, 300), "LAS NUBES", fontsize=20)
    portadilla.insert_text(fitz.Point(265, 800), "Página 7", fontsize=14.4)
    doc.save(tmp_path / "con_portadilla.pdf")
    doc.close()

    res = pdftext.analizar(tmp_path / "con_portadilla.pdf",
                           collection.load_excel())
    assert res.paginas_sin_texto == []        # ninguna hoja es un escaneo
    assert res.cobertura == 100.0
    assert not res.completable_con_ocr


def test_el_informe_no_llama_paginas_a_las_hojas_del_conversor():
    """
    En una edición digital las notas van a hoja POR NOTA: decir "1.436
    páginas" de un tomo de 528 despista más que informa.
    """
    a = _analisis(paginas=1436)
    a.formato = "ebook"
    a.paginas_impresas = 528
    a.obras = ["LAS NUBES", "LAS AVISPAS", "LA PAZ", "LAS AVES"]
    a.secciones = [
        {"desde": 3, "hasta": 364, "tipo": "texto", "titulo": "LAS NUBES"},
        {"desde": 365, "hasta": 1436, "tipo": "notas_finales", "titulo": "Notas"},
    ]
    assert a.hojas_de_texto == 364
    resumen = a.resumen()
    assert "Hojas del PDF: 1.436" in resumen
    assert "364 de texto" in resumen and "1.072 de notas finales" in resumen
    assert "528 páginas" in resumen              # las del papel
    assert "Obras del volumen: 4" in resumen and "LAS AVES" in resumen


def test_el_seguimiento_deduce_las_hojas_de_texto(tmp_path, monkeypatch):
    """
    Los textos guardados antes de distinguir hoja de página deben
    seguir dando la cifra buena en la lista, sin releer el archivo.
    """
    import json

    monkeypatch.setattr(pdftext, "TEXTOS_DIR", tmp_path)
    cabecera = {
        "tipo": "tomo", "orden": 391, "numero": "391",
        "autor": "Aristófanes", "obras": "Comedias", "paginas_pdf": 1436,
        "secciones": [
            {"desde": 3, "hasta": 364, "tipo": "texto", "titulo": "LAS NUBES"},
            {"desde": 365, "hasta": 1436, "tipo": "notas_finales",
             "titulo": "Notas"},
        ],
    }
    (tmp_path / "391 - x.jsonl").write_text(
        json.dumps(cabecera, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    estado = pdftext.estado_de_los_tomos()
    cabecera = next(iter(estado.values()))
    assert cabecera["hojas_texto"] == 364
    assert cabecera["paginas_pdf"] == 1436          # el dato crudo se conserva


def test_referencias_del_margen_fuera_del_texto():
    """
    En las ediciones digitales el conversor suelta las referencias del
    margen como líneas al principio de la hoja. Se citan así a Platón y
    a Juliano ("229D") y a Aristóteles ("1094a"), y quedaban DENTRO del
    texto (2026-07-29).
    """
    hoja = pdftext._texto_de_hoja(
        ["229D", "229E", "230A",
         "daño quien la toma, pero la conciencia del hermano que ve",
         "podría escandalizarse[61] según vosotros."],
        [], [], "texto",
    )
    assert hoja["versos"] == ["229D", "229E", "230A"]
    assert "229D" not in hoja["cuerpo"]
    # La llamada de nota tampoco parte la palabra
    assert "escandalizarse según" in hoja["cuerpo"]
    assert hoja["llamadas"] == ["61"]


def test_cada_nota_final_lleva_su_numero():
    hoja = pdftext._texto_de_hoja(
        ["[65] Deuteronomio 32, 9. <<"], [], [], "notas_finales",
    )
    assert hoja["nota"] == "65"
    assert hoja["cuerpo"] == "Deuteronomio 32, 9."


def test_buscar_frases_que_cruzan_el_renglon():
    """
    El texto se guarda renglón a renglón (en los poetas el renglón es el
    verso), así que buscar se hace sobre el texto aplanado.
    """
    registro = _pagina(
        cuerpo="daño quien la toma, pero la conciencia del hermano\n"
               "que ve podría escandalizarse según vosotros.",
    )
    frase = "la conciencia del hermano que ve"
    assert frase not in registro["cuerpo"]              # cruza el renglón
    assert frase in pdftext.texto_para_buscar(registro)


def test_revision_repara_los_textos_viejos(tmp_path, monkeypatch):
    """
    Los textos guardados antes de una corrección se quedaban con el
    defecto de su día. La revisión los arregla SIN el PDF: reclasifica
    las secciones, devuelve al cuerpo el texto que se fue a las notas y
    vuelve a leer el índice de nombres.
    """
    import json

    monkeypatch.setattr(pdftext, "TEXTOS_DIR", tmp_path)
    cabecera = {
        "tipo": "tomo", "orden": 47, "numero": "47", "autor": "Juliano",
        "obras": "Contra los galileos", "canonico": "Juliano — Contra los galileos",
        "archivo_pdf": "juliano.pdf", "paginas_pdf": 6, "palabras": 3,
        "secciones": [
            {"desde": 1, "hasta": 3, "tipo": "texto", "titulo": "CONTRA LOS GALILEOS"},
            # Guardada como texto: entonces "Notas cartas" no se
            # reconocía como cola de notas
            {"desde": 4, "hasta": 6, "tipo": "texto", "titulo": "Notas cartas"},
        ],
        "indice_nombres": {},
    }
    paginas = [
        {"tipo": "pagina", "pdf": 1, "impresa": None, "seccion": "texto",
         "titulo": "", "obra": "", "cuerpo": "Zeus y los suyos",
         "notas": "el resto del texto que se fue a las notas por medir "
                  "el cuerpo de letra una sola vez para todo el tomo"},
        {"tipo": "pagina", "pdf": 2, "impresa": None,
         "seccion": "indice_nombres", "titulo": "", "obra": "",
         "cuerpo": "ÍNDICE DE NOMBRES\nAarón, 12, 45\nAbel, 7\nAbraham, 9\n"
                   "Adán, 11\nAmón, 14\nApolo, 3\nAres, 5\nAtenea, 8",
         "notas": ""},
    ]
    archivo = tmp_path / "047 - Juliano - Contra los galileos.jsonl"
    with archivo.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(cabecera, ensure_ascii=False) + "\n")
        for p in paginas:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    informe = pdftext.revisar_textos(reparar=True)[0]
    assert any("secciones" in r for r in informe["reparado"])
    assert any("notas desbordadas" in r for r in informe["reparado"])
    assert any("índice de nombres" in r for r in informe["reparado"])

    lineas = archivo.read_text(encoding="utf-8").splitlines()
    nueva = json.loads(lineas[0])
    assert nueva["secciones"][1]["tipo"] == "notas_finales"
    assert nueva["hojas_texto"] == 3          # la cola de notas ya no cuenta
    assert nueva["palabras"] > 20             # el texto volvió al cuerpo
    assert "Aarón" in nueva["indice_nombres"]
    assert json.loads(lineas[1])["notas"] == ""


def test_la_lista_no_abre_los_textos(tmp_path, monkeypatch):
    """
    Los .jsonl son el almacén para el análisis (108 MB): abrirlos uno a
    uno dejaba la ventana de Textos colgada varios segundos. La lista se
    apoya en `_indice.json` y solo relee lo que haya cambiado.
    """
    import json
    import os

    monkeypatch.setattr(pdftext, "TEXTOS_DIR", tmp_path)
    cabecera = {
        "tipo": "tomo", "orden": 3, "numero": "3[4]", "autor": "Heródoto",
        "obras": "Historia", "canonico": "Heródoto — Historia · Libros I-II",
        "paginas_pdf": 400, "hojas_texto": 380, "palabras": 90_000,
        "estado": "nativo", "formato": "impreso", "dificultades": [],
        "indice_nombres": {"Ciro": ["12"]},
    }
    archivo = tmp_path / "003 - Heródoto - Historia.jsonl"
    archivo.write_text(
        json.dumps(cabecera, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    estado = pdftext.estado_de_los_tomos()
    indice = tmp_path / pdftext.INDICE
    assert indice.exists()
    ficha = estado["Heródoto — Historia · Libros I-II"]
    assert ficha["palabras"] == 90_000 and ficha["nombres"] == 1

    # Se estropea el contenido MANTENIENDO fecha y tamaño: si la lista
    # volviera a abrirlo, reventaría o daría otra cosa.
    stat = archivo.stat()
    archivo.write_text("X" * stat.st_size, encoding="utf-8")
    os.utime(archivo, (stat.st_atime, stat.st_mtime))

    otra = pdftext.estado_de_los_tomos()
    assert otra["Heródoto — Historia · Libros I-II"]["palabras"] == 90_000


def test_revision_avisa_del_tomo_equivocado(tmp_path, monkeypatch):
    """
    Mientras la ficha se abría por número de orden (que tres pares
    comparten), la Metamorfosis de Ovidio se guardó como la Geografía
    de Estrabón. La revisión lo canta.
    """
    import json

    monkeypatch.setattr(pdftext, "TEXTOS_DIR", tmp_path)
    cabecera = {
        "tipo": "tomo", "orden": 415, "numero": "415[27]",
        "autor": "Estrabón", "obras": "Geografía",
        "canonico": "Estrabón — Geografía · Libros XV-XVII",
        "archivo_pdf": "Metamorfosis Libros XI-XV - Publio Ovidio Nason.pdf",
        "paginas_pdf": 2, "palabras": 5, "secciones": [], "indice_nombres": {},
    }
    archivo = tmp_path / "415 - Estrabón - Geografía.jsonl"
    archivo.write_text(
        json.dumps(cabecera, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    informe = pdftext.revisar_textos()[0]
    assert any("tomo equivocado" in a for a in informe["avisos"])


def test_secciones_del_indice_no_se_funden(tmp_path):
    """
    Fundir las entradas del índice metía el texto de cada comedia bajo
    el rótulo de la bibliografía anterior. Con índice interno se
    respetan todas; solo se funden las deducidas a ojo.
    """
    import fitz

    ruta = _pdf_ebook(tmp_path / "Aristofanes - Comedias II.pdf", paginas=10)
    doc = fitz.open(ruta)
    doc.set_toc([
        [1, "Comedias II", 1],
        [1, "LAS NUBES", 2],
        [2, "BIBLIOGRAFÍA", 3],
        [3, "Ediciones, traducciones, comentarios", 3],
        [2, "LAS NUBES", 5],                     # aquí empieza el texto
        [1, "LAS AVISPAS", 8],
    ])
    doc.save(tmp_path / "con_indice.pdf")
    doc.close()

    res = pdftext.analizar(tmp_path / "con_indice.pdf", collection.load_excel())
    titulos = [s["titulo"] for s in res.secciones]
    assert "Ediciones, traducciones, comentarios" in titulos
    assert titulos.count("LAS NUBES") >= 1
    # La hoja 6 es texto de la comedia, no de la bibliografía
    assert res.registros[5]["titulo"] == "LAS NUBES"
    assert res.registros[5]["obra"] == "LAS NUBES"
    # La portada del volumen no cuenta como obra
    assert res.obras == ["LAS NUBES", "LAS AVISPAS"]
    # Y ningún rango queda del revés aunque dos entradas compartan hoja
    assert all(s["hasta"] >= s["desde"] for s in res.secciones)


def test_cubierta_y_laminas_no_se_ofrecen_para_ocr():
    """La cubierta y los mapas del final no son texto perdido."""
    a = _analisis(paginas=645, sin_texto=[1, 2, 644, 645])
    assert a.paginas_rescatables == []
    assert not a.completable_con_ocr
    # Pero una hoja de en medio sí que lo es
    b = _analisis(paginas=645, sin_texto=[1, 300, 645])
    assert b.paginas_rescatables == [300]
    assert b.completable_con_ocr


def test_cerrar_la_ventana_corta_el_analisis(tmp_path):
    """Un tomo de 600 páginas no puede seguir procesándose al cerrar."""
    ruta = _pdf_de_prueba(tmp_path / "Homero - Iliada.pdf", paginas=4)
    with pytest.raises(pdftext.AnalisisCancelado):
        pdftext.analizar(
            ruta, collection.load_excel(), cancelado=lambda: True
        )


# ----------------------------------------------------------------------
# Texto latino leído con la tabla del griego (mojibake cp1253)
# ----------------------------------------------------------------------
def test_repara_las_letras_que_se_volvieron_griegas():
    """
    Varios PDF traen "compaρeros", "mαs", "tambiιn": no es que el OCR
    leyera mal, es que los bytes latinos se descodificaron con la tabla
    del griego (Windows-1253). Comprobado carácter a carácter, así que
    la reparación es exacta.
    """
    assert pdftext.repara_mojibake("compaρeros mαs de tienda") == \
        "compañeros más de tienda"
    assert pdftext.repara_mojibake("Concedió tambiιn") == "Concedió también"
    assert pdftext.repara_mojibake("la caballerνa") == "la caballería"
    assert pdftext.repara_mojibake("HIERΣN") == "HIERÓN"
    # La "ú" salió como dígito árabe en esos mismos tomos (370 casos)
    assert pdftext.repara_mojibake("tienda p٥blica") == "tienda pública"


def test_no_toca_el_griego_de_verdad():
    """
    Se repara PALABRA A PALABRA y solo si ya tiene alguna letra latina:
    una palabra griega auténtica no lleva ninguna. Si no, el aparato
    crítico de medio corpus quedaría destrozado.
    """
    for intacto in (
        "ἀλλήλων καὶ ἀμφότεροι",          # griego politónico
        "λόγος πρᾶξις",                     # griego simple
        "phýsis, gnōsis, hýbris",           # transcripción con macron
        "Beiträge zur römischen Tragödien",  # alemán
        "Grèce, siècle, après",              # francés
        "Nøjgaard, Ješništova",              # danés y checo
    ):
        assert pdftext.repara_mojibake(intacto) == intacto


def test_no_toca_el_griego_con_una_o_latina_del_ocr():
    """
    El reconocimiento confunde la Ómicron griega con la O latina, y
    entonces una palabra griega ENTERA cumplía la condición de «tiene
    alguna letra latina»: "᾿Oδυσσεύς" salía como "᾿Oδυóóεύς", con las
    dos sigmas vueltas oes acentuadas (medido en el corpus 2026-08-09).
    La delatan las letras que NO están en la tabla de sustituciones.
    """
    for intacto in (
        "᾿Oδυσσεύς",        # la O es latina; δ, ε, υ, ς son griegas
        "Oδυσσεύς",
        "τιμσρ",            # σ y ρ de la tabla, pero τ y μ no
        "Aριστοτέλης",
    ):
        assert pdftext.repara_mojibake(intacto) == intacto
    # Y lo que sí es mojibake se sigue reparando igual.
    assert pdftext.repara_mojibake("INTRODUCCIΣN") == "INTRODUCCIÓN"


# ----------------------------------------------------------------------
# Los dos alfabetos revueltos (reparación especializada del corpus)
# ----------------------------------------------------------------------
def test_las_etiquetas_de_euclides_vuelven_al_griego():
    """
    Los Elementos nombran los puntos de cada figura con letras griegas,
    y el PDF puso latinas donde la mayúscula se ve idéntica. Que Σ y N
    convivan en la misma etiqueta prueba que la etiqueta es griega.
    """
    assert pdftext.repara_alfabetos("constrúyase el cuadrado ΣN") == \
        "constrúyase el cuadrado ΣΝ"
    assert pdftext.repara_alfabetos("los (triángulos) ABΓ, ΔEZ") == \
        "los (triángulos) ΑΒΓ, ΔΕΖ"
    # Sin una sola letra griega dentro, hace falta saber que el tomo es
    # matemático: "AB" a secas es una palabra en cualquier otro.
    assert pdftext.repara_alfabetos("de AΘ, HK.", etiquetas=True) == \
        "de ΑΘ, ΗΚ."
    assert pdftext.repara_alfabetos("de AΘ, HK.") == "de ΑΘ, HK."


def test_el_canto_homerico_en_letra_griega_no_se_toca():
    """
    Los griegos numeran los cantos de la Ilíada con las 24 letras:
    "(Ι 649)" es el canto IX, verso 649. Apolonio Díscolo cita así.
    Lo distingue la línea: alrededor manda el griego.
    """
    for intacto in (
        "ἀλλ᾿ ὑμεῖς ἔρχεσθε (Ι 649)",
        "καί μ᾿ ἐφίλησε (Ι 481)",
        "Χαρμίδης, Ιππίας Ελάττων, Ιππίας Μείζων",
    ):
        assert pdftext.repara_alfabetos(intacto) == intacto


def test_el_numeral_romano_con_iota_griega_vuelve_al_latin():
    """
    La misma iota, pero en un rótulo español, es la I de un numeral.
    """
    assert pdftext.repara_alfabetos(
        "ΙII. contenido, estilo y complejidad de los Diálogos"
    ) == "III. contenido, estilo y complejidad de los Diálogos"
    assert pdftext.repara_alfabetos("Ι-ΙΙ-ΙΙΙ LOS «OLINTÍACOS»") == \
        "I-II-III LOS «OLINTÍACOS»"


def test_la_vocal_griega_con_tono_dentro_de_palabra_espanola():
    """
    El PDF usa ό por ó. La tabla de cp1253 dice ό→ü, que aquí sería
    "Actürida": manda el parecido, salvo en güe/güi.
    """
    assert pdftext.repara_alfabetos("Actόrida") == "Actórida"
    assert pdftext.repara_alfabetos('"INTRODUCCIΣN"') == '"INTRODUCCIÓN"'


def test_no_se_lleva_por_delante_nombres_ni_cifras():
    """
    Los filtros de la etiqueta (2-4 letras, todas distintas, que no sea
    numeral) están puestos por lo que colaba sin ellos: HEATH y MAZON
    son los editores de Euclides y de la Ilíada, y XII es una cifra.
    """
    for intacto in ("ed. HEATH y MAZON, vol. XII", "II. LOS OLINTÍACOS",
                    "TITO Livio y HAHN"):
        assert pdftext.repara_alfabetos(intacto, etiquetas=True) == intacto
    # El límite conocido: dentro de un tomo matemático, dos mayúsculas
    # sueltas SON una etiqueta, aunque en español fueran una palabra
    # ("NO" = noroeste). Por eso la regla se enciende por tomo y solo la
    # encienden los tres Euclides, donde "NO" nombra dos puntos.
    assert pdftext.repara_alfabetos("el golfo, al NO de Grecia") == \
        "el golfo, al NO de Grecia"


def test_un_tomo_matematico_se_reconoce_por_sus_etiquetas_griegas():
    assert pdftext.cuenta_etiquetas_de_figura("los ΑΒΓ, ΔΕΖ y ΣΝ") == 3
    # "ΑΒ" no vale: todas sus letras tienen gemela latina, así que no
    # prueba nada sobre el tomo.
    assert pdftext.cuenta_etiquetas_de_figura("ΑΒ ΕΖ ΗΚ") == 0
    assert pdftext.cuenta_etiquetas_de_figura("Canta, oh diosa") == 0


def test_quita_los_caracteres_invisibles():
    """
    El espacio duro venía entre TODAS las palabras de algunos tomos (de
    ahí el texto desparramado) y el guion opcional partía palabras.
    """
    assert pdftext.limpia_invisibles("Para\xa0que\xa0los") == "Para que los"
    assert pdftext.limpia_invisibles("cons\xadtitución") == "constitución"
    assert pdftext.limpia_invisibles("dis\xad\ncurso") == "discurso"
    # …pero si al otro lado hay un número de verso, NO se une:
    # "qui¬\n5 sieran" daba "qui5 sieran".
    assert pdftext.limpia_invisibles("qui\xad\n5 sieran") == "qui\n5 sieran"


def test_reparar_no_toca_un_texto_limpio():
    limpio = "Canta, oh diosa, la cólera del Pélida Aquiles."
    assert pdftext.repara_mojibake(limpio) == limpio
    assert pdftext.limpia_invisibles(limpio) == limpio


# ----------------------------------------------------------------------
# Composición de la página (app/formato.py, 2026-08-08)
# ----------------------------------------------------------------------
def test_la_prosa_partida_se_recompone():
    """
    El defecto más común de los `.jsonl`: renglones cortos en mitad del
    párrafo. Medido en el corpus real (Isócrates, Discursos, hoja 177):
    «…las islas42; Pélope» / «hijo de Tántalo, se» eran dos renglones.
    Un renglón corto que NO cierra frase es un trozo suelto y se une.
    """
    from app import formato

    # Fiel a la página real: renglones cortados a unos 52 caracteres y,
    # en medio, los dos trozos sueltos.
    crudo = (
        "dición común contra los bárbaros, y que entonces, por\n"
        "vez primera, Europa levantó un trofeo en Asia; y a\n"
        "causa de estas acciones cambiamos tanto que, en la\n"
        "época anterior, los bárbaros que no tenían éxito en\n"
        "su tierra se creían capaces de gobernar ciudades\n"
        "griegas —Dánao40, huido de Egipto, sometió Argos;\n"
        "los carios se asentaron en las islas42; Pélope\n"
        "hijo de Tántalo, se\n"
        "apoderó de todo el Peloponeso— mientras que, tras\n"
        "aquella guerra, nuestra raza tomó un incremento tan\n"
        "grande como para quitarles ciudades y territorio.\n"
    )
    pagina = formato.componer(crudo)
    assert not pagina.es_verso
    assert len(pagina.bloques) == 1
    tipo, texto = pagina.bloques[0]
    assert tipo == "parrafo"
    assert "Pélope hijo de Tántalo" in texto      # ya no está partido
    assert "\n" not in texto


def test_el_verso_no_se_recompone():
    """
    En los poetas el renglón ES el verso. La decisión NO se toma por el
    largo —la Ilíada mide 57 y hay prosa de 55— sino por la RACHA de
    renglones que llenan la caja: la prosa los encadena, el verso no.
    """
    from app import formato

    poema = (
        "Canta, oh diosa, la cólera del Pélida Aquiles\n"
        "maldita, que causó a los aqueos incontables dolores\n"
        "y precipitó al Hades muchas valientes vidas\n"
        "de héroes, y a ellos mismos los hizo presa de perros\n"
    )
    pagina = formato.componer(poema)
    assert pagina.es_verso
    assert [t for t, _ in pagina.bloques] == ["verso"] * 4


def test_las_llamadas_de_nota_se_despegan():
    """
    En el libro son un superíndice; al extraer el PDF se quedan pegadas
    a la palabra («las islas42;») y ensucian lectura y búsqueda.
    """
    from app import formato

    texto, llamadas = formato.separa_llamadas(
        "sometió Argos; los carios se asentaron en las islas42; y Dánao40."
    )
    assert llamadas == ["42", "40"]
    assert "islas;" in texto and "Dánao." in texto
    # Pero NO se toca lo que no es una llamada
    intacto = "Bekker 1094a y el Libro 42 de la obra"
    assert formato.separa_llamadas(intacto)[0] == intacto


def test_las_marcas_van_al_margen():
    from app import formato

    pagina = formato.componer("b\nc\n403d\n9\nA Alipio, hermano\nY llegó.")
    assert pagina.marcas == ["b", "c", "403d", "9"]
    assert ("titulo", "A Alipio, hermano") in pagina.bloques
    assert all("403d" not in t for _tipo, t in pagina.bloques)


def test_revisar_mide_sin_tocar_nada():
    """`revisar` solo cuenta: los `.jsonl` son la fuente y no se tocan."""
    from app import formato

    registros = [
        {"tipo": "pagina", "cuerpo": "Una frase larga que llena la caja de\n"
                                     "texto y sigue en el renglón siguiente.",
         "notas": ""},
    ]
    datos = formato.revisar(registros)
    assert datos["hojas"] == 1
    assert datos["renglones_del_pdf"] == 2
    assert datos["bloques_compuestos"] >= 1


def test_la_pagina_del_dia_empieza_en_su_pasaje():
    """
    REGRESIÓN (2026-08-08): una hoja da dos o tres pasajes. Enseñándola
    desde arriba, el resumen citaba algo de más abajo y no cuadraba con
    lo primero que se leía.
    """
    from app import formato

    bloques = [
        ("parrafo", "Lo que venía antes y no toca leer todavía."),
        ("parrafo", "Al amanecer atacó el campamento y huyeron los persas."),
        ("parrafo", "Y después continuó la campaña hacia el norte."),
    ]
    recortado = formato.desde_el_pasaje(
        bloques, "Al amanecer atacó el campamento y cayeron muchos"
    )
    assert recortado[0][1].startswith("Al amanecer")
    assert len(recortado) == 2
    # Si el pasaje no se encuentra, se deja la página entera: mejor eso
    # que perderla.
    assert formato.desde_el_pasaje(bloques, "esto no está") == bloques
    assert formato.desde_el_pasaje(bloques, "") == bloques


def test_los_epigrafes_del_margen_parten_el_parrafo():
    """
    REGRESIÓN (2026-08-09, Helénicas hoja 85): la BCG lleva al MARGEN
    unos resumencitos —«Herípidas ataca a Farnabazo»— que el conversor
    a libro electrónico soltó dentro del texto. Sin reconocerlos, la
    página entera quedaba en UN bloque de dos mil caracteres: como los
    recortes cortan ENTRE bloques, no había frontera y el pasaje del
    día se quedaba colgado a media frase.

    Ojo al segundo epígrafe: el renglón que lo precede cierra frase pero
    viene LLENO (medía 70 sobre una caja de 82, y el umbral de «lleno»
    está en 69,7). Por eso lo que decide es si la oración cierra, no el
    ancho del renglón.
    """
    from app import formato

    cuerpo = "\n".join([
        "jinetes. Pues por temer que fuera cercado y sitiado, si se establecía",
        "delante los carros y poniéndose detrás con los jinetes, ordenó avanzar",
        "abatieron al punto a unos cien y los demás huyeron hacia Agesilao, pues",
        "encontraba cerca con los hoplitas.",
        "Heripidas ataca a Farnabazo",
        "Al tercero o cuarto día de este hecho se enteró Espitrídates de que",
        "Farnabazo estaba acampado en Cave, una aldea grande, que distaba unos",
        "ciento sesenta estadios, e inmediatamente se lo comunicó a Herípidas.",
        "Por cierto, no le ocurrió nada más grave a Agesilao en esta campaña de",
        "campaña que la deserción de Espitrídates, Megabates y los paflagonios.",
        "Entrevista de Agesilao y Farnabazo",
        "Había un tal Apolófanes de Cícico, que desde antiguo era huésped de",
        "Farnabazo y también por aquel tiempo mantenía relaciones de hospitalidad",
        "con Agesilao. Este hombre dijo a Agesilao que esperaba reunir a",
    ])
    bloques = formato.componer(cuerpo).bloques
    titulos = [t for tipo, t in bloques if tipo == "titulo"]
    assert titulos == ["Heripidas ataca a Farnabazo",
                       "Entrevista de Agesilao y Farnabazo"]

    # Y con esas fronteras, los recortes ya tienen dónde cortar.
    assert formato.acaba_en_parrafo(bloques)[-1][1].endswith(
        "Megabates y los paflagonios.")
    assert formato.empieza_en_frase(bloques)[0][1].startswith("Pues por temer")


def test_un_renglon_partido_no_se_confunde_con_un_epigrafe():
    """
    Lo único que se parece a un epígrafe es un renglón partido. Lo
    delatan la palabra de enlace del final y la minúscula del siguiente.
    """
    from app import formato

    # Hacen falta bastantes renglones LLENOS: con cuatro, la página se
    # toma por verso —la prosa se reconoce por la racha de renglones que
    # llenan la caja— y entonces cada renglón va a su bloque.
    cuerpo = "\n".join([
        "y por eso todos ellos se marcharon de la ciudad aquella misma noche",
        "sin que nadie de los que allí quedaban se diera cuenta de que aquellos",
        "hombres, a los que tanto habían temido durante el asedio, ya no iban a",
        "volver nunca más a las murallas ni a los campos de los alrededores.",
        "Pero Agesilao, que",
        "no quería dejarlo estar, mandó llamar a los suyos y les habló así de",
        "la conveniencia de perseguirlos hasta el río antes de que llegara la",
        "noche y se perdiera del todo el rastro que habían dejado al huir.",
    ])
    bloques = formato.componer(cuerpo).bloques
    assert not [t for tipo, t in bloques if tipo == "titulo"]
    assert "Pero Agesilao, que no quería" in " ".join(t for _, t in bloques)
