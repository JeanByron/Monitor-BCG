"""
ai.py
=====
Descripción del contenido de cada tomo mediante un modelo de lenguaje
(API de OpenAI).

Qué hace: dado un tomo de la colección (autor, obras, notas, volumen),
pide un resumen breve de QUÉ CONTIENE y una lista de temas. El resumen
se muestra en la ficha del tomo y los temas alimentan el buscador por
palabras clave: escribir "medicina" encuentra los tomos que tratan de
medicina aunque esa palabra no aparezca en el título.

Decisiones de diseño
--------------------
- **Sin dependencias nuevas**: se llama a la API REST con `urllib` de la
  biblioteca estándar. Añadir el SDK de OpenAI obligaría a tocar el
  empaquetado (PyInstaller) para un par de peticiones HTTP.
- **Anclado a los datos reales**: el prompt lleva el autor, la obra, las
  notas y el volumen tal como están en la colección, y prohíbe inventar.
  Para volúmenes oscuros el modelo debe decirlo en `confianza` en vez de
  rellenar con adornos.
- **Respuesta en JSON** (`response_format`), validada antes de guardar:
  si el modelo devuelve cualquier otra cosa, se lanza `AIError` y ese
  tomo se queda sin descripción (nunca se guarda basura).
- **Nunca lanza al hilo de la GUI**: el llamador captura `AIError`.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 60
MAX_TEMAS = 8

_SISTEMA = (
    "Eres un bibliotecario especializado en literatura griega y latina "
    "antigua, y en concreto en la Biblioteca Clásica Gredos. Respondes "
    "SIEMPRE en español y SIEMPRE en JSON válido.\n"
    "Reglas:\n"
    "1. Describe lo que el volumen CONTIENE realmente: qué obra u obras "
    "son, de qué tratan y qué abarca este volumen concreto.\n"
    "2. No inventes. Si no conoces con seguridad el contenido de ese "
    "volumen, dilo bajando 'confianza' y describe solo lo que sepas del "
    "autor y de la obra.\n"
    "3. Nada de juicios comerciales, precios ni ediciones modernas.\n"
    "4. Ni una palabra fuera del JSON."
)

_ESQUEMA = (
    '{"resumen": "60-90 palabras sobre el contenido del volumen", '
    '"temas": ["5 a 8 palabras clave en minúsculas: materias, géneros, '
    'lugares o conceptos por los que alguien buscaría este tomo"], '
    '"genero": "épica | tragedia | historia | filosofía | oratoria | '
    'medicina | ciencia | biografía | poesía | otro", '
    '"epoca": "p. ej. siglo V a. C.", '
    '"confianza": 0.0}'
)


class AIError(RuntimeError):
    """Fallo al generar una descripción (red, clave, respuesta rara)."""


@dataclass
class Descripcion:
    """Lo que devuelve el modelo, ya validado."""

    resumen: str
    temas: list[str]
    genero: str = ""
    epoca: str = ""
    confianza: float = 0.0

    def temas_texto(self) -> str:
        """Temas como una sola cadena, tal como se guardan en la BD."""
        return " · ".join(self.temas)


def build_prompt(tomo) -> str:
    """Petición para UN tomo, anclada en los datos de la colección."""
    partes = [
        f"Autor: {tomo.autor or '(sin autor: obra colectiva o anónima)'}",
        f"Obra(s) según la colección: {tomo.obras}",
    ]
    if getattr(tomo, "sufijo", ""):
        partes.append(f"Volumen: {tomo.sufijo}")
    if tomo.notas:
        partes.append(f"Notas de la edición: {tomo.notas}")
    if tomo.paginas:
        partes.append(f"Páginas: {tomo.paginas}")
    if tomo.orden is not None:
        partes.append(f"Número en la Biblioteca Clásica Gredos: {tomo.orden}")
    return (
        "Describe este volumen de la Biblioteca Clásica Gredos.\n\n"
        + "\n".join(partes)
        + "\n\nResponde EXACTAMENTE con este JSON:\n"
        + _ESQUEMA
    )


def parse_response(texto: str) -> Descripcion:
    """Valida el JSON del modelo; lanza `AIError` si no sirve."""
    try:
        datos = json.loads(texto)
    except ValueError as exc:
        raise AIError(f"respuesta que no es JSON: {exc}") from exc
    if not isinstance(datos, dict):
        raise AIError("respuesta que no es un objeto JSON")

    resumen = str(datos.get("resumen") or "").strip()
    if len(resumen) < 40:
        raise AIError("resumen vacío o demasiado corto")

    crudos = datos.get("temas") or []
    if isinstance(crudos, str):                 # a veces llega "a, b, c"
        crudos = [t for t in crudos.replace(";", ",").split(",")]
    temas, vistos = [], set()
    for tema in crudos:
        limpio = " ".join(str(tema).lower().split()).strip(" .·-")
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            temas.append(limpio)
    if not temas:
        raise AIError("sin temas")

    try:
        confianza = float(datos.get("confianza") or 0.0)
    except (TypeError, ValueError):
        confianza = 0.0
    return Descripcion(
        resumen=resumen,
        temas=temas[:MAX_TEMAS],
        genero=str(datos.get("genero") or "").strip().lower(),
        epoca=str(datos.get("epoca") or "").strip(),
        confianza=max(0.0, min(1.0, confianza)),
    )


def _post(url: str, cuerpo: dict, api_key: str, timeout: int) -> dict:
    """POST con JSON; traduce los errores HTTP a mensajes en español."""
    datos = json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=datos,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detalle = ""
        try:
            detalle = json.loads(exc.read().decode("utf-8"))
            detalle = detalle.get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001 - el detalle es opcional
            pass
        if exc.code == 401:
            raise AIError("clave de API rechazada (401)") from exc
        if exc.code == 429:
            raise AIError(f"límite de uso alcanzado (429): {detalle}") from exc
        raise AIError(f"error HTTP {exc.code}: {detalle}") from exc
    except urllib.error.URLError as exc:
        raise AIError(f"sin conexión con OpenAI: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise AIError(f"fallo de red: {exc}") from exc


def describe_tomo(
    tomo,
    api_key: str,
    model: str = "gpt-4o-mini",
    timeout: int = TIMEOUT,
    _post_fn=_post,
) -> Descripcion:
    """
    Pide al modelo la descripción de UN tomo.

    `_post_fn` existe para las pruebas: permite inyectar una respuesta
    sin tocar la red.
    """
    if not api_key:
        raise AIError("falta la clave de API de OpenAI (Configuración)")
    cuerpo = {
        "model": model,
        "temperature": 0.2,          # descriptivo, no creativo
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SISTEMA},
            {"role": "user", "content": build_prompt(tomo)},
        ],
    }
    respuesta = _post_fn(API_URL, cuerpo, api_key, timeout)
    try:
        texto = respuesta["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("respuesta de la API sin contenido") from exc
    desc = parse_response(texto)
    logger.info(
        "Descripción generada para el tomo %s (%s): %d temas, confianza %.2f",
        tomo.orden, model, len(desc.temas), desc.confianza,
    )
    return desc


def check_api_key(api_key: str, model: str, _post_fn=_post) -> Optional[str]:
    """
    Comprueba que la clave sirve. Devuelve None si todo va bien, o el
    motivo del fallo (para enseñarlo en Configuración).
    """
    if not api_key:
        return "No hay clave configurada."
    cuerpo = {
        "model": model,
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "ok"}],
    }
    try:
        _post_fn(API_URL, cuerpo, api_key, 20)
    except AIError as exc:
        return str(exc)
    return None
