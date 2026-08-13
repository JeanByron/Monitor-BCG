"""
test_rutas.py
=============
La aplicación tiene que sobrevivir a que la muevan de carpeta.

El 2026-08-13 el proyecto pasó de `D:\\jeanb\\Desktop\\Codigo BCG` a
`D:\\jeanb\\Desktop\\Proyectos\\Codigo BCG`. Los DATOS aguantaron
—todas las rutas internas salen de `app_dir()`, que se deduce de
`__file__`—, pero dos cosas viven FUERA del proyecto, en el registro de
Windows, y guardan la ruta entera de `main.py`:

- la clave Run (arranque con Windows),
- el esquema `bcgmonitor:` de los enlaces de las notificaciones.

Ninguna de las dos se queja al quedarse obsoleta: Windows lanza un
archivo que ya no existe y `pythonw.exe` muere en silencio. Estas
pruebas fijan que ambas se reajusten solas en cada arranque y que
ningún archivo del proyecto vuelva a llevar una ruta absoluta escrita a
mano.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as punto_de_entrada
from app import autostart, deeplink
from app.config import app_dir
from app.database import Database

RAIZ = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# La base de todo: app_dir() se deduce del propio archivo
# ----------------------------------------------------------------------
def test_app_dir_es_la_raiz_del_proyecto():
    assert app_dir() == RAIZ
    assert (app_dir() / "main.py").exists()


def test_los_comandos_apuntan_a_este_main():
    """Ambos comandos se construyen desde `__file__`, no desde el cwd."""
    esperado = str(RAIZ / "main.py")
    assert esperado in autostart._command()
    assert esperado in deeplink._comando()
    assert autostart._command().endswith("--tray")


# ----------------------------------------------------------------------
# Qué se compara de un comando registrado
# ----------------------------------------------------------------------
def test_objetivo_es_lo_que_se_lanza_no_el_interprete():
    # Desarrollo: el intérprete y el script.
    assert autostart._objetivo(
        r'"D:\Miniconda\pythonw.exe" "D:\x\main.py" --tray'
    ) == r"d:\x\main.py"
    # Empaquetado: solo el .exe.
    assert autostart._objetivo(r'"D:\Apps\MonitorBCG.exe" --tray') == \
        r"d:\apps\monitorbcg.exe"
    assert autostart._objetivo("sin comillas --tray") is None


def test_objetivo_no_distingue_mayusculas():
    # Windows no las distingue en las rutas: compararlas tal cual daría
    # por distinta una ruta que es la misma y reescribiría la clave en
    # cada arranque.
    assert autostart._objetivo(r'"py.exe" "D:\X\MAIN.PY" --tray') == \
        autostart._objetivo(r'"py.exe" "d:\x\main.py" --tray')


# ----------------------------------------------------------------------
# Sincronización del arranque con Windows
# ----------------------------------------------------------------------
@pytest.fixture()
def registro(monkeypatch):
    """Clave Run de mentira: qué hay puesto y qué se llegó a escribir."""
    estado: dict = {"valor": (None, None), "escrito": []}

    monkeypatch.setattr(autostart, "_IS_WINDOWS", True)
    monkeypatch.setattr(autostart, "_valor_registrado",
                        lambda: estado["valor"])
    monkeypatch.setattr(
        autostart, "set_enabled",
        lambda activo: estado["escrito"].append(activo) or True,
    )
    return estado


def test_sincronizar_reescribe_cuando_la_ruta_cambio(registro):
    registro["valor"] = (
        "MonitorBCG",
        r'"D:\Miniconda\pythonw.exe" "D:\jeanb\Desktop\Codigo BCG\main.py" --tray',
    )
    assert autostart.sincronizar_ruta(True) is True
    assert registro["escrito"] == [True]


def test_sincronizar_no_toca_nada_si_ya_apunta_aqui(registro):
    registro["valor"] = ("MonitorBCG", autostart._command())
    assert autostart.sincronizar_ruta(True) is False
    assert registro["escrito"] == []


def test_sincronizar_pasa_del_nombre_antiguo_aunque_la_ruta_valga(registro):
    # Mismo comando pero con el nombre de antes del renombrado: hay que
    # reescribir, o quedarían dos entradas y arrancarían dos copias.
    registro["valor"] = ("MonitorTodocoleccion", autostart._command())
    assert autostart.sincronizar_ruta(True) is True
    assert registro["escrito"] == [True]


def test_sincronizar_restaura_la_clave_que_falta_si_la_config_lo_pide(registro):
    # Copiar el proyecto a otro equipo trae config.json pero no el registro.
    registro["valor"] = (None, None)
    assert autostart.sincronizar_ruta(True) is True
    assert registro["escrito"] == [True]


def test_sincronizar_no_activa_nada_si_el_usuario_no_lo_quiere(registro):
    registro["valor"] = (None, None)
    assert autostart.sincronizar_ruta(False) is False
    assert registro["escrito"] == []


def test_sincronizar_nunca_retira_el_arranque(registro):
    """Quitarlo es una acción explícita del usuario, no un efecto lateral."""
    registro["valor"] = ("MonitorBCG", autostart._command())
    autostart.sincronizar_ruta(False)
    assert registro["escrito"] == []


def test_apunta_aqui(monkeypatch):
    monkeypatch.setattr(autostart, "_IS_WINDOWS", True)
    monkeypatch.setattr(autostart, "_valor_registrado",
                        lambda: ("MonitorBCG", autostart._command()))
    assert autostart.apunta_aqui() is True
    assert autostart.is_enabled() is True

    monkeypatch.setattr(autostart, "_valor_registrado",
                        lambda: ("MonitorBCG", r'"py.exe" "D:\viejo\main.py" --tray'))
    # Registrado sí, pero a la carpeta de antes: para el diagnóstico eso
    # es un fallo, no un "activado".
    assert autostart.is_enabled() is True
    assert autostart.apunta_aqui() is False


def test_apunta_aqui_respeta_otro_interprete(monkeypatch):
    """
    El intérprete registrado es el que tiene PySide6 instalado.

    Ejecutar main.py o una herramienta una sola vez desde otro Python no
    puede repuntar el arranque a ese otro: al siguiente inicio de sesión
    Windows lanzaría un Python sin dependencias y la aplicación no
    arrancaría, sin decir por qué.
    """
    monkeypatch.setattr(autostart, "_IS_WINDOWS", True)
    otro = f'"{sys.executable}" "{RAIZ / "main.py"}" --tray'
    monkeypatch.setattr(autostart, "_valor_registrado",
                        lambda: ("MonitorBCG", otro))
    assert autostart.apunta_aqui() is True


def test_apunta_aqui_falla_si_el_interprete_ya_no_existe(monkeypatch):
    monkeypatch.setattr(autostart, "_IS_WINDOWS", True)
    muerto = rf'"D:\NoExiste\pythonw.exe" "{RAIZ / "main.py"}" --tray'
    monkeypatch.setattr(autostart, "_valor_registrado",
                        lambda: ("MonitorBCG", muerto))
    assert autostart.apunta_aqui() is False


def test_apunta_aqui_falla_sin_tray(monkeypatch):
    """Sin --tray, iniciar sesión abriría la ventana en vez de la bandeja."""
    monkeypatch.setattr(autostart, "_IS_WINDOWS", True)
    sin_tray = f'"{sys.executable}" "{RAIZ / "main.py"}"'
    monkeypatch.setattr(autostart, "_valor_registrado",
                        lambda: ("MonitorBCG", sin_tray))
    assert autostart.apunta_aqui() is False


# ----------------------------------------------------------------------
# Enlaces bcgmonitor:
# ----------------------------------------------------------------------
def test_deeplink_apunta_aqui(monkeypatch):
    monkeypatch.setattr(deeplink, "_ES_WINDOWS", True)
    monkeypatch.setattr(deeplink, "comando_registrado",
                        lambda: deeplink._comando())
    assert deeplink.apunta_aqui() is True

    monkeypatch.setattr(deeplink, "comando_registrado",
                        lambda: r'"py.exe" "D:\viejo\main.py" "%1"')
    assert deeplink.apunta_aqui() is False


# ----------------------------------------------------------------------
# Aviso del cambio de carpeta
# ----------------------------------------------------------------------
def test_revisar_carpeta_detecta_la_mudanza(monkeypatch):
    db = Database(path=":memory:")
    try:
        monkeypatch.setattr(punto_de_entrada, "app_dir",
                            lambda: Path(r"D:\jeanb\Desktop\Codigo BCG"))
        assert punto_de_entrada.revisar_carpeta(db) is None      # primera vez
        assert punto_de_entrada.revisar_carpeta(db) is None      # sin cambios

        monkeypatch.setattr(
            punto_de_entrada, "app_dir",
            lambda: Path(r"D:\jeanb\Desktop\Proyectos\Codigo BCG"),
        )
        anterior = punto_de_entrada.revisar_carpeta(db)
        assert anterior == r"D:\jeanb\Desktop\Codigo BCG"
        # Y una vez avisado, no vuelve a avisar del mismo cambio.
        assert punto_de_entrada.revisar_carpeta(db) is None
        assert db.get_meta("ruta_app") == r"D:\jeanb\Desktop\Proyectos\Codigo BCG"
    finally:
        db.close()


def test_revisar_carpeta_no_tumba_el_arranque_si_la_bd_falla():
    class BDRota:
        def get_meta(self, clave):
            raise RuntimeError("base de datos bloqueada")

        def set_meta(self, clave, valor):
            raise RuntimeError("base de datos bloqueada")

    assert punto_de_entrada.revisar_carpeta(BDRota()) is None


# ----------------------------------------------------------------------
# Regresión: ninguna ruta absoluta escrita a mano en el código
# ----------------------------------------------------------------------
# Letra de unidad SUELTA (nada alfanumérico delante: si no, "https:" y
# los "\n" de cualquier mensaje entrarían como rutas) y sin `//`
# detrás, que es lo que distingue `D:/x` de `https://x`. Se lee hasta la
# comilla o el fin de línea, NO hasta el primer espacio: las rutas de
# Windows los llevan ("C:\\Program Files\\…") y cortando ahí la lista de
# permitidas nunca casaría.
# Y con DOS separadores: una ruta de verdad tiene carpeta ("C:\\Users\\…"),
# mientras que `[a-z:/._]+` de una expresión regular o un ":\n" de
# cualquier mensaje solo tienen uno.
_RUTA_ABSOLUTA = re.compile(r"""(?<!\w)[A-Za-z]:[\\/](?![\\/])[^"'\n]{0,120}?[\\/]""")

# Rutas de sistema que NO dependen de dónde viva el proyecto: buscar
# Tesseract donde Windows lo instala es legítimo.
_PERMITIDAS = ("Program Files", "ProgramData", "Tesseract", "AppData",
               "Windows\\System32", "Miniconda", "Python")


def _rutas_absolutas(texto: str) -> list[str]:
    return [
        m for m in _RUTA_ABSOLUTA.findall(texto)
        if not any(p.casefold() in m.casefold() for p in _PERMITIDAS)
    ]


def test_el_detector_de_rutas_distingue_lo_que_tiene_que_distinguir():
    """Sin esto el guardián de abajo podría pasar por no ver NADA."""
    assert _rutas_absolutas(r'ruta = r"D:\jeanb\Desktop\Codigo BCG\main.py"')
    assert _rutas_absolutas('ruta = "D:/jeanb/Desktop/Codigo BCG"')
    # Y lo que NO es una ruta del proyecto:
    assert not _rutas_absolutas('"https://www.todocoleccion.net/x~x1"')
    assert not _rutas_absolutas('f"Colección importada:\\n{path}"')
    assert not _rutas_absolutas('re.compile(r"[a-z:/._]+")')
    assert not _rutas_absolutas(r'"C:\Program Files\Tesseract-OCR\tesseract.exe"')


def test_ningun_archivo_del_proyecto_lleva_una_ruta_absoluta():
    """
    Todo se resuelve desde `app_dir()`. Una ruta escrita a mano volvería
    a romperse en la siguiente mudanza y, como aquí nada falla de golpe,
    el fallo no se vería hasta echar de menos los avisos.
    """
    archivos = [RAIZ / "main.py", RAIZ / "MonitorBCG.spec"]
    for carpeta in ("app", "tools", "tests"):
        archivos.extend(sorted((RAIZ / carpeta).glob("*.py")))

    culpables: list[str] = []
    for archivo in archivos:
        if archivo.name == "test_rutas.py":     # aquí son ejemplos a propósito
            continue
        for ruta in _rutas_absolutas(archivo.read_text(encoding="utf-8")):
            culpables.append(f"{archivo.name}: {ruta}")
    assert not culpables, "Rutas absolutas en el código: " + "; ".join(culpables)
