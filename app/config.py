"""
config.py
=========
Gestión de la configuración de la aplicación.

Carga y guarda la configuración desde/hacia un archivo `config.json`
situado junto al ejecutable. Si el archivo no existe, se crea uno
con valores por defecto para que el usuario solo tenga que rellenar
sus credenciales.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform.startswith("win")


# ----------------------------------------------------------------------
# Cifrado DPAPI de la contraseña (ligado al usuario de Windows)
# ----------------------------------------------------------------------
# config.json guardaba la contraseña de aplicación EN CLARO. Con DPAPI
# (CryptProtectData) queda cifrada con la clave del propio usuario de
# Windows: sin contraseña maestra, y otro usuario/máquina no puede
# descifrarla. En sistemas no-Windows se guarda en claro (desarrollo).

def _dpapi_crypt(data: bytes, encrypt: bool) -> Optional[bytes]:
    """CryptProtectData/CryptUnprotectData vía ctypes; None si falla."""
    if not _IS_WINDOWS:
        return None
    import ctypes
    import ctypes.wintypes as wt

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wt.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    func = (
        ctypes.windll.crypt32.CryptProtectData
        if encrypt
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    ok = func(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt_password(plain: str) -> Optional[str]:
    """Contraseña → base64(DPAPI). None si DPAPI no está disponible."""
    if not plain:
        return None
    raw = _dpapi_crypt(plain.encode("utf-8"), encrypt=True)
    return base64.b64encode(raw).decode("ascii") if raw else None


def decrypt_password(token: str) -> Optional[str]:
    """base64(DPAPI) → contraseña. None si no se puede descifrar."""
    try:
        raw = _dpapi_crypt(base64.b64decode(token), encrypt=False)
        return raw.decode("utf-8") if raw else None
    except Exception as exc:  # noqa: BLE001 - token corrupto/otra máquina
        logger.error("No se pudo descifrar la contraseña: %s", exc)
        return None


def app_dir() -> Path:
    """
    Devuelve el directorio base de la aplicación.

    - Si el programa está empaquetado con PyInstaller (`sys.frozen`),
      es el directorio donde reside el .exe.
    - En desarrollo, es el directorio de este archivo.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # En desarrollo el código vive en app/: la base es la RAÍZ del
    # proyecto (config.json, tc_monitor.db, log.txt, BDtomos...)
    return Path(__file__).resolve().parent.parent


def resource_path(relativa: str) -> Path:
    """
    Ruta de un recurso de solo lectura (iconos): primero junto al
    ejecutable/proyecto y, si no está, dentro del propio .exe.

    PyInstaller descomprime los `datas` en `sys._MEIPASS`, que NO es
    `app_dir()`; sin este respaldo, un .exe empaquetado se quedaba sin
    logo en los avisos salvo que el usuario copiase la carpeta assets a
    mano.
    """
    ruta = app_dir() / relativa
    if ruta.exists():
        return ruta
    empaquetado = getattr(sys, "_MEIPASS", None)
    if empaquetado:
        interno = Path(empaquetado) / relativa
        if interno.exists():
            return interno
    return ruta  # inexistente: el llamador ya contempla que falte


CONFIG_PATH: Path = app_dir() / "config.json"


@dataclass
class Config:
    """Configuración completa de la aplicación."""

    # --- Conexión IMAP -------------------------------------------------
    imap_server: str = "imap.gmail.com"      # Servidor IMAP
    imap_port: int = 993                     # Puerto (SSL)
    email_user: str = "tu_correo@gmail.com"  # Dirección de correo
    email_password: str = "contraseña_de_aplicacion"  # Contraseña o app password
    mail_folder: str = "INBOX"               # Carpeta a vigilar

    # --- Lógica de alertas ---------------------------------------------
    min_discount_percent: float = 50.0       # Umbral mínimo de descuento (%)
    check_interval_seconds: int = 30         # Intervalo de sondeo (fallback si no hay IDLE)
    use_imap_idle: bool = True               # Usar IMAP IDLE si el servidor lo soporta

    # En la PRIMERA ejecución solo se revisan los N correos más
    # recientes del buzón (línea base); después, el monitor avanza por
    # UID: todo lo posterior al último correo procesado, esté leído o
    # no. (El antiguo modo "solo no leídos" se eliminó: si otra pestaña
    # o el móvil marcaban una oferta como leída, el monitor no la veía.)
    startup_check_count: int = 40

    # Marcar como LEÍDOS los correos de Todocolección ya procesados
    # (flag \\Seen por IMAP). Los correos personales no se tocan.
    mark_processed_as_read: bool = True
    # (Nota: el antiguo `initial_backlog_count` quedó obsoleto y se
    # eliminó; si sigue en config.json se ignora sin más.)

    # --- Notificaciones --------------------------------------------------
    enable_sound: bool = True                # Sonido en la notificación
    auto_open_link: bool = False             # Abrir el anuncio automáticamente al notificar

    # --- Detección de lotes ----------------------------------------------
    # Notificar cuando un correo de Todocolección contenga un lote de
    # `min_lot_books` o más libros (ver utils.detect_lot).
    enable_lot_detection: bool = True
    min_lot_books: int = 5

    # --- Deseados ---------------------------------------------------------
    # Umbral REDUCIDO para tomos marcados ⭐ Deseado: cualquier bajada
    # que alcance este % avisa, ignorando el umbral global. Con 0.0,
    # un deseado notifica con CUALQUIER bajada detectada.
    wished_discount_percent: float = 20.0

    # --- Resumen diario ---------------------------------------------------
    # Un toast al final del día con la actividad: alertas notificadas,
    # ignoradas por umbral, lotes y el mejor descuento del día.
    daily_summary_enabled: bool = True
    daily_summary_time: str = "21:00"        # hora local HH:MM

    # --- Arranque con Windows --------------------------------------------
    # Registrar el programa en la clave Run del usuario para que arranque
    # al iniciar sesión (ver autostart.py). El valor se sincroniza con el
    # registro cada vez que se guarda la configuración desde la GUI.
    start_with_windows: bool = False

    # --- Descripciones de la colección (OpenAI) ---------------------------
    # Clave de API para generar la descripción del contenido de cada
    # tomo. Se guarda CIFRADA con DPAPI, igual que la del correo (ver
    # save/load). Sin clave, la aplicación funciona igual: solo queda
    # desactivada la generación.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Tesseract (programa aparte) para reconocer las páginas de un PDF
    # que no traen texto. Vacío = buscarlo donde se instala por defecto.
    tesseract_path: str = ""

    # --- Filtro de remitente ---------------------------------------------
    # Cualquier correo cuyo remitente contenga este texto se considera
    # candidato. Los correos de ofertas SIEMPRE vienen de Todocolección.
    sender_filter: str = "todocoleccion"

    # Palabras clave (en minúsculas y sin tildes) que deben aparecer en el
    # asunto o cuerpo para tratar el correo como aviso de precio/favoritos.
    subject_keywords: list[str] = field(default_factory=lambda: [
        "bajada de precio",
        "baja de precio",
        "cambio de precio",
        "ha bajado",
        "nuevo precio",
        "favorito",
        "favoritos",
        "rebaja",
        "descuento",
        "oferta",
        "seguimiento",
    ])

    # Correos de Todocolección que NO son avisos de bajada de precio y que
    # deben descartarse aunque su cuerpo mencione "oferta" o "descuento":
    # recomendaciones ("Tenemos lotes que te pueden interesar") y pujas.
    # Se comparan solo contra el ASUNTO, en minúsculas y sin tildes.
    exclude_subject_keywords: list[str] = field(default_factory=lambda: [
        "te pueden interesar",
        "lotes que te pueden",
        "mejorar oferta",
        "mejora tu oferta",
        "mejorar tu oferta",
        "te han superado",
        "puja",
        # OFERTAS AL VENDEDOR (2026-08-07): no son bajadas de precio,
        # son la invitación a regatear. Traen VARIOS lotes y un carrusel
        # de recomendados, y colaban por la palabra clave suelta
        # "oferta" — así se notificó "Foto número 1 del pedido, 40 € →
        # 8,91 €", con cada dato de un anuncio distinto.
        "haz una oferta",
        "haz tu oferta",
        "oferta al vendedor",
        "admite ofertas",
        "admiten ofertas",
        "han hecho una oferta",
        "ha hecho una oferta",
        "acepta tu oferta",
        "oferta aceptada",
        "oferta rechazada",
        "contraoferta",
        # Artículos vendidos y boletines de marketing: no son avisos de
        # los favoritos del usuario.
        "se ha vendido",
        "ha sido vendido",
        "ya esta vendido",
        "vendido",
        "novedades",
        "recomendaciones",
        "recomendados",
        "recomendamos",
        "descubre",
        "newsletter",
        "boletin",
        "ultima oportunidad",
        "no te lo pierdas",
    ])

    # ------------------------------------------------------------------
    # Carga / guardado
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        """
        Carga la configuración desde `config.json`.

        Si el archivo no existe se crea con los valores por defecto.
        Las claves desconocidas se ignoran y las que falten toman
        el valor por defecto, de modo que el archivo es tolerante a
        versiones antiguas o incompletas.
        """
        if not path.exists():
            logger.warning("config.json no encontrado. Creando uno por defecto en %s", path)
            cfg = cls()
            cfg.save(path)
            return cfg

        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Error leyendo config.json (%s). Usando valores por defecto.", exc)
            return cls()

        # Contraseña cifrada (DPAPI): descifrar a memoria. Si falla el
        # descifrado (otra máquina/usuario), se avisa y queda vacía.
        token = raw.pop("email_password_dpapi", None)
        if token and not raw.get("email_password"):
            plain = decrypt_password(token)
            if plain is not None:
                raw["email_password"] = plain
            else:
                logger.error(
                    "Contraseña cifrada ilegible: vuelve a introducirla "
                    "en Configuración."
                )

        # Clave de OpenAI, cifrada igual que la del correo
        token_ia = raw.pop("openai_api_key_dpapi", None)
        if token_ia and not raw.get("openai_api_key"):
            clave = decrypt_password(token_ia)
            if clave is not None:
                raw["openai_api_key"] = clave
            else:
                logger.error(
                    "Clave de OpenAI cifrada ilegible: vuelve a "
                    "introducirla en Configuración."
                )

        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in raw.items() if k in valid_fields}
        cfg = cls(**filtered)
        logger.info("Configuración cargada desde %s", path)

        # Migración: si la contraseña estaba EN CLARO en el archivo,
        # re-guardar ya cifrada.
        if _IS_WINDOWS and raw.get("email_password") and not token:
            cfg.save(path)
            logger.info("Contraseña migrada a cifrado DPAPI en config.json.")

        # Exclusiones de asunto NUEVAS: la lista está guardada en el
        # archivo, así que sin esto un usuario de siempre nunca vería
        # las que se añaden después — y son justo las que tapan un
        # agujero recién descubierto (las «ofertas al vendedor»,
        # 2026-08-07). Se AÑADEN, nunca se quita lo que el usuario tenga.
        if cfg.anadir_exclusiones_nuevas():
            cfg.save(path)
        return cfg

    def anadir_exclusiones_nuevas(self) -> bool:
        """
        Suma las exclusiones de asunto que hayan aparecido en el
        programa y falten en este config.json. Devuelve True si añadió
        alguna.
        """
        del_programa = type(self).__dataclass_fields__[
            "exclude_subject_keywords"
        ].default_factory()
        tengo = {k.strip().lower() for k in self.exclude_subject_keywords}
        nuevas = [k for k in del_programa if k.strip().lower() not in tengo]
        if not nuevas:
            return False
        self.exclude_subject_keywords = list(self.exclude_subject_keywords) + nuevas
        logger.info(
            "Exclusiones de asunto añadidas a config.json: %s",
            ", ".join(nuevas),
        )
        return True

    def save(self, path: Path = CONFIG_PATH) -> None:
        """
        Guarda la configuración en `config.json` (formato legible).

        En Windows la contraseña se guarda CIFRADA con DPAPI
        (`email_password_dpapi`); el campo en claro no se escribe. Si el
        cifrado no está disponible, se guarda en claro con aviso.
        """
        data = asdict(self)
        token = encrypt_password(self.email_password)
        if token is not None:
            data["email_password_dpapi"] = token
            data.pop("email_password", None)
        elif _IS_WINDOWS and self.email_password:
            logger.warning(
                "DPAPI no disponible: la contraseña se guarda EN CLARO."
            )
        # La clave de OpenAI, con el mismo tratamiento
        token_ia = encrypt_password(self.openai_api_key) if self.openai_api_key else None
        if token_ia is not None:
            data["openai_api_key_dpapi"] = token_ia
            data.pop("openai_api_key", None)
        elif _IS_WINDOWS and self.openai_api_key:
            logger.warning(
                "DPAPI no disponible: la clave de OpenAI se guarda EN CLARO."
            )
        try:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Configuración guardada en %s", path)
        except OSError as exc:
            logger.error("No se pudo guardar la configuración: %s", exc)
