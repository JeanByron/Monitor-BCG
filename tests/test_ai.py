"""
test_ai.py
==========
Descripciones del contenido de cada tomo (app/ai.py) y búsqueda por
palabras clave (Database.buscar_tomos).

NINGUNA prueba toca la red: `describe_tomo` acepta una función de envío
inyectada y las respuestas del modelo se simulan aquí.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai, collection  # noqa: E402
from app.database import Database  # noqa: E402


@pytest.fixture(scope="module")
def tomos():
    return collection.load_excel()


@pytest.fixture()
def db(tomos):
    base = Database(path=":memory:")
    base.replace_tomos(
        [(t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
         for t in tomos]
    )
    yield base
    base.close()


def _respuesta(**campos) -> dict:
    """Respuesta de la API con el JSON que devolvería el modelo."""
    datos = {
        "resumen": "Relato de las Guerras Médicas entre griegos y persas, "
                   "con digresiones sobre Egipto y Escitia. Este volumen "
                   "abarca los libros I y II de la obra.",
        "temas": ["guerras médicas", "persia", "egipto"],
        "genero": "Historia",
        "epoca": "siglo V a. C.",
        "confianza": 0.9,
    }
    datos.update(campos)
    return {"choices": [{"message": {
        "content": json.dumps(datos, ensure_ascii=False)
    }}]}


# ----------------------------------------------------------------------
# Prompt: anclado en los datos REALES de la colección
# ----------------------------------------------------------------------
def test_prompt_lleva_los_datos_del_tomo(tomos):
    tomo = next(t for t in tomos if t.orden == 3)
    prompt = ai.build_prompt(tomo)
    assert "Heródoto" in prompt
    assert "Historia" in prompt
    assert "Libros I-II" in prompt          # el volumen concreto
    assert str(tomo.paginas) in prompt
    assert "Biblioteca Clásica Gredos: 3" in prompt


def test_prompt_sin_autor_lo_dice(tomos):
    tomo = next(t for t in tomos if collection.is_collective_author(t.autor))
    tomo_sin = collection.Tomo(
        numero=tomo.numero, orden=tomo.orden, autor="", obras=tomo.obras,
        paginas=None, notas="",
    )
    assert "sin autor" in ai.build_prompt(tomo_sin)


# ----------------------------------------------------------------------
# Validación de la respuesta: nunca guardar basura
# ----------------------------------------------------------------------
def test_describe_tomo_valida_y_normaliza(tomos):
    tomo = next(t for t in tomos if t.orden == 3)
    desc = ai.describe_tomo(
        tomo, "clave", "gpt-4o-mini",
        _post_fn=lambda *a, **k: _respuesta(
            temas=["Guerras Médicas", "PERSIA", "guerras médicas", " egipto "]
        ),
    )
    # Minúsculas, sin espacios sobrantes y SIN repetidos
    assert desc.temas == ["guerras médicas", "persia", "egipto"]
    assert desc.temas_texto() == "guerras médicas · persia · egipto"
    assert desc.confianza == 0.9
    assert desc.genero == "historia"


@pytest.mark.parametrize(
    "contenido",
    [
        "esto no es json",
        '{"resumen": "corto", "temas": ["a"]}',        # resumen ridículo
        '{"resumen": "' + "x" * 60 + '", "temas": []}',  # sin temas
        '["lista", "en vez de objeto"]',
    ],
)
def test_respuestas_invalidas_se_rechazan(contenido):
    with pytest.raises(ai.AIError):
        ai.parse_response(contenido)


def test_temas_en_una_sola_cadena(tomos):
    """Algunos modelos devuelven 'a, b, c' en vez de una lista."""
    desc = ai.parse_response(json.dumps({
        "resumen": "x" * 60, "temas": "medicina, cirugía; ética médica",
    }))
    assert desc.temas == ["medicina", "cirugía", "ética médica"]


def test_sin_clave_no_llama_a_la_api(tomos):
    llamadas = []
    with pytest.raises(ai.AIError):
        ai.describe_tomo(
            tomos[0], "", _post_fn=lambda *a, **k: llamadas.append(1)
        )
    assert llamadas == []


def test_confianza_fuera_de_rango_se_recorta():
    desc = ai.parse_response(json.dumps({
        "resumen": "x" * 60, "temas": ["a"], "confianza": 42,
    }))
    assert desc.confianza == 1.0


# ----------------------------------------------------------------------
# Base de datos: guardar, contar y SOBREVIVIR al reimporte
# ----------------------------------------------------------------------
def test_descripcion_se_guarda_y_sobrevive_al_reimporte(db, tomos):
    assert db.descripciones_count() == 0
    assert len(db.tomos_sin_descripcion()) == db.tomos_count()

    db.set_tomo_description(3, "Guerras médicas.", "guerra · persia", "modelo-x")
    assert db.descripciones_count() == 1
    assert len(db.tomos_sin_descripcion()) == db.tomos_count() - 1
    fila = next(r for r in db.get_tomos() if r["orden"] == 3)
    assert fila["temas"] == "guerra · persia"
    assert fila["desc_modelo"] == "modelo-x"
    assert fila["desc_fecha"]

    # Reimportar el Excel NO puede borrar lo que costó dinero generar
    db.replace_tomos(
        [(t.orden, t.numero, t.autor, t.obras, t.paginas, t.notas)
         for t in tomos]
    )
    fila = next(r for r in db.get_tomos() if r["orden"] == 3)
    assert fila["descripcion"] == "Guerras médicas."
    assert fila["temas"] == "guerra · persia"

    # Borrar la descripción vuelve a dejar el tomo pendiente
    db.set_tomo_description(3, None)
    assert db.descripciones_count() == 0


# ----------------------------------------------------------------------
# Búsqueda por palabras clave
# ----------------------------------------------------------------------
def test_busqueda_exige_todas_las_palabras(db):
    assert db.buscar_tomos("jenofonte helenicas")
    assert db.buscar_tomos("jenofonte anabasis")
    # Palabras de tomos DISTINTOS: ningún tomo las tiene todas
    assert db.buscar_tomos("jenofonte metamorfosis") == []
    assert db.buscar_tomos("") == []
    assert db.buscar_tomos("   ") == []


def test_busqueda_ignora_tildes_y_mayusculas(db):
    con = db.buscar_tomos("HERÓDOTO")
    sin = db.buscar_tomos("herodoto")
    assert con and len(con) == len(sin)


def test_busqueda_encuentra_por_tema_generado(db):
    """
    La razón de ser de las descripciones: encontrar tomos por lo que
    TRATAN aunque la palabra no esté en el título.
    """
    assert db.buscar_tomos("apicultura") == []
    db.set_tomo_description(
        3, "Historia de las guerras entre griegos y persas.",
        "guerras médicas · apicultura · persia", "modelo-x",
    )
    encontrados = db.buscar_tomos("apicultura")
    assert [r["fila"]["orden"] for r in encontrados] == [3]
    assert "temas" in encontrados[0]["campos"]


def test_busqueda_ordena_por_relevancia(db):
    """El título pesa más que la descripción."""
    db.set_tomo_description(9, "Trata de Jenofonte y sus Helénicas.",
                            "jenofonte", "modelo-x")
    resultados = db.buscar_tomos("jenofonte helenicas")
    assert resultados[0]["fila"]["orden"] != 9      # el tomo real primero
    assert resultados[0]["fila"]["autor"].lower().startswith("jenofonte")
    ordenes = [r["fila"]["orden"] for r in resultados]
    assert 9 in ordenes                             # pero el otro sale


def test_busqueda_por_numero_de_tomo(db):
    resultados = db.buscar_tomos("66")
    assert resultados[0]["fila"]["orden"] == 66     # exacto primero
