"""
test_rag.py
===========
Buscador sobre el texto de los tomos (app/rag.py, tarea RAG-1).

Todo corre sobre un corpus INVENTADO en un directorio temporal: el de
verdad son 250 MB del usuario y no está en el repositorio. Lo que se
prueba aquí es que el índice trocea, cita, filtra y se actualiza como
debe — no la calidad de un tomo concreto.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import rag  # noqa: E402


# --------------------------------------------------------------------
# Corpus de prueba
# --------------------------------------------------------------------

def _hoja(pdf, cuerpo, **extra):
    reg = {
        "tipo": "pagina", "pdf": pdf, "impresa": None, "seccion": "texto",
        "titulo": "", "obra": "", "versos": [], "cuerpo": cuerpo,
        "notas": "", "capitulos": [], "llamadas": [],
    }
    reg.update(extra)
    return reg


def _escribe_tomo(carpeta: Path, nombre: str, cabecera: dict, hojas: list):
    ruta = carpeta / nombre
    with ruta.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(cabecera, ensure_ascii=False) + "\n")
        for hoja in hojas:
            fh.write(json.dumps(hoja, ensure_ascii=False) + "\n")
    return ruta


@pytest.fixture()
def indice(tmp_path, monkeypatch):
    """Índice recién hecho sobre dos tomos de mentira."""
    textos = tmp_path / "TextosTomos"
    textos.mkdir()
    monkeypatch.setattr(rag, "TEXTOS_DIR", textos)

    _escribe_tomo(
        textos, "150 - Homero - Ilíada.jsonl",
        {
            "tipo": "tomo", "orden": 150, "numero": "150", "autor": "Homero",
            "obras": "Ilíada", "canonico": "Homero — Ilíada",
            "formato": "ebook", "hojas_texto": 3, "palabras": 60,
            "indice_nombres": {
                "Aquiles": ["I 1", "I 58", "XXII 330"],
                "Héctor": ["VI 440"],
                "y ss": ["12"],          # basura del OCR: NO debe entrar
                "XV": ["3"],             # romano suelto: tampoco
            },
        },
        [
            _hoja(10, "Canta, oh diosa, la cólera del Pélida Aquiles.\n"
                      "Cólera funesta que causó infinitos males a los aqueos "
                      "y precipitó al Hades muchas almas valerosas de "
                      "héroes, a quienes hizo presa de perros y pasto de "
                      "aves, mientras se cumplía la voluntad de Zeus, desde "
                      "que por vez primera se separaron disputando el "
                      "Atrida, rey de hombres, y el divino Aquiles, el de "
                      "los pies ligeros, en el campamento de los griegos.",
                  impresa=23, obra="Canto I", versos=["1", "2"]),
            _hoja(11, "Así habló Héctor, y los troyanos le aclamaron.",
                  impresa=24, obra="Canto VI",
                  notas="Sobre el epíteto de Héctor, véase la introducción."),
            _hoja(12, "Nota 45. El escudo de Aquiles es obra de Hefesto.",
                  seccion="notas_finales", nota="45"),
            # Hoja de RÓTULO: dos palabras. BM25 divide por la longitud,
            # así que sin corregirlo esto salía por delante del canto.
            _hoja(13, "XVIII\nAQUILES", impresa=99, obra="Canto XVIII"),
        ],
    )
    _escribe_tomo(
        textos, "002 - Jenofonte - Helénicas.jsonl",
        {
            "tipo": "tomo", "orden": 2, "numero": "2", "autor": "Jenofonte",
            "obras": "Helénicas", "canonico": "Jenofonte — Helénicas",
            "formato": "escaneo", "hojas_texto": 1, "palabras": 20,
            "indice_nombres": {"Aquiles": ["II 3"]},
        },
        [_hoja(5, "Los lacedemonios enviaron embajadores a los atenienses "
                  "y a los demás griegos.",
               impresa=101, obra="Libro I")],
    )

    idx = rag.Indice(tmp_path / "textos.db")
    idx.indexar()
    yield idx
    idx.close()


# --------------------------------------------------------------------
# Piezas puras
# --------------------------------------------------------------------

def test_fts5_disponible():
    """Sin FTS5 no hay buscador; si esto falla, es el intérprete."""
    assert rag.fts5_disponible()


def test_troceado_respeta_el_verso():
    """
    El renglón es la unidad de cita en los poetas: los saltos de línea
    tienen que sobrevivir al troceado, y una palabra pegada a un salto
    no puede contar como dos ni como una sola con la siguiente.
    """
    texto = "\n".join(f"verso numero {i}" for i in range(200))
    trozos = list(rag._trozos(texto))
    assert len(trozos) > 1
    assert "\n" in trozos[0]
    # Ninguna palabra se pierde ni se inventa
    assert trozos[0].split()[:3] == ["verso", "numero", "0"]
    for trozo in trozos:
        assert len(trozo.split()) <= rag.PALABRAS_PASAJE


def test_troceado_solapa():
    """El solape evita que una frase partida por el corte no se halle."""
    palabras = [f"p{i}" for i in range(400)]
    trozos = list(rag._trozos(" ".join(palabras)))
    primero = trozos[0].split()
    segundo = trozos[1].split()
    comunes = set(primero) & set(segundo)
    assert len(comunes) == rag.SOLAPE


def test_troceado_texto_corto_va_entero():
    assert list(rag._trozos("dos palabras")) == ["dos palabras"]
    assert list(rag._trozos("   ")) == []


def test_consulta_quita_palabras_vacias():
    assert rag.a_consulta_fts("el alma es inmortal") == '"alma" "inmortal"'


def test_consulta_respeta_la_frase_entre_comillas():
    """Dentro de comillas manda el usuario: no se quita nada."""
    assert rag.a_consulta_fts('"la ira de Aquiles"') == '"la ira de Aquiles"'


def test_consulta_solo_vacias_no_se_queda_en_nada():
    assert rag.a_consulta_fts("de los") == '"de" "los"'


def test_consulta_escapa_la_sintaxis_de_fts():
    """
    Un `*`, un `:` o un `NEAR` sueltos rompían la consulta con un error
    de SQLite en vez de buscar. Todo signo se escapa.
    """
    salida = rag.a_consulta_fts('NEAR ^tomo: "cita"')
    assert '"NEAR"' in salida and '"tomo"' in salida
    assert "^" not in salida and ":" not in salida


def test_consulta_prefijo():
    assert rag.a_consulta_fts("lacede*") == '"lacede"*'


def test_reconoce_una_pregunta():
    assert rag.es_pregunta("¿qué tomo habla de los lacedemonios?")
    assert rag.es_pregunta("cómo se educaba a los niños")
    assert rag.es_pregunta("dónde muere Sócrates")
    assert not rag.es_pregunta("el libro de los muertos")
    assert not rag.es_pregunta("la ira de Aquiles")


def test_en_una_pregunta_se_cae_el_andamio():
    """
    «¿Qué TOMO HABLA de los lacedemonios?»: «tomo» y «habla» son cómo se
    pregunta, no lo que se busca. Exigirlas devolvía páginas llenas de
    «tomó» sin un solo lacedemonio.
    """
    assert rag.a_consulta_fts(
        "¿qué tomo habla de los lacedemonios?"
    ) == '"lacedemonios"'


def test_fuera_de_una_pregunta_el_andamio_es_texto():
    """
    «libro» y «muertos» son palabras de los tomos: en una búsqueda
    normal no se tocan.
    """
    assert rag.a_consulta_fts("el libro de los muertos") == \
        '"libro" "muertos"'


def test_es_nombre_filtra_la_basura_del_ocr():
    assert rag._es_nombre("Aquiles")
    assert not rag._es_nombre("y ss")
    assert not rag._es_nombre("XV")       # romano suelto partido por el OCR
    assert not rag._es_nombre("a")


def test_marcar_no_descoloca_con_tildes():
    """
    El resaltado se calcula sin tildes y se pinta sobre el original: si
    las posiciones no coinciden, la marca cae sobre la letra de al lado.
    """
    trozos = rag.marcar("La cólera del Pélida Aquiles", "colera")
    assert "".join(t for t, _ in trozos) == "La cólera del Pélida Aquiles"
    assert [t for t, marca in trozos if marca] == ["cólera"]


def test_marcar_con_saltos_de_linea():
    texto = "primer verso\nsegundo verso"
    trozos = rag.marcar(texto, "segundo")
    assert "".join(t for t, _ in trozos) == texto
    assert [t for t, marca in trozos if marca] == ["segundo"]


def test_resaltar_recorta_alrededor():
    texto = "palabra " * 60 + "AGUJA " + "palabra " * 60
    recorte = rag.resaltar(texto, "aguja", ancho=80)
    assert "AGUJA" in recorte and len(recorte) <= 84


# --------------------------------------------------------------------
# Índice
# --------------------------------------------------------------------

def test_indexa_los_dos_tomos(indice):
    datos = indice.resumen()
    assert datos["tomos"] == 2
    assert datos["pasajes"] >= 4
    assert datos["pendientes"] == 0


def test_busca_y_cita_con_la_pagina_impresa(indice):
    """
    La cita SIEMPRE prefiere la página impresa: la hoja del PDF no
    existe en el libro de papel y no sirve para citar.
    """
    hits = indice.buscar("cólera Pélida")
    assert hits
    primero = hits[0]
    assert primero.canonico == "Homero — Ilíada"
    assert "pág. 23" in primero.cita()
    assert "Tomo 150" in primero.cita()


def test_encabezado_no_repite_el_autor(indice):
    """El canónico ya lo lleva: salía "Homero — Homero — Ilíada"."""
    hit = indice.buscar("cólera")[0]
    assert hit.encabezado() == "Homero — Ilíada"
    assert hit.encabezado().count("Homero") == 1


def test_busca_sin_tildes(indice):
    """`colera` tiene que encontrar `cólera` (tokenizador sin tildes)."""
    assert indice.buscar("colera")


def test_las_notas_van_aparte(indice):
    """
    Las notas son 66.781 hojas de las 101.402 del corpus: mezcladas con
    el texto del autor lo taparían. Se indexan como clase 'notas' y se
    pueden excluir.
    """
    con_notas = indice.buscar("Hefesto escudo", incluir_notas=True)
    sin_notas = indice.buscar("Hefesto escudo", incluir_notas=False)
    assert con_notas and not sin_notas
    assert con_notas[0].clase == "notas"
    assert "nota 45" in con_notas[0].cita()


def test_nota_al_pie_es_pasaje_propio(indice):
    """El cuerpo y la nota al pie de la misma hoja no se mezclan."""
    hit = indice.buscar("epíteto", incluir_notas=True)[0]
    assert hit.clase == "notas"
    assert "troyanos" not in hit.texto


def test_el_texto_gana_a_la_nota_en_el_ranking(indice):
    """A igualdad de coincidencia manda lo que escribió el autor."""
    hits = indice.buscar("Aquiles", incluir_notas=True)
    assert hits[0].clase == rag.CLASE_OBRA


def test_un_rotulo_no_gana_al_texto(indice):
    """
    Medido con el corpus real: los cuatro primeros resultados de
    «Aquiles» eran hojas de rótulo de dos palabras, porque BM25 divide
    por la longitud del pasaje. Se quedan, pero detrás.
    """
    hits = indice.buscar("Aquiles", incluir_notas=False)
    assert hits
    assert len(hits[0].texto.split()) > 5
    assert any(h.texto.strip() == "XVIII\nAQUILES" for h in hits)


def test_peso_por_longitud():
    assert rag._peso_largo(2) < rag._peso_largo(40) < rag._peso_largo(200)
    assert rag._peso_largo(200) == 1.0


def test_filtra_por_tomo_y_por_autor(indice):
    assert indice.buscar("Aquiles", tomo="Jenofonte — Helénicas") == []
    hits = indice.buscar("lacedemonios", autor="Jenofonte")
    assert hits and hits[0].canonico == "Jenofonte — Helénicas"


def test_una_hoja_no_sale_repetida(indice):
    """
    Los pasajes se solapan a propósito; sin deduplicar, la misma página
    aparecía dos y tres veces seguidas.
    """
    hits = indice.buscar("Aquiles", incluir_notas=True, limite=20)
    claves = [(h.canonico, h.hoja, h.clase) for h in hits]
    assert len(claves) == len(set(claves))


def test_hoja_completa_recompone_sin_repetir(indice):
    hoja = indice.hoja_completa("Homero — Ilíada", 10)
    assert hoja["impresa"] == 23
    assert hoja["cuerpo"].count("Cólera funesta") == 1
    assert hoja["obra"] == "Canto I"


def test_hoja_completa_de_lo_que_no_existe(indice):
    assert indice.hoja_completa("Homero — Ilíada", 9999) == {}


def test_indice_de_nombres(indice):
    """
    La concordancia del traductor responde «¿qué tomo habla de X?» sin
    leer el texto y sin IA: ya trae la localización canónica.
    """
    filas = indice.buscar_nombres("Aquiles")
    assert {f["canonico"] for f in filas} == {
        "Homero — Ilíada", "Jenofonte — Helénicas"
    }
    # Manda el que más veces lo cita
    assert filas[0]["canonico"] == "Homero — Ilíada"
    assert filas[0]["cuantas"] == 3
    assert "XXII 330" in filas[0]["refs"]


def test_indice_de_nombres_sin_basura(indice):
    assert indice.buscar_nombres("y ss") == []
    assert not any(
        f["nombre"] == "XV" for f in indice.buscar_nombres("XV")
    )


def test_sugerencias_de_nombres(indice):
    assert "Aquiles" in indice.sugerir_nombres("aqui")
    assert indice.sugerir_nombres("a") == []      # una letra no sugiere


def test_tomos_con_lista_todos_los_tomos(indice):
    """
    La vista por tomos es COMPLETA: no hay tope de candidatos.

    REGRESIÓN (2026-08-05): pidiendo pasajes sueltos había que cortar
    por los 400 mejores de BM25, y los tomos que caían fuera no
    aparecían jamás — buscando «lacedemonios» (2.510 pasajes en el
    corpus real) faltaban las obras menores de Jenofonte.
    """
    filas = indice.tomos_con("griegos", incluir_notas=True)
    assert {f["canonico"] for f in filas} == {
        "Homero — Ilíada", "Jenofonte — Helénicas"
    }
    # Y con el recuento de cada uno, que es lo que ordena la lista
    assert all(f["pasajes"] >= 1 and f["hojas"] >= 1 for f in filas)
    homero = next(f for f in filas if f["canonico"] == "Homero — Ilíada")
    assert homero["numero"] == "150"


def test_tomos_con_respeta_el_filtro_de_notas(indice):
    con = indice.tomos_con("Hefesto", incluir_notas=True)
    sin = indice.tomos_con("Hefesto", incluir_notas=False)
    assert con and not sin


def test_tomos_con_cuadra_con_los_pasajes(indice):
    """
    Las dos vistas usan la MISMA expresión: si cada una aflojara por su
    cuenta, el recuento de una no cuadraría con lo que abre la otra.
    """
    filas = indice.tomos_con("trirreme Aquiles", incluir_notas=True)
    assert filas
    canonico = filas[0]["canonico"]
    hits = indice.buscar(
        "trirreme Aquiles", tomo=canonico, por_tomo=0, limite=200
    )
    assert hits
    assert all(h.canonico == canonico for h in hits)
    # Un pasaje por hoja: el recuento de hojas es el que se ve al abrir
    assert len(hits) <= filas[0]["hojas"]


def test_tomos_con_sin_consulta(indice):
    assert indice.tomos_con("") == []


def test_afloja_soltando_las_palabras_comunes(indice):
    """
    Si no hay ningún pasaje con TODAS las palabras, se sueltan las más
    comunes y se siguen exigiendo las raras — nunca al revés: con OR
    mandaba BM25 y ganaba quien repetía mucho la palabra vulgar.
    """
    estado: dict = {}
    hits = indice.buscar("trirreme Aquiles", estado=estado)
    assert hits
    assert estado["modo"] == "algunas"
    # "trirreme" no está en ningún pasaje: se descarta y queda "Aquiles"
    assert estado["palabras"] == ["Aquiles"]
    assert all("quiles" in h.texto.lower() for h in hits)


def test_avisa_de_que_ha_aflojado(indice):
    estado: dict = {}
    indice.buscar("cólera Aquiles", estado=estado)
    assert estado["modo"] == "todas"       # esas dos SÍ están juntas
    indice.buscar("", estado=estado)
    assert estado["modo"] == "vacia"


def test_consulta_vacia_no_devuelve_todo(indice):
    assert indice.buscar("") == []
    assert indice.buscar("   ") == []


# --------------------------------------------------------------------
# Reindexado incremental
# --------------------------------------------------------------------

def test_segunda_pasada_no_repite_trabajo(indice):
    """Se puede seguir analizando PDF: solo entra lo nuevo o cambiado."""
    res = indice.indexar()
    assert res.tomos == 0
    assert res.saltados == 2
    assert indice.resumen()["tomos"] == 2


def test_un_texto_nuevo_entra_solo(indice, tmp_path):
    _escribe_tomo(
        rag.TEXTOS_DIR, "003 - Safo - Poemas.jsonl",
        {"tipo": "tomo", "orden": 3, "numero": "3", "autor": "Safo",
         "obras": "Poemas", "canonico": "Safo — Poemas", "indice_nombres": {}},
        [_hoja(1, "Me parece igual a los dioses aquel hombre.", impresa=7)],
    )
    res = indice.indexar()
    assert res.tomos == 1 and res.saltados == 2
    assert indice.buscar("dioses")[0].canonico == "Safo — Poemas"


def test_un_texto_reanalizado_se_reemplaza(indice):
    """
    Al volver a analizar un tomo, sus pasajes viejos se van: si no,
    quedarían citas de una extracción que ya no existe.
    """
    ruta = rag.TEXTOS_DIR / "002 - Jenofonte - Helénicas.jsonl"
    _escribe_tomo(
        rag.TEXTOS_DIR, ruta.name,
        {"tipo": "tomo", "orden": 2, "numero": "2", "autor": "Jenofonte",
         "obras": "Helénicas", "canonico": "Jenofonte — Helénicas",
         "indice_nombres": {}},
        [_hoja(5, "Texto completamente distinto sobre trirremes.", impresa=101)],
    )
    os.utime(ruta, (0, 0))               # fecha distinta → hay que releerlo
    indice.indexar()
    assert indice.buscar("lacedemonios") == []
    assert indice.buscar("trirremes")
    assert indice.resumen()["tomos"] == 2      # no se ha duplicado


def test_un_texto_borrado_sale_del_indice(indice):
    (rag.TEXTOS_DIR / "002 - Jenofonte - Helénicas.jsonl").unlink()
    res = indice.indexar()
    assert res.borrados == 1
    assert indice.buscar("lacedemonios") == []
    assert indice.resumen()["tomos"] == 1


def test_pendientes_cuenta_lo_que_falta(indice):
    _escribe_tomo(
        rag.TEXTOS_DIR, "004 - Píndaro - Odas.jsonl",
        {"tipo": "tomo", "orden": 4, "canonico": "Píndaro — Odas",
         "autor": "Píndaro", "indice_nombres": {}},
        [_hoja(1, "Lo mejor es el agua.")],
    )
    assert indice.pendientes() == 1
    indice.indexar()
    assert indice.pendientes() == 0


def test_texto_roto_no_tumba_el_indexado(indice):
    """
    Más vale un índice con 171 tomos que ninguno: un `.jsonl` a medias
    se anota y se sigue.
    """
    (rag.TEXTOS_DIR / "999 - Roto - Roto.jsonl").write_text(
        "{esto no es json\n", encoding="utf-8"
    )
    res = indice.indexar()
    assert res.errores
    assert indice.resumen()["tomos"] == 2


def test_forzar_rehace_todo(indice):
    res = indice.indexar(forzar=True)
    assert res.tomos == 2 and res.saltados == 0
    assert indice.resumen()["tomos"] == 2      # sin duplicar


def test_cambio_de_version_reconstruye(tmp_path, monkeypatch, indice):
    """
    Si cambia cómo se trocea, los pasajes viejos no son comparables:
    subir `VERSION` tiene que vaciar el índice, no mezclar.
    """
    ruta = indice.ruta
    indice.close()
    monkeypatch.setattr(rag, "VERSION", rag.VERSION + 1)
    otro = rag.Indice(ruta)
    try:
        assert otro.resumen()["tomos"] == 0
    finally:
        otro.close()


def test_cancelar_corta_el_indexado(tmp_path, monkeypatch, indice):
    indice.close()
    otro = rag.Indice(tmp_path / "otro.db")
    try:
        otro.indexar(cancelado=lambda: True)
        assert otro.resumen()["tomos"] == 0
    finally:
        otro.close()


def test_el_progreso_informa(indice, tmp_path):
    pasos = []
    otro = rag.Indice(tmp_path / "tercero.db")
    try:
        otro.indexar(progreso=lambda fase, hechas, total: pasos.append(
            (fase, hechas, total)))
    finally:
        otro.close()
    assert pasos and pasos[-1][1] == pasos[-1][2] == 2


def test_no_toca_la_base_del_monitor(indice):
    """
    El índice es un archivo APARTE y reconstruible: borrarlo no puede
    perder ni un precio ni un lote.
    """
    assert indice.ruta.name.endswith(".db")
    assert "tc_monitor" not in indice.ruta.name


# ----------------------------------------------------------------------
# Lo que salió del banco de pruebas de consultas (2026-08-08)
# ----------------------------------------------------------------------
def test_el_acento_suelto_no_parte_la_palabra():
    """
    Un acento puede llegar como letra sola («ó») o como letra + signo
    («o» + U+0301), que es lo que pega Windows al copiar de un PDF. Sin
    recomponerlo, el signo caía en la limpieza de puntuación y PARTÍA la
    palabra: «nómos» se buscaba como «no mos» (1 tomo en vez de 50).
    """
    import unicodedata

    junta = rag.a_consulta_fts("nómos")
    suelta = rag.a_consulta_fts(unicodedata.normalize("NFD", "nómos"))
    assert junta == suelta == '"nómos"'


def test_un_prefijo_corto_no_barre_el_indice():
    """«a*» daba 121.320 pasajes de los 172 tomos en 3,2 s."""
    assert rag.a_consulta_fts("a*") == '"a"'          # sin el comodín
    assert rag.a_consulta_fts("de*") == '"de"'
    assert rag.a_consulta_fts("lac*") == '"lac"*'     # tres letras, sí


def test_avisa_si_solo_hay_palabras_de_enlace():
    assert rag.solo_palabras_vacias("de los")
    assert rag.solo_palabras_vacias("el la los las")
    assert rag.solo_palabras_vacias("¿qué tomo?")     # solo el andamio
    assert not rag.solo_palabras_vacias("lacedemonios")
    assert not rag.solo_palabras_vacias("de los lacedemonios")


def test_el_griego_se_encuentra_con_y_sin_acentos():
    """
    El tokenizador de SQLite quita las tildes LATINAS pero no las
    griegas: el índice guarda «λόγοσ» con su acento y escribir «λογος»
    no encontraba nada. A cada pasaje con griego se le añade —solo para
    buscar— una copia de sus palabras sin acentos.
    """
    plegado = rag._plegado("la πρᾶξις de Aristóteles")
    assert plegado is not None
    assert "πρᾶξις" in plegado          # se conserva lo original
    assert "πραξις" in plegado          # y se añade sin acentos
    # Sin griego no se guarda copia: son 87 M de caracteres de corpus
    assert rag._plegado("Canta, oh diosa, la cólera") is None
    assert rag._plegado("") is None


def test_el_indice_de_una_version_vieja_se_rehace(tmp_path, monkeypatch):
    """
    Al cambiar el esquema, los `CREATE ... IF NOT EXISTS` no tocan lo
    que ya existe: hay que tirar el índice y sus disparadores a mano. Y
    EN EL SITIO, sin borrar el archivo, que la aplicación puede tenerlo
    abierto (en Windows entonces no se deja).
    """
    textos = tmp_path / "TextosTomos"
    textos.mkdir()
    monkeypatch.setattr(rag, "TEXTOS_DIR", textos)
    _escribe_tomo(
        textos, "001 - Homero - Ilíada.jsonl",
        {"tipo": "tomo", "orden": 1, "numero": "1", "autor": "Homero",
         "obras": "Ilíada", "canonico": "Homero — Ilíada",
         "indice_nombres": {}},
        [_hoja(1, "Canta, oh diosa, la cólera del Pélida Aquiles.")],
    )
    ruta = tmp_path / "textos.db"
    idx = rag.Indice(ruta)
    idx.indexar()
    assert idx.buscar("cólera")
    idx._con.execute(
        "INSERT OR REPLACE INTO meta(clave, valor) VALUES('version', '0')"
    )
    idx._con.commit()
    idx.close()

    otro = rag.Indice(ruta)              # abre uno de "otra versión"
    try:
        assert ruta.exists()             # NO se ha borrado el archivo
        assert otro.resumen()["tomos"] == 0
        otro.indexar()
        assert otro.buscar("cólera")     # y se reconstruye entero
    finally:
        otro.close()


# ----------------------------------------------------------------------
# Pasaje del día (2026-08-08)
# ----------------------------------------------------------------------
def test_el_pasaje_del_dia_no_cambia_en_todo_el_dia(indice):
    """
    El mismo durante veinticuatro horas: la elección se guarda en
    `meta` y se rehace sola al cambiar la fecha.
    """
    uno = indice.pasaje_del_dia("2026-08-08")
    otro = indice.pasaje_del_dia("2026-08-08")
    assert uno and uno["id"] == otro["id"]
    # Con la fecha del día siguiente puede tocar otro, pero nunca falla
    manana = indice.pasaje_del_dia("2026-08-09")
    assert manana and "titulo" in manana


def test_el_pasaje_del_dia_trae_todo_lo_que_hace_falta(indice):
    ficha = indice.pasaje_del_dia("2026-08-08")
    assert ficha["texto"]
    assert ficha["canonico"]
    assert ficha["titulo"]
    assert ficha["descripcion"]
    assert ficha["pagina"].get("cuerpo")     # la página entera, para leerla


def test_el_titulo_sale_del_tomo_y_no_se_inventa():
    """
    Aquí no hay ninguna IA: el título son los NOMBRES del índice del
    traductor, o el rótulo de la sección, o el título del tomo. Nunca
    algo redactado.
    """
    assert rag.titulo_de_pasaje(
        "texto", "Libro I", "", ["Licurgo", "Esparta"], "Jenofonte — X"
    ) == "Licurgo y Esparta"
    # Con rótulo que dice algo, se acompaña
    assert rag.titulo_de_pasaje(
        "texto", "", "Sobre la educación", ["Licurgo"], "Jenofonte — X"
    ) == "Licurgo · Sobre la educación"
    # Sin nombres, el rótulo… si no es una etiqueta vacía
    assert rag.titulo_de_pasaje(
        "texto", "", "Sobre la educación", [], "Jenofonte — X"
    ) == "Sobre la educación"
    assert rag.titulo_de_pasaje(
        "texto", "Libro III", "", [], "Jenofonte — X"
    ) == "Jenofonte — X"


def test_los_rotulos_vacios_no_valen_como_titulo():
    for vacio in ("Libro I", "III", "Canto XVIII", "Vol. II", "", "  ", "7"):
        assert not rag._rotulo_sirve(vacio), vacio
    for bueno in ("Sobre la educación", "República de los lacedemonios"):
        assert rag._rotulo_sirve(bueno)


def test_la_descripcion_es_una_frase_del_propio_pasaje():
    """
    Se ENTRESACA, no se redacta: redactar exigiría un modelo de
    lenguaje, y un resumen inventado en una biblioteca es peor que
    ninguno.
    """
    texto = (
        "Era de noche. Licurgo dispuso que los muchachos de Esparta "
        "se educaran juntos y en común, lejos de sus casas. Llovía."
    )
    frase = rag.descripcion_de_pasaje(texto, ["Licurgo", "Esparta"])
    assert frase in texto
    assert "Licurgo" in frase        # gana la frase con los nombres
    assert rag.descripcion_de_pasaje("", []) == ""


# --------------------------------------------------------------------
# El índice de nombres NO es una nota al pie (2026-08-14)
# --------------------------------------------------------------------
# Un índice va en letra menor que el texto, así que el separador de
# notas al pie se llevaba la hoja entera al campo `notas` del .jsonl y
# el indexador la marcaba como clase 'notas'. Resultado: con «Con
# notas» desmarcado el índice desaparecía. Y no en todos los tomos —
# solo en aquellos cuyo PDF lo compuso más pequeño—, así que el mismo
# contenido se encontraba o no según el tomo (60 tomos de cuerpo, 22 de
# notas). Caso real: «mirmidones» no encontraba «Ovidio — Metamorfosis
# · Libros XI-XV», que lo cita en su índice.

@pytest.fixture()
def indice_de_letra_menor(tmp_path, monkeypatch):
    """Tomo cuyo índice de nombres cayó entero en el campo `notas`."""
    textos = tmp_path / "TextosTomos"
    textos.mkdir()
    monkeypatch.setattr(rag, "TEXTOS_DIR", textos)
    _escribe_tomo(
        textos, "415 - Ovidio - Metamorfosis.jsonl",
        {
            "tipo": "tomo", "orden": 415, "numero": "415.2", "autor": "Ovidio",
            "obras": "Metamorfosis",
            "canonico": "Ovidio — Metamorfosis · Libros XI-XV",
            "formato": "ebook", "hojas_texto": 2, "palabras": 40,
            "indice_nombres": {"Mirmidones": ["VII 654"]},
        },
        [
            _hoja(8, "Ceix y Alcíone se transformaron en aves marinas.",
                  impresa=310, obra="LIBRO XI",
                  notas="Sobre Ceix, véase la introducción de este volumen."),
            # El índice: NADA en cuerpo, todo en notas (letra menor).
            _hoja(132, "", seccion="indice_nombres",
                  notas="Minturno: ciudad de Campania XV 716.\n"
                        "Mirmidones: hombres surgidos de hormigas VII 654.\n"
                        "Mirra: hija de Cíniras X 312."),
            # Y una hoja de índice PARTIDA entre los dos campos.
            _hoja(133, "Néstor: rey de Pilos XII 169.", seccion="indice_nombres",
                  notas="Níobe: hija de Tántalo VI 148."),
        ],
    )
    idx = rag.Indice(tmp_path / "textos.db")
    idx.indexar()
    yield idx
    idx.close()


def test_el_indice_de_nombres_se_busca_sin_marcar_notas(indice_de_letra_menor):
    idx = indice_de_letra_menor
    sin = idx.tomos_con("mirmidones", incluir_notas=False)
    assert [t["canonico"] for t in sin] == [
        "Ovidio — Metamorfosis · Libros XI-XV"
    ]
    # Es aparato, no obra: el índice lo escribió el traductor. Pero
    # aparato entra en la búsqueda mientras no se pidan solo obras.
    hit = idx.buscar("mirmidones", incluir_notas=False)[0]
    assert hit.clase == rag.CLASE_APARATO


def test_la_nota_al_pie_de_verdad_sigue_siendo_nota(indice_de_letra_menor):
    """
    El arreglo NO puede sacar notas auténticas al texto del autor: la
    hoja 8 lleva cuerpo y nota al pie, y la nota se queda en su sitio.
    """
    idx = indice_de_letra_menor
    assert not idx.buscar("introducción volumen", incluir_notas=False)
    nota = idx.buscar("introducción volumen", incluir_notas=True)
    assert nota and nota[0].clase == "notas"
    assert "Alcíone" not in nota[0].texto


def test_la_hoja_de_indice_partida_se_recompone_entera(indice_de_letra_menor):
    """
    Los dos campos son la MISMA lista: se juntan antes de trocear. Si se
    trocearan por separado habría dos trozos «0» en la hoja y al
    recomponerla se intercalarían.
    """
    hoja = indice_de_letra_menor.hoja_completa(
        "Ovidio — Metamorfosis · Libros XI-XV", 133
    )
    assert "Néstor" in hoja["cuerpo"] and "Níobe" in hoja["cuerpo"]
    assert not hoja.get("notas")
    assert hoja["cuerpo"].index("Néstor") < hoja["cuerpo"].index("Níobe")


def test_las_secciones_sin_notas_al_pie_son_pocas_a_proposito():
    """
    `bibliografia` e `introduccion` SÍ pueden llevar notas al pie de
    verdad: reclasificarlas sacaría notas auténticas al texto del autor.
    """
    assert rag.SECCIONES_SIN_NOTAS_AL_PIE == ("indice_nombres", "indice_general")
    assert "notas_finales" not in rag.SECCIONES_SIN_NOTAS_AL_PIE
    assert "bibliografia" not in rag.SECCIONES_SIN_NOTAS_AL_PIE


def test_el_pasaje_del_dia_se_resortea_si_el_guardado_ya_no_existe(indice):
    """
    Se guarda por IDENTIFICADOR, y reindexar un tomo borra sus pasajes y
    los reinserta con otros: el del día podía quedar colgando y la
    ventana salía vacía.
    """
    import json as _json

    hoy = "2026-08-14"
    assert indice.pasaje_del_dia(hoy)
    indice._escribir_meta(
        "pasaje_del_dia", _json.dumps({"fecha": hoy, "id": 999_999})
    )
    ficha = indice.pasaje_del_dia(hoy)
    assert ficha and ficha["id"] != 999_999
    # Y la elección nueva queda guardada, no se re-sortea en cada visita.
    assert indice.pasaje_del_dia(hoy)["id"] == ficha["id"]


def test_rehacer_el_indice_olvida_el_pasaje_del_dia(indice):
    """Al vaciar, los identificadores se reparten de nuevo desde el 1."""
    indice.pasaje_del_dia("2026-08-14")
    assert indice._leer_meta("pasaje_del_dia")
    indice._vaciar()
    assert indice._leer_meta("pasaje_del_dia") is None
