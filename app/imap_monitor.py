"""
imap_monitor.py
===============
Monitor de correo IMAP.

Corre en un hilo propio (QThread) para no bloquear la interfaz.

Estrategia de "tiempo real":

1. Si el servidor soporta **IMAP IDLE** (Gmail, Outlook, la mayoría),
   se usa IDLE: el servidor avisa en cuanto llega un correo nuevo,
   así la notificación aparece en apenas unos segundos y se consumen
   menos recursos que sondeando.
2. Si IDLE no está disponible (o está desactivado en config.json),
   se sondea la carpeta cada `check_interval_seconds` (30 s por defecto).

Control de duplicados: cada correo se identifica por su **UID IMAP**,
que se guarda en SQLite. Un UID procesado jamás vuelve a notificarse.

Flujo por correo nuevo:
    remitente contiene "todocoleccion"?  → no: ignorar
    asunto/cuerpo con palabras de precio/favoritos? → no: ignorar
    extraer título, precios, enlace y calcular descuento
    descuento >= umbral? → sí: notificación de Windows; no: registrar como ignorado
"""

from __future__ import annotations

import email
import imaplib
import logging
import select
import socket
import ssl
import time
from typing import Optional

from PySide6.QtCore import QThread, Signal

from app import collection
from app.config import Config, app_dir
from app.database import Database
from app.notification import Notifier
from app.utils import (
    LotAlert,
    PriceAlert,
    count_books_in_text,
    decode_header_value,
    detect_lot,
    extract_bodies,
    format_price,
    is_from_todocoleccion,
    is_price_alert,
    message_key,
    parse_alert_email,
    save_last_email,
    titulo_desde_url,
    varios_volumenes,
)

logger = logging.getLogger(__name__)

# IDLE debe renovarse antes de 30 min (RFC 2177), pero los routers
# domésticos cortan conexiones TCP inactivas sin avisar. Con 9 min el
# log real (2026-07-25) mostró EOF en CADA renovación: el NAT mataba la
# conexión antes. 4 min genera tráfico suficiente para mantener viva la
# ruta y, si aun así muere, se detecta enseguida.
IDLE_TIMEOUT_SECONDS = 4 * 60
# Espera antes de reintentar tras un error de conexión.
RECONNECT_DELAY_SECONDS = 15


class ImapMonitor(QThread):
    """Hilo que vigila el buzón y dispara notificaciones."""

    # Señales hacia la GUI (thread-safe en Qt)
    status_changed = Signal(str)          # texto de estado ("Monitor activo", ...)
    checked = Signal(str)                 # hora de la última revisión (HH:MM:SS)
    mail_processed = Signal(int)          # total de correos revisados
    alert_sent = Signal(int)              # total de alertas enviadas
    lot_detected = Signal(int)            # total de lotes notificados
    error_occurred = Signal(str)          # mensaje de error para la GUI

    def __init__(self, config: Config, db: Database, notifier: Notifier) -> None:
        super().__init__()
        self.config = config
        self.db = db
        self.notifier = notifier

        self._stop_requested = False
        self._imap: Optional[imaplib.IMAP4_SSL] = None
        self._supports_idle = False
        self._startup_scan_done = False
        self._tomos: list = []  # colección BCG (se carga en run())

        self.mails_checked_count = 0
        self.alerts_sent_count = 0
        self.lots_detected_count = 0

    # ------------------------------------------------------------------
    # Ciclo de vida del hilo
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """
        Solicita la parada del monitor de forma INMEDIATA.

        Además de activar el flag, cierra el socket IMAP: eso aborta al
        instante cualquier lectura bloqueante (por ejemplo, esperar la
        respuesta al DONE sobre una conexión que el router ya mató en
        silencio), que era lo que congelaba la aplicación al detener.
        """
        self._stop_requested = True
        imap = self._imap
        if imap is not None:
            try:
                imap.sock.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001 - el socket puede estar ya cerrado
                pass

    def _cargar_coleccion(self, primera: bool = False) -> None:
        """
        Trae la colección de la base de datos a memoria.

        Se REPITE en cada vuelta de correo, no solo al arrancar. Los
        datos que el usuario cambia desde la ventana —el precio
        objetivo, ⭐ Deseado, Obtenido— viven en estas filas, y con la
        carga única el monitor seguía con la foto del arranque: poner un
        precio objetivo con el programa abierto no surtía efecto hasta
        reiniciarlo (2026-08-09). Son 423 filas; leerlas cuesta
        milisegundos y solo pasa cuando llega correo.
        """
        try:
            tomos = [
                collection.Tomo(
                    numero=r["numero"], orden=r["orden"], autor=r["autor"],
                    obras=r["obras"], paginas=r["paginas"],
                    notas=r["notas"] or "",
                    poseido=bool(r["poseido"]),
                    deseado=bool(r["deseado"]),
                    precio_objetivo=r["precio_objetivo"],
                )
                for r in self.db.get_tomos()
            ]
            collection.annotate_ambiguous(tomos)
            self._tomos = tomos
            if primera and tomos:
                logger.info(
                    "Colección cargada: %d tomo(s) para el cruce de ofertas.",
                    len(tomos),
                )
        except Exception as exc:  # noqa: BLE001 - la colección es opcional
            logger.warning("No se pudo cargar la colección: %s", exc)
            # Si ya había una copia buena, se conserva: quedarse sin
            # colección dejaría de etiquetar las ofertas.
            if primera:
                self._tomos = []

    def run(self) -> None:  # noqa: D401 - punto de entrada del QThread
        """Bucle principal: conectar, procesar pendientes y esperar novedades."""
        logger.info("Monitor IMAP iniciado.")
        self.status_changed.emit("🟢 Monitor activo")

        self._cargar_coleccion(primera=True)

        while not self._stop_requested:
            try:
                self._connect()
                # (Re)conexión con éxito: reflejarlo en la interfaz.
                # Sin esto, tras un corte la ventana se quedaba en
                # "Reconectando…" para siempre aunque todo funcionara.
                self.status_changed.emit("🟢 Monitor activo")
                self.error_occurred.emit("")  # limpia el error anterior
                # Al (re)conectar, procesar cualquier correo pendiente.
                self._check_new_mail()

                if self.config.use_imap_idle and self._supports_idle:
                    self._idle_loop()
                else:
                    self._poll_loop()

            except (imaplib.IMAP4.error, OSError, ssl.SSLError, ValueError) as exc:
                if self._stop_requested:
                    break
                logger.error("Error de conexión IMAP: %s", exc)
                self.error_occurred.emit(f"Error IMAP: {exc}")
                self.status_changed.emit("🟠 Reconectando...")
                self._safe_logout()
                # Espera troceada para poder detenerse rápido
                for _ in range(RECONNECT_DELAY_SECONDS):
                    if self._stop_requested:
                        break
                    time.sleep(1)

        self._safe_logout()
        self.status_changed.emit("🔴 Monitor detenido")
        logger.info("Monitor IMAP detenido.")

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        """Abre la conexión IMAP SSL, hace login y selecciona la carpeta."""
        if self._imap is not None:
            return
        logger.info(
            "Conectando a %s:%s ...", self.config.imap_server, self.config.imap_port
        )
        # timeout=30: sin él, un socket medio muerto (p. ej. tras
        # suspender el equipo) podía bloquear login/select/search para
        # SIEMPRE. Si el timeout salta a mitad de lectura, la conexión
        # queda inservible — da igual: la excepción fuerza reconexión
        # limpia. El bucle IDLE no se ve afectado (espera con select()).
        self._imap = imaplib.IMAP4_SSL(
            self.config.imap_server, self.config.imap_port, timeout=30
        )
        self._imap.login(self.config.email_user, self.config.email_password)
        # Lectura-escritura solo si hay que poder marcar \Seen; si no,
        # readonly=True garantiza no alterar jamás el buzón.
        self._imap.select(
            self.config.mail_folder,
            readonly=not self.config.mark_processed_as_read,
        )

        # Keepalive TCP real: SO_KEEPALIVE solo no basta en Windows (usa
        # el intervalo del sistema: 2 HORAS). Con SIO_KEEPALIVE_VALS se
        # envían sondas a los 30 s de inactividad y cada 10 s: el NAT
        # del router ve tráfico y no mata la conexión IDLE en silencio.
        try:
            raw = self._imap.sock
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "SIO_KEEPALIVE_VALS"):  # Windows
                raw.ioctl(
                    socket.SIO_KEEPALIVE_VALS,
                    (1, 30_000, 10_000),  # activo, tras 30 s, cada 10 s
                )
        except OSError as exc:
            logger.debug("No se pudo activar el keepalive TCP: %s", exc)

        caps = [c.upper() for c in (self._imap.capabilities or ())]
        self._supports_idle = "IDLE" in caps
        logger.info(
            "Conectado. Soporte IDLE: %s. Modo: %s",
            self._supports_idle,
            "IDLE (tiempo real)"
            if (self._supports_idle and self.config.use_imap_idle)
            else f"sondeo cada {self.config.check_interval_seconds}s",
        )

    def _safe_logout(self) -> None:
        """Cierra la conexión ignorando cualquier error."""
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:  # noqa: BLE001
                pass
            self._imap = None

    # ------------------------------------------------------------------
    # Modo sondeo (fallback)
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        """Revisa el buzón cada `check_interval_seconds` segundos."""
        while not self._stop_requested:
            interval = max(5, int(self.config.check_interval_seconds))
            for _ in range(interval):
                if self._stop_requested:
                    return
                time.sleep(1)
            # NOOP mantiene viva la sesión y refresca el estado del buzón
            assert self._imap is not None
            self._imap.noop()
            self._check_new_mail()

    # ------------------------------------------------------------------
    # Modo IDLE (tiempo real)
    # ------------------------------------------------------------------
    def _idle_loop(self) -> None:
        """
        Bucle IMAP IDLE implementado sobre imaplib.

        imaplib no trae IDLE de serie, así que enviamos el comando a
        mano y escuchamos el socket. Cuando el servidor manda EXISTS
        o RECENT (correo nuevo), salimos de IDLE y revisamos el buzón.
        """
        assert self._imap is not None
        while not self._stop_requested:
            imap = self._imap
            tag = imap._new_tag().decode()  # noqa: SLF001 - necesario para IDLE
            imap.send(f"{tag} IDLE\r\n".encode())

            # El servidor debe contestar "+ idling" (o similar)
            response = imap.readline()
            if not response.startswith(b"+"):
                logger.warning("El servidor rechazó IDLE (%r). Paso a sondeo.", response)
                self._supports_idle = False
                self._poll_loop()
                return

            logger.debug("IDLE iniciado. Esperando avisos del servidor...")
            new_mail = False
            start = time.monotonic()
            sock = imap.socket()

            try:
                while not self._stop_requested:
                    if time.monotonic() - start > IDLE_TIMEOUT_SECONDS:
                        break  # renovar IDLE antes del límite del servidor

                    # IMPORTANTE: esperar con select(), SIN tocar el timeout
                    # del socket. Un timeout durante una lectura bufferizada
                    # deja el lector interno de imaplib inutilizable para
                    # siempre ("cannot read from timed out object"), lo que
                    # provocaba un bucle infinito de reconexiones.
                    if isinstance(sock, ssl.SSLSocket) and sock.pending():
                        ready = True  # datos ya descifrados en el buffer TLS
                    else:
                        readable, _, _ = select.select([sock], [], [], 2.0)
                        ready = bool(readable)
                    if not ready:
                        continue  # sin novedades todavía; comprobar parada/renovación

                    line = imap.readline()
                    if not line:
                        raise imaplib.IMAP4.abort("Conexión cerrada por el servidor")
                    upper = line.upper()
                    if b"EXISTS" in upper or b"RECENT" in upper:
                        new_mail = True
                        break
            finally:
                # Terminar IDLE correctamente. Si se pidió parar, el socket
                # ya fue abortado por stop(): no hay despedida que negociar.
                if not self._stop_requested:
                    try:
                        imap.send(b"DONE\r\n")
                        # Consumir hasta la respuesta etiquetada, con plazo
                        # máximo: una conexión muerta no debe colgarnos aquí.
                        deadline = time.monotonic() + 5.0
                        while time.monotonic() < deadline:
                            readable, _, _ = select.select([sock], [], [], 1.0)
                            if not readable:
                                continue
                            line = imap.readline()
                            if not line or line.decode(errors="replace").startswith(tag):
                                break
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Error cerrando IDLE: %s", exc)

            if self._stop_requested:
                return
            if new_mail:
                logger.info("El servidor avisa de correo nuevo (IDLE).")
            self._check_new_mail()

    # ------------------------------------------------------------------
    # Revisión y procesado de correos
    # ------------------------------------------------------------------
    def _check_new_mail(self) -> None:
        """Busca correos aún no procesados y los analiza uno a uno."""
        assert self._imap is not None
        # Lo que el usuario haya tocado en la ventana desde la última
        # vuelta (precio objetivo, ⭐ Deseado, Obtenido) tiene que valer
        # YA, sin reiniciar el programa.
        self._cargar_coleccion()
        folder = self.config.mail_folder

        # CRITERIO ÚNICO: progresión de UID — todo lo posterior al último
        # correo procesado, ESTÉ LEÍDO O NO.
        #
        # Histórico de por qué NO usar `SEARCH UNSEEN` como criterio
        # (2026-07-24, "ya no revisa correos"): si una oferta se marca
        # como leída por otra vía (Gmail del móvil, otra pestaña...)
        # desaparece de UNSEEN y el monitor queda ciego ante ella para
        # siempre. La progresión de UID es inmune a eso y el control de
        # duplicados hace el resto. El marcado \Seen de ofertas
        # procesadas (`mark_processed_as_read`) se conserva.
        last_uid = self.db.last_processed_uid(folder)
        if last_uid is None:
            # Primera ejecución de la vida: fijar línea base con los N
            # más recientes (no recorrer todo el histórico del buzón).
            typ, data = self._imap.uid("SEARCH", None, "ALL")
            self.checked.emit(time.strftime("%H:%M:%S"))
            if typ != "OK" or not data or not data[0]:
                return
            all_uids = [u.decode() for u in data[0].split()]
            cap = max(1, int(self.config.startup_check_count))
            uids = all_uids[-cap:]
            self._startup_scan_done = True
            logger.info(
                "Primera ejecución: se revisan los %d correos más "
                "recientes (de %d en la carpeta).",
                len(uids), len(all_uids),
            )
        else:
            typ, data = self._imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
            self.checked.emit(time.strftime("%H:%M:%S"))
            if typ != "OK" or not data or not data[0]:
                return
            # El rango "N:*" siempre devuelve al menos el último correo,
            # aunque ya esté procesado: el control de duplicados filtra.
            uids = [u.decode() for u in data[0].split()]
            if not self._startup_scan_done:
                self._startup_scan_done = True
                pending = [
                    u for u in uids if not self.db.is_uid_processed(folder, u)
                ]
                logger.info(
                    "Arranque: %d correo(s) nuevos desde el último "
                    "procesado (UID %d).",
                    len(pending), last_uid,
                )

        for uid in uids:
            if self._stop_requested:
                return
            # El rango "N:*" siempre devuelve al menos el último correo,
            # aunque ya esté procesado: el control de duplicados lo filtra.
            if self.db.is_uid_processed(folder, uid):
                continue
            try:
                self._process_uid(uid)
            except Exception as exc:  # noqa: BLE001 - un correo raro no debe tumbar el monitor
                logger.error("Error procesando UID %s: %s", uid, exc)
            finally:
                # Pase lo que pase, nunca reprocesar el mismo correo.
                self.db.mark_uid_processed(folder, uid)

        self._recheck_marked_unread()
        self.checked.emit(time.strftime("%H:%M:%S"))

    # Cuántos re-análisis manuales como máximo por revisión. Solo entran
    # correos que el usuario marcó a mano (procesados + no leídos), así
    # que el tope es holgado; cubre también pruebas masivas.
    _RECHECK_CAP = 50

    def _recheck_marked_unread(self) -> None:
        """
        RE-ANÁLISIS MANUAL: ofertas marcadas como no leídas a propósito.

        El monitor marca \\Seen las ofertas de Todocolección procesadas;
        por tanto, un correo del remitente vigilado que esté NO LEÍDO y
        ya conste como procesado solo puede haberlo marcado el usuario a
        mano → se reprocesa (y se vuelve a notificar si cumple las
        condiciones). Los no-leídos de Todocolección NUNCA procesados
        (boletines históricos del backlog) se ignoran: reprocesarlos
        sería la avalancha de 2026-07-24 otra vez.

        Solo activo con `mark_processed_as_read`: sin el marcado, un
        no-leído no es ninguna señal.
        """
        if not self.config.mark_processed_as_read:
            return
        assert self._imap is not None
        folder = self.config.mail_folder
        try:
            typ, data = self._imap.uid(
                "SEARCH", None, "UNSEEN", "FROM", f'"{self.config.sender_filter}"'
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Búsqueda de re-análisis fallida: %s", exc)
            return
        if typ != "OK" or not data or not data[0]:
            return
        unseen_tc = [u.decode() for u in data[0].split()]
        recheck = [
            u for u in unseen_tc if self.db.is_uid_processed(folder, u)
        ][-self._RECHECK_CAP:]
        if not recheck:
            return
        logger.info(
            "Re-análisis manual: %d oferta(s) marcadas como no leídas "
            "por el usuario (%s).",
            len(recheck), ", ".join(recheck),
        )
        for uid in recheck:
            if self._stop_requested:
                return
            try:
                # Reprocesado consciente: puede volver a notificar. Al
                # terminar, _process_uid la marca \Seen de nuevo y sale
                # del conjunto UNSEEN.
                self._process_uid(uid)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error reprocesando UID %s: %s", uid, exc)

    def _mark_seen(self, uid: str) -> None:
        """
        Marca un correo como leído (\\Seen) si la opción está activa.

        Tolerante: si el servidor lo rechaza (p. ej. carpeta abierta en
        solo lectura), queda en el log y el monitor sigue.
        """
        if not self.config.mark_processed_as_read:
            return
        assert self._imap is not None
        try:
            typ, _ = self._imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            if typ == "OK":
                logger.debug("UID %s marcado como leído.", uid)
            else:
                logger.warning("No se pudo marcar como leído el UID %s.", uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error marcando como leído el UID %s: %s", uid, exc)

    def _process_uid(self, uid: str) -> None:
        """
        Analiza un único correo identificado por su UID.

        En DOS fases para no tocar el correo personal:
        1. Descarga SOLO las cabeceras (remitente/asunto) y descarta en
           el acto lo que no venga de Todocolección — sin bajar el
           cuerpo, sin parsear y sin ruido en el log (antes se
           descargaban y analizaban completos correos de YouTube, etc.).
        2. Solo si es de Todocolección: descarga completa y análisis.
        """
        assert self._imap is not None

        # --- Fase 1: cabeceras --------------------------------------------
        typ, data = self._imap.uid(
            "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
        )
        if typ != "OK" or not data or data[0] is None:
            logger.warning("No se pudieron leer las cabeceras del UID %s", uid)
            return
        raw_hdr = data[0][1] if isinstance(data[0], tuple) else data[0]
        hdr = email.message_from_bytes(raw_hdr)

        self.mails_checked_count += 1
        self.mail_processed.emit(self.mails_checked_count)

        if not is_from_todocoleccion(hdr, self.config.sender_filter):
            logger.debug(
                "UID %s ignorado sin descargar: remitente ajeno (%s).",
                uid, decode_header_value(hdr.get("From", "")),
            )
            return

        # --- Fase 2: correo completo (solo Todocolección) -----------------
        typ, data = self._imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not data or data[0] is None:
            logger.warning("No se pudo descargar el correo UID %s", uid)
            return

        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        save_last_email(raw, app_dir())
        msg = email.message_from_bytes(raw)

        subject = decode_header_value(msg.get("Subject", ""))
        sender = decode_header_value(msg.get("From", ""))
        logger.info("Correo leído [UID %s] De: %s | Asunto: %s", uid, sender, subject)

        # Identidad ESTABLE del correo: con ella cada dato que genere
        # (historial, precios, lotes, publicaciones vigiladas) entra UNA
        # sola vez aunque el correo se reprocese. Si ya dejó su fila de
        # historial, este pase es un RE-ANÁLISIS: se recalcula todo por
        # si falta algún dato, pero NO se vuelve a notificar (si no, un
        # volcado del backlog dispararía cientos de avisos repetidos).
        msg_id = message_key(msg)
        ya = self.db.email_inserted_status(msg_id)
        reanalisis = ya["historial"] > 0
        if reanalisis:
            logger.info(
                "Correo ya insertado (historial %d · precios %d · lotes %d · "
                "enlaces %d/%d): re-análisis SIN volver a notificar.",
                ya["historial"], ya["precios"], ya["lotes"],
                ya["enlaces"], ya["lotes_vigilados"],
            )

        # Oferta de Todocolección ya revisada → marcarla como LEÍDA en
        # el buzón (los correos personales nunca se tocan).
        self._mark_seen(uid)

        # 2) ¿Es un aviso de MIS favoritos? (asunto obligatorio; las
        #    recomendaciones, ventas realizadas y boletines se descartan
        #    aquí — nada que el usuario no puso en favoritos pasa de
        #    este punto, ni al flujo de precios NI al de lotes).
        plain, html_text, _links = extract_bodies(msg)
        body = plain if len(plain) > len(html_text) else html_text
        if not is_price_alert(
            subject,
            body,
            self.config.subject_keywords,
            exclude_keywords=getattr(self.config, "exclude_subject_keywords", None),
        ):
            logger.debug("Ignorado: correo de Todocolección sin aviso de favoritos.")
            # Registrar TAMBIÉN los descartados: el Historial debe mostrar
            # TODO lo analizado de Todocolección, no solo lo que pasó el
            # filtro de favoritos (boletines, vendidos, pujas...). Con su
            # precio actual si el correo lo trae ("Precio actual: X €" en
            # pujas, primer importe en otros) — sin tocar la serie de
            # Precios, que es solo de alertas analizadas.
            from app.utils import extract_current_price

            # Con el ANUNCIO al que corresponde, no solo el asunto: en
            # los correos de ofertas el asunto es genérico («Haz una
            # oferta al vendedor») y en el Historial no había manera de
            # saber de qué libro hablaban (2026-08-07). El enlace y el
            # título salen del propio anuncio; el precio, del correo.
            descartado = parse_alert_email(msg)
            enlace = descartado.link
            titulo = descartado.title or ""
            if enlace and (not titulo or titulo.strip() == (subject or "").strip()):
                del_slug = titulo_desde_url(enlace)
                if del_slug:
                    titulo = del_slug
            self.db.add_history(
                titulo=titulo or subject or "(sin asunto)",
                precio_ant=None,
                precio_new=extract_current_price(body),
                descuento=None,
                enlace=enlace,
                estado="descartado",
                mensaje_id=msg_id,
            )
            return

        # 3) Detección de LOTES — solo dentro de avisos de favoritos
        #    (p. ej. "Bajada de precio: LOTE DE 20 LIBROS...").
        lot = None
        if self.config.enable_lot_detection:
            lot: LotAlert | None = detect_lot(msg)
            min_books = max(2, int(self.config.min_lot_books))
            if lot is not None and lot.book_count >= min_books:
                if not reanalisis:
                    self.notifier.notify_lot(lot)
                    self.lots_detected_count += 1
                    self.lot_detected.emit(self.lots_detected_count)
                lot_title = f"[LOTE ×{lot.book_count}] {lot.title}"
                self.db.add_history(
                    titulo=lot_title,
                    precio_ant=None,
                    precio_new=lot.price,
                    descuento=None,
                    enlace=lot.link,
                    estado="lote",
                    mensaje_id=msg_id,
                )
                # Serie de precios del LOTE (espacio `lote::`, ventana
                # Lotes) — separada de las series de tomos; punto nuevo
                # solo si difiere del último (nada de duplicados).
                if lot.price is not None:
                    prev_lot = self.db.last_lot_price(lot_title)
                    if prev_lot is None or abs(lot.price - prev_lot) >= 0.01:
                        self.db.add_lot_price_point(
                            lot_title, lot.price, url=lot.link,
                            mensaje_id=msg_id,
                        )
                # Y queda VIGILADO en la pestaña Lotes (enlace + precio),
                # para poder reconsultarlo sin pegarlo a mano.
                if lot.link:
                    self.db.add_lote_if_new(lot_title, lot.link, lot.price)
                logger.info(
                    "Lote de %d libros notificado (umbral: %d).",
                    lot.book_count, min_books,
                )

        # 4) Extraer datos y calcular descuento
        alert: PriceAlert = parse_alert_email(msg)
        logger.info(
            "Descuento detectado: '%s' %s → %s (%s%%)",
            alert.title,
            alert.old_price,
            alert.new_price,
            alert.discount_percent,
        )

        # 5) Cruce con la colección: si la oferta corresponde a un tomo,
        #    el TÍTULO CANÓNICO de la base de datos (autor — obras, en
        #    su orden original) sustituye al del anuncio en la alerta,
        #    el historial y la serie de precios (así el mismo libro
        #    vendido por distintos vendedores comparte serie).
        # El título del ANUNCIO, antes de sustituirlo por el canónico:
        # es donde se ve si la publicación trae varios volúmenes.
        titulo_anuncio = alert.title
        tomo = collection.match_tomo(self._tomos, alert.title)
        if tomo is not None:
            logger.info(
                "Identificado con la colección: %s (anuncio: '%s')",
                tomo.canonical_title(), alert.title,
            )
            alert.title = tomo.canonical_title()

        # 6) Historial de precios por libro + aviso de subida.
        #    REGLAS de la serie (petición 2026-07-26): SOLO tomos
        #    identificados con la colección (título canónico = el de la
        #    BD) y SOLO anuncios de UN tomo — los lotes o títulos
        #    multi-tomo contaminarían la gráfica del tomo con el precio
        #    del conjunto.
        #    "DIÁLOGOS TOMO I, II Y III" o "ANALES - LIBROS I-IV +
        #    XI-XVI" no dicen cuántos libros son, pero ENUMERAN los
        #    volúmenes: su precio es el del conjunto y no puede entrar
        #    en la serie de un tomo suelto (2026-08-02).
        multi_tomo = (
            lot is not None
            or count_books_in_text(subject)[0] >= 2
            or varios_volumenes(subject)
            or varios_volumenes(titulo_anuncio)
        )
        if alert.new_price is not None and tomo is not None and not multi_tomo:
            prev = self.db.last_price(alert.title)
            nuevo_punto = self.db.add_price_point(
                alert.title, alert.new_price, url=alert.link,
                mensaje_id=msg_id,
            )
            # La publicación queda VIGILADA en la ficha del tomo
            # (Colección → doble clic): enlace + precio, sin pegarla a
            # mano. Dedup por URL sin parámetros de campaña.
            if alert.link and (tomo.numero or tomo.orden) is not None:
                # Por NÚMERO: el orden lo comparten tres pares de tomos
                self.db.add_tomo_link_if_new(
                    tomo.numero or tomo.orden, alert.link, alert.new_price
                )
            if nuevo_punto and not reanalisis and prev is not None \
                    and alert.new_price > prev:
                self.notifier.notify_info(
                    "📈 Ha vuelto a subir",
                    f"{alert.title}\n"
                    f"{format_price(prev)} → {format_price(alert.new_price)}",
                    link=alert.link,
                )

        # 7) Resolución del umbral, por prioridad:
        #    tomo ⭐ DESEADO (umbral reducido) > patrón de la tabla de
        #    umbrales > umbral global.
        if tomo is not None and tomo.deseado:
            threshold = self.config.wished_discount_percent
            logger.info(
                "Tomo deseado: umbral reducido %.1f%% (global: %.1f%%).",
                threshold, self.config.min_discount_percent,
            )
        else:
            custom = self.db.threshold_for(alert.title)
            threshold = (
                custom if custom is not None
                else self.config.min_discount_percent
            )
            if custom is not None:
                logger.info(
                    "Umbral específico para '%s': %.1f%% (global: %.1f%%).",
                    alert.title, custom, self.config.min_discount_percent,
                )

        # ¿Precio objetivo alcanzado? (comparación ABSOLUTA en €,
        # independiente del porcentaje de descuento)
        target_hit = (
            tomo is not None
            and tomo.precio_objetivo is not None
            and alert.new_price is not None
            and alert.new_price <= tomo.precio_objetivo
        )
        if target_hit:
            logger.info(
                "Precio objetivo alcanzado: %.2f € <= %.2f € ('%s').",
                alert.new_price, tomo.precio_objetivo, alert.title,
            )
        supera_umbral = (
            alert.discount_percent is not None
            and alert.discount_percent >= threshold
        )
        if supera_umbral or target_hit:
            # El precio objetivo se basa en el precio absoluto extraído
            # (no en un %), así que no requiere la puerta de fiabilidad
            # del descuento.
            if not target_hit and not alert.is_reliable():
                # Porcentaje de regex genérica sin precios reales: casi
                # seguro texto de marketing ("100 % seguro"), no una
                # oferta. Registrar sin notificar.
                estado = "ignorado"
                logger.warning(
                    "Descartado sin notificar (datos no fiables): '%s' "
                    "%s%% sin precios [fuente: %s].",
                    alert.title,
                    alert.discount_percent,
                    alert.sources.get("discount_percent", "?"),
                )
            else:
                extra = None
                if tomo is not None:
                    extra = collection.tomo_label(tomo)
                    if tomo.paginas:
                        extra += f" · {tomo.paginas} págs."
                    if tomo.poseido:
                        extra += " · ya obtenido ✔"
                    elif tomo.deseado:
                        extra += " · ⭐ DESEADO"
                    else:
                        extra += " · ¡TE FALTA!"
                    if collection.is_rare(tomo):
                        extra += " · 💎 RARO (360-415)"
                    elif collection.is_appendix(tomo):
                        extra += " · apéndice (fuera de colección)"
                    if target_hit:
                        extra += " · precio objetivo alcanzado"
                if not reanalisis:
                    self.notifier.notify_price_drop(alert, extra_line=extra)
                    self.alerts_sent_count += 1
                    self.alert_sent.emit(self.alerts_sent_count)
                estado = "notificado"
                if supera_umbral:
                    logger.info("Notificación enviada (%.1f%% >= %.1f%%).",
                                alert.discount_percent, threshold)
                else:
                    logger.info("Notificación enviada (precio objetivo).")
        else:
            estado = "ignorado"
            logger.info(
                "Descuento por debajo del umbral (%s%% < %.1f%%). Ignorado.",
                alert.discount_percent, threshold,
            )

        # 8) Guardar SIEMPRE en el historial
        self.db.add_history(
            titulo=alert.title,
            precio_ant=alert.old_price,
            precio_new=alert.new_price,
            descuento=alert.discount_percent,
            enlace=alert.link,
            estado=estado,
            mensaje_id=msg_id,
        )
