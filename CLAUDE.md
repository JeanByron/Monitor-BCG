# Monitor BCG — contexto del proyecto

(Se llamó "Monitor Todocolección" hasta el 2026-08-04; ese nombre solo
sobrevive como valor antiguo de la clave Run, que se migra al arrancar.)

App de escritorio Windows (PySide6) que vigila el correo IMAP y notifica
bajadas de precio y lotes de libros de la Biblioteca Clásica Gredos.
Tests: `python -m pytest tests/ -q`.

## Repositorio (2026-08-13)

`https://github.com/JeanByron/Monitor-BCG.git` (rama `main`). Se sube el
CÓDIGO; los datos de ejecución se quedan fuera — `config.json` (correo y
contraseña cifrada con DPAPI), `tc_monitor.db`, `log.txt`,
`last_email.eml` y `BDtomos/` entero SALVO `titulosBCG.xlsx`, sin el
cual la aplicación no sabe qué tomos existen. `textos.db` (384 MB) no
podría subirse aunque se quisiera: GitHub corta en 100 MB por archivo, y
además se reconstruye solo desde los `.jsonl`. En su lugar viaja
`config.example.json`. Tres trampas, las tres medidas aquí:
- **git NO admite comentarios al final de una línea de patrón**:
  `config.json  # credenciales` es un patrón llamado
  «config.json  # credenciales» y no casa con nada. Con esa primera
  versión del `.gitignore`, `config.json` —con el correo y el blob
  DPAPI— quedó preparado para el commit. Los comentarios van en su
  propia línea, y conviene comprobar con `git ls-files` lo que de verdad
  entra ANTES de confirmar.
- **En `.gitattributes` gana la ÚLTIMA regla que casa**: con
  `* text=auto` al final se llevaba por delante la excepción de los
  `.eml`. Esos correos son de verdad y las pruebas leen sus cabeceras
  tal cual, así que van como binarios (`*.eml -text`): si git les cambia
  los finales de línea al clonar, el parseo deja de cuadrar con el
  `.expected.json`.
- El correo de prueba de Ausonio traía el Gmail del usuario cinco veces;
  se sustituyó por `usuario@ejemplo.com` (el `.expected.json` no mira
  ese campo, las 361 pruebas siguen pasando).

## Estructura (reorganizada 2026-07-26)

Código en el paquete `app/` (imports SIEMPRE `from app.x import`);
herramientas de desarrollo en `tools/` (con bootstrap de sys.path);
`assets/` (iconos, version_info), `docs/`. Los DATOS DE EJECUCIÓN viven
en la RAÍZ: config.json, tc_monitor.db, log.txt, last_email.eml,
BDtomos/, tests/. `app_dir()` devuelve la raíz del proyecto en
desarrollo (parent.parent de app/config.py) y la carpeta del .exe
congelado. El registro Run apunta a main.py en la raíz (sin cambios).
`fetch de publicaciones`: Todocolección devuelve 403 a urllib/curl
(huella TLS) — SIEMPRE vía `ListingPriceFetcher` (QWebEngineView
oculto, único que pasa la protección y renderiza Wallapop). Usar la
instancia ÚNICA `shared_price_fetcher()` (jamás una por diálogo): vive
lo que la app, así las consultas SIGUEN aunque se cierre la ficha y el
precio aparece al reabrir; el callback persiste en BD primero y toca la
UI después (RuntimeError del diálogo muerto ya capturado en `_finish`).
Velocidad (2026-07-26): imágenes desactivadas (AutoLoadImages=False) y
SONDEO ADAPTATIVO — captura inmediata en loadFinished y reintentos de
400 ms (tope 3 s) SOLO si aún no hay precio (Wallapop lo pinta por JS);
prohibida la espera fija (2,5 s hacían cada consulta lenta). Botones de
búsqueda externa (ficha del tomo): consulta = autor + obras, SIN
añadir "Gredos" (estrechaba resultados) y SIN autor colectivo. Regla
ÚNICA: `collection.author_for_search` / `is_collective_author` —
compara el autor normalizado SIN paréntesis ni puntuación contra
`_COLLECTIVE_AUTHORS` (vvaa, aavv, varios, anónimo, desconocido…), así
cubre "VV.AA.", "VVAA (sofistas)" y "Anónimo"; de la aclaración entre
paréntesis se conserva el descriptor ("sofistas"), que es parte del
título real del tomo. Son 38 tomos de 423; jamás duplicar esta lógica
en la GUI (la comparación suelta dejaba escapar 4 variantes).

## Sistema de diseño de la UI (OBLIGATORIO para cualquier ventana nueva)

Estética: tomo clásico de la Biblioteca Clásica Gredos — SIMILPIEL azul
oscuro con estampación dorada (ver `gui.py`). Referencia real: la BCG
va en cartoné/similpiel azul oscuro que con poca luz parece negra;
base = Oxford Blue auténtico #002147 (Pantone 282) ajustado al cuero.

### Paleta (constantes en gui.py — no inventar colores nuevos)

| Constante | Valor | Uso |
|---|---|---|
| `AZUL_OXFORD` | `#03142a` | piel/cubierta base (casi negro azulado) |
| `AZUL_OXFORD_CLARO` | `#082343` | brillo superior del cuero |
| `AZUL_OXFORD_OSCURO` | `#01070f` | sombra inferior |
| `AZUL_CAMPO` | `#020c1c` | fondos de campos, tablas, listas |
| `ORO` | `#d4af37` | dorado base |
| `ORO_CLARO` | `#efd88f` | brillo de pan de oro |
| `ORO_TEXTO` | `#e7cd7f` | texto dorado general |
| `ORO_VIEJO` | `#b08d2e` | filetes y bordes |
| `ORO_APAGADO` | `#7d6420` | bordes secundarios, pespuntes |

Tipografía: **Georgia** en TODA la aplicación (2026-08-05, a petición
del usuario), títulos en mayúsculas con letter-spacing. Hoja de estilos
global: `GREDOS_QSS`. NUNCA construir un `QFont` a mano: usar el helper
`gui.fuente(tam, negrita=, espaciado=, cursiva=)`, que trae tres cosas
sin las que el texto se ve duro:
- `setFamilies([Georgia, Palatino Linotype, Segoe UI Symbol, Cambria])`.
  Las reservas están MEDIDAS, no puestas por gusto: Georgia no tiene
  griego POLITÓNICO (ἀ ᾳ ῥ ὧ), que abunda en el corpus, ni los glifos
  de la propia interfaz (▸ ▾ ⧉ ✔ ✖). Sin la lista, Qt elegía cualquier
  fuente del sistema y el griego salía de otro estilo en cada equipo.
- `PreferAntialias | PreferQuality`.
- `PreferNoHinting`, que es el que de verdad se nota: sin él Windows
  DEFORMA cada letra para encajarla en la rejilla de píxeles y en una
  serif de remates finos eso se ve como trazos desiguales.
Además, todo `paintEvent` que escriba texto necesita
`setRenderHint(QPainter.RenderHint.TextAntialiasing)` junto al
`Antialiasing` de siempre, y `MainWindow` hace `app.setFont(fuente(10.5))`
—la hoja de estilos fija la FAMILIA, pero el alisado solo viaja en un
QFont de verdad—.

**GRIEGO (2026-08-05)**: Georgia trae el griego MODERNO (λόγος) pero NO
el POLITÓNICO (ἀ ᾳ ῥ ὧ), que es el de los tomos. Medido con
`QTextLayout`: sin reservas Qt resolvía esas letras con **Tahoma** (palo
seco en mitad de una serif) y, aun con la reserva puesta, una misma
palabra se partía ENTRE DOS TIPOGRAFÍAS — la ἀ de Palatino y «λλήλων»
de Georgia, en 4 de cada 5 palabras politónicas. Por eso el griego se
trata por RACHAS enteras, nunca carácter a carácter:
`partir_por_griego(texto)` devuelve `(fragmento, ¿es griego?)` y
`fuente_griega()` da Palatino Linotype (politónico completo) con la
escala `_ESCALA_GRIEGA` = 7,70/8,30, la razón MEDIDA entre las alturas-x
de Georgia y Palatino — sin ella el griego se ve más grande que el
castellano que lo rodea. En `PasajeDialog._html` cada racha va en su
`<span>` (compuesto con el resaltado de la búsqueda, que sigue
funcionando encima del griego); en las celdas de tabla no cabe HTML, así
que si `es_griego(texto)` la celda ENTERA lleva `fuente_griega`.
Las pruebas de tipografía se SALTAN sin pantalla: sin las fuentes de
Windows, `QTextLayout.glyphRuns()` provoca un fallo de acceso y
`QRawFont` miente — hay que comprobarlas con `QT_QPA_PLATFORM=windows`
(guarda `_sin_fuentes_del_sistema` en `tests/test_gui.py`).

### Reglas de ventanas

- **Sin marco de Windows**: todas las ventanas son frameless con
  `TitleBar` propia, esquinas redondeadas (12 px) y sombra pintada por
  `_ShadowFrameMixin` (margen translúcido `_SHADOW_MARGIN = 14`).
- **Ventana principal**: `LeatherFrame` con tonos normales; botón de
  **pantalla completa de DOS estados** (normal ↔ fullscreen, glifos
  ⛶/❐ — nunca usar maximizado como tercer estado) y redimensionado por
  bordes (filtro global + `startSystemResize`).
- **VENTANAS SECUNDARIAS (diálogos)**: SIEMPRE heredar de
  `FramelessDialog`, que aplica el mismo cuero en **tonos ~30 % más
  oscuros** (`LeatherFrame(dark=True)` → `QColor.darker(132)`) y el
  **redimensionado por bordes** (`_EdgeResizeMixin`). Cualquier
  diálogo/ventana futura abierta desde botones debe seguir esta regla:
  mismo diseño, versión oscura, redimensionable. Usar
  `resize()+setMinimumSize()`, NUNCA `setFixedSize()` (bloquearía el
  redimensionado).
- `_EdgeResizeMixin`: instala su filtro global en `showEvent` y lo
  retira en `hideEvent` (no acumular filtros por diálogo abierto).
  El filtro usa SIEMPRE `event.globalPosition()`, jamás QCursor.pos()
  (robaba clics legítimos cuando el cursor real no coincidía,
  2026-07-26).
- Fondo de cuero: procedural en `LeatherFrame` — tesela 1024×1024
  TESELABLE (desplazamientos periódicos): red CELULAR de arrugas en
  CURVAS suaves (quadTo) entre células abombadas + moteado de tinte +
  poros + pliegues largos. El cuero real es red
  celular, NO ruido de puntos. Filete de borde que se AVIVA hacia donde
  está el ratón y marco interior hairline con ornamentos de esquina
  (orla estampada).
  **SIN el reflejo sobre la piel** (2026-08-08): eran tres capas
  concéntricas que paseaban una mancha de luz por toda la cubierta y el
  usuario pidió quitar «el efecto linterna». Lo que SÍ se conserva —y
  se pidió expresamente mantener— es la iluminación del BORDE siguiendo
  al cursor. No reponer la mancha del fondo sin que lo pida.
- **LUZ QUE SIGUE AL RATÓN (`_BordeQueSigueAlRaton`, 2026-08-08)**: la
  usan `LeatherFrame` (borde de la ventana, alcance 150 px) y
  `GlowLineEdit` (todos los campos de texto, alcance 110 px). Reglas:
  · Se sondea la posición GLOBAL del cursor, no `mouseMoveEvent`: los
  hijos se comen los eventos del padre y el marco no vería pasar el
  ratón por encima de un botón.
  · UN solo `rastreador_de_cursor()` para toda la app (un QTimer por
  campo sería tirar CPU) y solo corre mientras haya oyentes: se
  suscribe en `showEvent` y se desuscribe en `hideEvent`, así con las
  ventanas cerradas no queda nada vivo.
  · Solo repinta cuando el dato CAMBIA; si no, serían 25 repintados por
  segundo y por campo.
  · Con el FOCO puesto, `GlowLineEdit` no pinta nada encima: el QSS ya
  da el filete en oro vivo y superponerlo lo emborrona.
  · Todo campo nuevo va con `GlowLineEdit`, nunca `QLineEdit` a secas
  (hay prueba que lo comprueba en cada ventana). Diálogos oscuros: `darker(120)` (no más — con la base
  casi negra se perdería el azul).

### Botones y componentes

- Usar **`GlowButton`** para todo botón: pintado a mano — letras con
  sombra de grabado (letterpress), encendido en oro con "calor"
  animado en fundido (QVariantAnimation 180 ms, no encendido brusco),
  halo en corona de 1 px + núcleo radial en el puntero, borde avivado.
  NUNCA rellenar el botón de oro macizo en hover. El ancho mínimo se
  deriva del texto (las etiquetas nunca se cortan) — etiquetas cortas.
- Avisos/mensajes: `GredosMessageBox` (nunca `QMessageBox`). `ask`
  devuelve bool; `ask_ex` distingue TRES salidas — "aceptar",
  "cancelar" (segundo botón) y "cerrar" (✕ o Esc): la ✕ NO es el
  segundo botón, quien cierra quiere dejarlo estar (por eso la ✕ del
  aviso del OCR aborta el análisis). La salida se anota en un dict de
  FUERA, nunca leyendo el diálogo tras exec() (WA_DeleteOnClose).
- **Barra de progreso**: `GlowProgress` (nunca `QProgressBar`): campo
  hundido en `AZUL_CAMPO`, relleno de pan de oro con gradiente vertical
  en veladura + destello especular que lo recorre, filete y pespunte
  discontinuo. Sobre el oro las letras van en TINTA OSCURA (dorado
  sobre dorado no se lee). `setRange(0)` = indeterminada (banda en
  vaivén). La animación se para en `hideEvent`: una barra oculta
  animándose es CPU tirada. API: `arrancar(texto, total)` /
  `avanzar(fase, hechas, total)` / `parar()`.
- **Tablas**: usar `GlowTable` (no QTableWidget a secas): su delegado
  `_GlowRowDelegate` pinta la luz que sigue la PUNTA del ratón por la
  fila; en la fila SELECCIONADA, veladura de oro claro con gradiente
  vertical (metal en sombra) y DESTELLO ESPECULAR al pasar el cursor
  (núcleo #fff6d0 → oro claro → oro; SIN línea de reflejo horizontal —
  se probó y se descartó). El delegado quita `State_HasFocus` (fuera recuadro
  azul de foco) y el QSS lleva `outline: none` +
  `selection-background-color: transparent` (una sola capa, el fondo
  selected lo pinta el delegado). Jamás oro macizo plano. Instalar
  `GlowHeader` como
  cabecera horizontal (mismo brillo de letras que GlowButton,
  indicador ▴/▾), ocultar el header vertical,
  `setShowGrid(False)`, celdas numéricas con `_NumItem`
  (orden numérico real), título con tooltip del texto completo, enlace
  como "Ver anuncio ⧉" con la URL en `UserRole`. Listados largos llevan
  barra superior con contador + "Buscar:" (filtro por título vía
  `utils.normalize`, oculta filas) + combo "Ordenar por".
- Paneles de datos: marco `#panel` (filete sólido) + `#stitch` interior
  (pespunte discontinuo, efecto bordado).
- Líneas divisorias: `_filete()`.

### Trampas conocidas (no repetir)

- **NO usar `QGraphicsDropShadowEffect` en un contenedor** que tenga
  hijos con efectos propios: Qt deja de pintar los hijos. Las sombras
  de ventana se pintan a mano en `paintEvent`.
- Diálogos con `WA_DeleteOnClose` + gc.collect() periódico en el hilo
  de la GUI: sin ambos, el recolector de ciclos destruía diálogos (con
  QTimer vivos) desde el hilo del monitor → "QBasicTimer::stop ...
  different thread" y cierre de la app (2026-07-25). Consecuencia: NO
  tocar widgets de un diálogo tras exec() — capturar en accept()
  (ConfigDialog.accept ya lo hace).
- El QSS global no se aplica si no existe `QApplication` con
  `setStyleSheet(GREDOS_QSS)` (lo hace `MainWindow.__init__`).
- Widgets nuevos con fondo propio (listas, tablas, spinners) necesitan
  regla en `GREDOS_QSS`, si no salen blancos (ya cubiertos: QListWidget,
  QTableWidget, QLineEdit/QSpinBox/QDoubleSpinBox/QTimeEdit, scrollbars,
  QComboBox y `QTableWidget::indicator` — casillas de tabla en oro).
- Empaquetado: SOLO `MonitorBCG.spec`. JAMÁS meter
  `config.json` en `datas` del spec (contiene la contraseña); va junto
  al .exe. Los ICONOS sí van en `datas` y se resuelven con
  `config.resource_path` (junto al .exe → `sys._MEIPASS`): sin eso el
  build empaquetado se quedaba sin logo en los toasts. El .spec se
  ejecuta como Python — tenía `icon=` DUPLICADO desde la reorganización
  y reventaba con SyntaxError; hay test que lo compila. El registro Run
  usa `--tray` (arranque en segundo plano).
  **NO EXCLUIR MÓDULOS DE Qt del spec (2026-08-09)**: la app importa
  seis (QtCore, QtGui, QtWidgets, QtNetwork, QtWebEngineCore,
  QtWebEngineWidgets), así que tienta quitar QtQml/QtQuick para adelgazar
  el .exe — pero QtWebEngine está CONSTRUIDO sobre Qt Quick y los carga
  por dentro: sin ellos muere `ListingPriceFetcher`, y el fallo no se ve
  hasta consultar un precio con el programa ya empaquetado. `excludes`
  solo lleva `tkinter` (únicamente lo usa `tools/debug_panel.py`, que no
  entra) y `pytest`. `requirements.txt` faltaba **PyMuPDF**, que es
  obligatorio (`pdftext` lo importa como `fitz`, y tarde: dentro de las
  funciones).
- Pruebas: `python -m pytest tests/ -q` (117). `tests/test_gui.py` abre
  cada ventana sin pantalla (`QT_QPA_PLATFORM=offscreen`) — ahí van las
  regresiones de interfaz; el resto son de lógica pura. Hay test de
  concurrencia: los métodos de `Database` NO pueden anidar `self._lock`
  (colgarían la app entre el hilo del monitor y la GUI).

## Arquitectura no-UI (resumen)

- **RUIDO DE LOS ANUNCIOS EN EL LOG (2026-08-08)**: `ListingPriceFetcher`
  visita Todocolección y Wallapop, que llevan anuncios de Google, y sus
  scripts vuelcan decenas de avisos por consulta —«[GPT] … deprecated»,
  «AdSense head tag…», «FedCM get() rejects»— que Qt reenviaba al log
  como «js: …», tapando el registro de verdad. `_PaginaSinRuido`
  (QWebEnginePage con `javaScriptConsoleMessage` vacío) se los traga.
  No son fallos de la aplicación ni afectan al precio. Si algún día hay
  que depurar una página, quitar ese método.
- `notification.py`: cola FIFO con hilo daemon y 2 s entre toasts (las
  ráfagas simultáneas perdían avisos); cascada winotify → PowerShell
  WinRT → globo de bandeja; SIN portada del anuncio en el toast (se
  probó y el usuario la quitó, 2026-07-26): solo el logo de la app.
  `APP_ID` ASCII (`BCG.Monitor`), y la ventana usa ESE MISMO valor como
  AUMID (`gui.py` importa `notification.APP_ID`; duplicar la cadena ya
  se prestó a que se separaran al renombrar la app).
- `imap_monitor.py`: criterio único de búsqueda = **progresión de UID**
  (`UID último+1:*`), primera ejecución acotada a `startup_check_count`.
  PROHIBIDO volver a `SEARCH UNSEEN` como criterio: si el móvil u otra
  pestaña marcan una oferta como leída, desaparece de UNSEEN y el
  monitor queda ciego (pasó el 2026-07-24, "ya no revisa correos");
  marca `\Seen` las ofertas de Todocolección procesadas (correos
  personales intactos); **re-análisis manual**: una oferta de TC
  NO-LEÍDA y ya procesada = el usuario la marcó a mano →
  `_recheck_marked_unread` la reprocesa y puede re-notificar (tope 10
  por ciclo; los no-leídos de TC jamás procesados se ignoran — son el
  backlog; tope ahora 50); higiene 2026-07-25: 71 ofertas pre-marcado
  puestas \Seen. `is_price_alert`: exclusiones de asunto → keyword en
  ASUNTO → o **frase SEMÁNTICA de bajada en el CUERPO** ("Descuento del
  X%", "ha bajado de precio") — los avisos reales llevan como asunto
  SOLO el título del anuncio (bug 2026-07-25: keyword-en-asunto
  obligatoria dejó de detectar TODO); JAMÁS aceptar por el substring
  suelto "descuento" (avalancha 2026-07-24); también entran las
  subastas de seguimientos ("finaliza hoy a las", "sale a subasta",
  "comienza la cuenta atrás" — registran precio/enlace y quedan
  "ignorado" si no hay descuento); las pujas ajenas ("Han hecho una
  oferta al vendedor") siguen descartadas; vendidos/novedades/
  boletines en `exclude_subject_keywords`; detección de lotes SOLO dentro de avisos
  de favoritos (tras el filtro, nunca antes); **puerta de fiabilidad**:
  solo se notifica si `PriceAlert.is_reliable()` (ambos precios, o % de
  frase semántica — jamás % de regex genérica sin precios).
  **CORREOS DE VARIOS ANUNCIOS (2026-08-07)**: el correo «Haz una oferta
  al vendedor» notificó «Foto número 1 del pedido · 40 € → 8,91 € ·
  35 %» con cada dato de un ANUNCIO DISTINTO (40 € del lote real, y
  8,91 € y el −35 % de un «HISTORIA DE ESPAÑA» del carrusel). Tres
  defensas, y las tres hacen falta porque cada una tapa un hueco de la
  anterior:
  (1) `utils.recorta_carrusel` corta el correo en «Te puede
  interesar» / «Productos relacionados» / «Otros lotes del vendedor», y
  se aplica en `_extract_payloads` — el ÚNICO punto por el que pasan el
  filtro de favoritos, el parser de precios y el de títulos, así que
  basta hacerlo una vez. NO corta si delante quedan menos de 400
  caracteres (dejaría el correo sin su propio anuncio).
  (2) exclusiones de asunto para las ofertas al vendedor («haz una
  oferta», «oferta al vendedor», «admite ofertas», «contraoferta»…):
  colaban por la palabra clave SUELTA «oferta» de `subject_keywords` —
  y por eso «Han hecho una oferta al vendedor» tampoco estaba
  realmente descartada, pese a lo que decía este documento. Como la
  lista vive en config.json, `Config.anadir_exclusiones_nuevas` las
  añade al cargar: sin eso, quien ya tenía el archivo no vería nunca
  una exclusión nueva.
  (3) `is_reliable()` rechaza además dos cosas: que el correo mencione
  MÁS DE UN anuncio (`_anuncios_distintos`, por el id `~x########`;
  todos los avisos reales guardados traen exactamente uno) y que los
  tres números NO CUADREN entre sí (`es_coherente`, margen de 3 puntos
  por el redondeo de Todocolección): 40 € → 8,91 € no es un 35 %.
  Y `_ALT_DE_IMAGEN_RE` veta como título el texto ALTERNATIVO de las
  imágenes («Foto número 1 del pedido», «Foto del lote»), que es lo que
  se coló en el toast y en el Historial.
  Los DESCARTADOS guardan ahora el ANUNCIO al que corresponden, no solo
  el asunto: enlace + título, y si el título que saca el parser es el
  asunto genérico se usa `utils.titulo_desde_url` (el slug del propio
  enlace). En estos correos el título vive en una celda suelta, en otra
  rama del HTML que la portada, así que forzar ahí una heurística sería
  frágil — el slug SIEMPRE corresponde al anuncio. Conexión:
  `IMAP4_SSL(..., timeout=30)` (sin él, un socket muerto tras suspender
  colgaba el hilo para siempre), keepalive REAL de Windows vía
  `SIO_KEEPALIVE_VALS` (SO_KEEPALIVE solo = 2 h, inútil), IDLE renovado
  cada 4 min (con 9 min el NAT mataba la conexión en cada ciclo,
  log 2026-07-25), y tras cada reconexión se re-emite "🟢 Monitor
  activo" + limpieza del error (si no, la UI decía "Reconectando…"
  eternamente).
- `utils.py`: cascada de parseo (HTML especializado → semántico → regex
  → heurísticas); `detect_lot` SOLO por el texto del ANUNCIO: asunto +
  título parseado, con evidencia EXPLÍCITA de cantidad ("lote de N",
  "N tomos"). NUNCA contar anuncios del HTML ni el CUERPO del correo
  (trae el carrusel de "recomendados" con sus propios tomos) y NUNCA
  contar rangos romanos ("Anales, libros I-VI" es como la BCG titula UN
  volumen). Volcado real 2026-07-26: con las dos reglas viejas, 23 de
  26 "lotes" eran un tomo suelto y los lotes reales salían con el
  número falseado ("3 TOMOS - POLIBIO" → lote de 39); ciñéndolo al
  anuncio quedó 1 lote real de 300 correos. Títulos pasan por
  `_is_nav_text`/`_JUNK_TITLE_TOKENS` (nada de "center", "App Store",
  "preferencias"...); los enlaces de notificación NUNCA deben apuntar a
  hosts/rutas de imagen ni a tracking/cuenta y DEBEN tener forma de
  anuncio (`_AD_URL_RE`: ~x######## | /lote/ | slug-######) — sin esa
  forma, mejor devolver None que un enlace que no lleva al producto.
  Veto en DOS niveles (bug "diáLOGOs" 2026-07-25: el token blando
  "logo" casaba dentro del slug y vetaba anuncios legítimos):
  `_HARD_BAD_LINK_WORDS` (/api/, idmopen, /mitc/, imágenes...) vetan
  SIEMPRE; los blandos (`_BAD_LINK_WORDS`: logo, redes, baja...) solo
  vetan URLs SIN forma de anuncio.
- `database.py`: history (estados `notificado`/`ignorado`/`lote`/
  `descartado` — este último = correo de TC analizado pero rechazado
  por el filtro de favoritos; el Historial muestra TODO lo analizado),
  processed_uids, price_history (clave = título normalizado; SOLO se
  registran precios de tomos identificados con la colección — títulos
  canónicos de la BD, nunca títulos de anuncios sueltos), thresholds
  (patrón → %, gana el más largo), meta (clave/valor).
- `autostart.py`: clave Run de HKCU para arrancar con Windows. El valor
  se llama `MonitorBCG`; `_VALORES_ANTIGUOS` guarda los nombres previos
  y SIEMPRE se borran al escribir (dos valores = dos copias de la app
  al iniciar sesión). `migrar_nombre()` se llama desde main.py.
- **MOVER EL PROYECTO DE CARPETA (2026-08-13)**: pasó de
  `Desktop\Codigo BCG` a `Desktop\Proyectos\Codigo BCG`. Los DATOS
  aguantan solos —todo cuelga de `app_dir()`, que sale de `__file__`:
  config.json, tc_monitor.db, BDtomos/, y las claves de
  `TextosTomos/_indice.json` y de `textos.db` son NOMBRES de archivo,
  no rutas—. Lo que NO aguanta vive fuera, en el registro de Windows, y
  guarda la ruta ENTERA de main.py: la clave Run y el esquema
  `bcgmonitor:`. Ninguna de las dos se queja al quedarse obsoleta —
  Windows lanza un archivo que ya no existe y `pythonw.exe` muere en
  SILENCIO—, así que el usuario solo lo nota al echar de menos los
  avisos. `deeplink.registrar()` ya se reescribía en cada arranque; el
  arranque con Windows solo se reescribía al ACEPTAR el diálogo de
  Configuración, y ahora `autostart.sincronizar_ruta(config.
  start_with_windows)` lo repasa también en cada arranque (main.py).
  Reglas medidas:
  · **Se compara lo que se LANZA (main.py o el .exe), NUNCA el
  intérprete** (`_objetivo`). En este equipo conviven dos Python y solo
  el registrado (Miniconda) tiene PySide6: comparando el comando
  entero, ejecutar main.py o cualquier herramienta UNA vez desde el
  otro Python repuntaba el arranque a ese otro y la app dejaba de
  arrancar con Windows. Pasó al probar este mismo arreglo.
  · Sí obligan a reescribir: que el objetivo sea otra carpeta, que
  falte `--tray` (nacería abriendo la ventana), que el intérprete
  registrado haya DESAPARECIDO o que la clave lleve un nombre antiguo.
  · `sincronizar_ruta` NUNCA retira el arranque: quitarlo es una acción
  explícita del usuario (`set_enabled(False)`). Si la configuración lo
  pide y la clave falta (copiar el proyecto a otro equipo trae
  config.json pero no el registro), se repone.
  · `is_enabled()` = "hay clave"; `apunta_aqui()` = "y apunta aquí". El
  diagnóstico de arranque lleva ✔/✖ "arranque con Windows" con lo
  segundo, que es lo que de verdad importa.
  · `main.revisar_carpeta()` guarda la carpeta en `meta.ruta_app` y deja
  la mudanza en el log. No repara nada —de eso se encargan las dos
  reescrituras—, pero sin esa línea no hay forma de atar "dejaron de
  llegar avisos" con "moví la carpeta".
  · `tests/test_rutas.py` fija todo esto e incluye un guardián que
  RECHAZA rutas absolutas escritas a mano en app/, tools/, tests/,
  main.py y el .spec (se permiten las de sistema: Program Files,
  Tesseract…). Una ruta a mano volvería a romperse en la siguiente
  mudanza y tampoco fallaría de golpe.
- **ENLACES DE LAS NOTIFICACIONES (`deeplink.py`, 2026-08-02)**: el
  resumen del día se envía con `launch="bcgmonitor://historial"`, así
  que al pulsarlo la aplicación sale de la bandeja y abre el Historial.
  Tres piezas: (1) el esquema `bcgmonitor:` se registra en
  `HKCU\Software\Classes` en CADA arranque (sin admin, como la clave
  Run; reescribirlo mantiene la ruta correcta si el programa se mueve o
  se pasa al .exe); (2) Windows lanza el comando con la URL y ese
  proceso NO abre una segunda copia: entrega el encargo por
  `QLocalServer`/`QLocalSocket` a la instancia viva y se cierra; (3)
  `MainWindow.abrir_seccion` saca la ventana y abre el diálogo con 120
  ms de respiro (el diálogo es modal y se comía el repintado).
  Secciones válidas en `deeplink.SECCIONES`; una desconocida solo trae
  la ventana al frente. OJO al probarlo: emisor y receptor NO pueden
  compartir bucle de eventos (en el mismo proceso la tubería no se
  acepta nunca) — la prueba real necesita dos procesos.
- **IDENTIDAD DE UN TOMO = SU NÚMERO, JAMÁS EL ORDEN (2026-07-29)**:
  tres pares comparten `orden` — 200 Aristóteles/Museo, 250
  Plinio/Basilio, 415 Estrabón/Ovidio ("415[27]" y "415.2"). Marcar uno
  como Obtenido marcaba el otro, y lo mismo con precio objetivo,
  descripción de la IA, publicaciones vigiladas, la ficha que abría el
  doble clic y el ✔ de Textos. `numero` es único (423/423) y estable
  entre reimportes: `Database._numero_de_tomo` resuelve cualquier clave
  (acepta el orden por compatibilidad, y entonces gana el primero),
  `tomo_links` lleva columna `numero` con migración desde `orden`, y en
  la GUI el `UserRole` de las filas guarda el NÚMERO. En los textos
  extraídos la clave es el TÍTULO CANÓNICO (`pdftext.clave_de_tomo` /
  `estado_del_tomo`), también único.
- `collection.py` + tabla `tomos`: colección BCG importada desde
  `BDtomos/titulosBCG.xlsx` (openpyxl; columnas Número|Autor|Obras|
  Páginas|Notas, número tipo "1[2]" → orden=1). `match_tomo`: primero
  número junto a "Gredos/BCG"; si no, puntuación DIFUSA `score_tomo`
  (palabras clave del autor ×3 — SIEMPRE obligatorio —, palabras de la
  obra ×2, ORDEN vía subsecuencia común, semejanza difflib ×6; umbral
  7.0; sin evidencia → None, nunca etiquetar mal; BONUS de VOLUMEN:
  romanos de las Notas presentes en el anuncio suman +2.5 c/u, romanos
  de OTRO volumen restan −2). DIEZ tomos se titulan solo "Obras" o
  "Biblioteca" — `_significant_words` los deja sin palabras y NUNCA
  podían identificarse (Luciano ×4, Ausonio ×2, Prudencio ×2,
  Terencio, Pseudo Apolodoro, bug 2026-07-29): en ese caso basta con
  que el título aparezca tal cual, con el autor ya confirmado. Al
  identificar, la alerta usa
  `tomo.canonical_title()` — ÚNICO por tomo: 197 tomos en 57 grupos
  comparten autor+obras ("Heródoto — Historia" ×5) y
  `annotate_ambiguous` añade sufijo de volumen desde las Notas
  ("· Libros I-II") o "· nº X"; OBLIGATORIO llamar a annotate_ambiguous
  en TODA ruta que construya la lista de Tomos (load_excel ya lo hace;
  monitor y TomoDialog también, vía `tomos_from_rows`) — el canónico va
  también a historial y serie de precios (unifica vendedores SIN
  mezclar volúmenes).
  **VOLÚMENES (revisión completa 2026-07-26)**: el ordinal puede venir
  en las Notas ("Vol. III.") o DENTRO del título ("Tragedias III",
  "(Moralia) II", "Discursos XXXVI-LX") → `split_volume` lo separa y
  `annotate_ambiguous` agrupa por autor + TÍTULO BASE, guardando en
  `Tomo.titulo_base` la parte común (canonical_title usa titulo_base o
  obras). Prioridad de `_volume_hint`: "Vol. N" de Notas → ordinal/rango
  del título → "Libros X-Y" de Notas → primera frase corta → "nº X".
  JAMÁS trocear la nota por el punto a ciegas: "Vol. II." daba "Vol" y
  todo el grupo colisionaba, quedando etiquetado SOLO el primero y el
  resto como "nº 234" (bug visible en Elio Aristides ×5, Plutarco
  Moralia ×13). Hoy: 63 grupos, 215 tomos agrupados, 423 canónicos
  únicos y CERO tomos con sufijo "nº X" — si vuelve a aparecer uno, es
  una regresión. El romano debe llegar a L/C (Dion Casio, "Libros
  L-LX"). `_clean_numero`: openpyxl da floats y la tabla mostraba
  "233.0" — se aplica TAMBIÉN en `tomos_from_rows` (en la BD hay
  números ya guardados como "150.0" y salían así en Textos, 2026-07-28). Series históricas renombradas al canónico nuevo (16 en la BD
  real, copia en tc_monitor.db.bak-canonicos). Etiquetados: columnas `poseido` ("Obtenido") y `deseado`
  ("Deseado") — checkboxes en CollectionDialog, SOBREVIVEN reimportes
  vía re-aplicación por orden; siempre `set_tomo_flag`/`flag_count`
  con lista blanca de columnas. El toast añade "· ya obtenido ✔",
  "· ⭐ DESEADO" o "· ¡TE FALTA!". El texto de la GUI dice "obtenidos",
  nunca "en tu poder".
  Visor: CollectionDialog (auto-importa si la tabla está vacía) con
  filtro "Ver" (Todos/Obtenidos/Deseados/Me faltan/Raros), casillas
  Obtenido/Deseado ORDENABLES por clic en su cabecera (_NumItem con
  _value 0/1 actualizado al marcar) y doble clic en la fila →
  `TomoDialog` (ficha: datos, editor del `precio_objetivo` — la
  columna "Precio máx." se retiró de la tabla a petición del usuario
  2026-07-26 —, PUBLICACIONES VIGILADAS (tabla `tomo_links`: URLs que
  el usuario añade; `utils.fetch_listing_price` extrae el precio con
  cascada itemprop→og→JSON→texto y alimenta price_history SOLO si el
  precio cambió), gráfica del tomo, últimas alertas, búsquedas
  externas). price_history lleva columna `url`: cada punto de las
  gráficas es CLICABLE y abre su publicación (cursor de mano; radio de
  clic 8 px, ceñido al punto — 14 se sentía desplazado); los puntos de
  alertas guardan alert.link. REGLAS de la serie (2026-07-26): SOLO
  tomos identificados (título canónico) y SOLO anuncios de UN tomo —
  lotes y multi-tomo JAMÁS entran (contaminan la gráfica); punto nuevo
  solo si difiere del último de la serie. **VARIOS VOLÚMENES SIN DECIR
  CUÁNTOS (2026-08-02)**: `utils.varios_volumenes` cierra el hueco que
  dejaba `detect_lot` — hay anuncios que no dicen "N tomos" pero los
  ENUMERAN ("DIÁLOGOS TOMO I, II Y III", "ANALES - LIBROS I-IV +
  XI-XVI", "DISCURSOS I-V Y VI-XII") y su precio es el del CONJUNTO:
  330,65 € acabaron en la gráfica de "Platón — Diálogos I". Cuenta
  GRUPOS de volumen (un rango, "I-VI", es UN grupo): tres o más grupos,
  o dos ENUMERADOS (unidos por y/,/+), es multi-tomo. Ojo con
  "Historias I. Libros XIV-XIX", que es UN tomo (su volumen y los
  libros que trae): dos grupos NO enumerados. El aviso se notifica
  igual; lo que no entra es el precio ni la publicación vigilada.
  ListingPriceFetcher lleva nº
  de secuencia: loadFinished dispara VARIAS veces por página y sin la
  guarda se cruzaban capturas entre peticiones (zigzag de duplicados).
- **Filtro de precio en Precios y Lotes (2026-08-01)**: la segunda
  columna es el ÚLTIMO precio de cada serie (antes era el nº de puntos,
  que pasó al tooltip) y el combo «Precio» ordena por él — «Precio
  mayor» / «Precio menor» / «Sin ordenar» (el de la base: lo más
  reciente arriba). El dato sale de
  `Database.price_history_stats(lotes=)`: UNA consulta con los
  agregados de todas las series, en vez de pedir la serie entera de
  cada título (107 consultas). Sin criterio hay que RETIRAR el
  indicador de la cabecera (`setSortIndicator(-1, …)`) o la tabla se
  reordena sola por la última columna pulsada.
- **LOTES** (botón propio en MainWindow, 2026-07-26): `LotesDialog`
  HEREDA de `PriceHistoryDialog` (hooks `_TITLE`/`_EMPTY_TEXT`/
  `_HINT_TEXT`/`_series()`); sus series viven en `price_history` con
  clave prefijo `lote::` (`Database.LOT_PREFIX`) — `price_history_titles`
  las EXCLUYE (NOT LIKE) y `lot_price_titles` las lista; JAMÁS mezclar
  ambos espacios. Tabla `lotes` = publicaciones vigiladas (titulo, url,
  ultimo_precio). "Añadir lote": `shared_price_fetcher().fetch(url, cb,
  want_html=True)` → `utils.extract_listing_text` (SOLO fuentes del
  anuncio: og:, JSON-LD @type Product — en TC trae la descripción
  íntegra del vendedor —, h1, id="descripcion"; el texto COMPLETO de la
  página solo como último recurso: el carrusel de "relacionados" dio 19
  tomos falsos en la prueba real) → `collection.match_tomos_multi`
  (segmentos por línea/;/·/|, nunca coma; exige evidencia autor+obra
  por segmento) → nombre `[LOTE ×n] canónico + canónico...`. Sin
  reconocimiento → mensaje en ROJO `#c0392b` (el rojo del icono de
  bandeja; no inventar otro) sugiriendo «Editar títulos»: botón que se
  ACTIVA al seleccionar lote y abre `TomoPickerDialog` — arriba la
  tabla «En el lote» con los títulos YA registrados (se actualiza en
  vivo al marcar; doble clic saca un título), abajo la colección con
  buscador único por nº/autor/obra y casillas; selección capturada en
  accept() (regla WA_DeleteOnClose). La preselección sale de los
  canónicos del nombre del lote y, si no los lleva (lotes del monitor,
  con el asunto crudo), de `match_tomos_multi` sobre ese texto — nunca
  abrir el selector en blanco. Al aceptar, `rename_lot` migra serie y
  vigilados a la clave nueva. "Quitar" borra vigilancia + serie
  (permitido: acción explícita del usuario; la regla de conservar el
  histórico íntegro protege las series de TOMOS). El monitor registra
  cada lote detectado en su serie `lote::` (dedup vs `last_lot_price`);
  siembra única `lotes_seed_v1` en `_create_tables` desde history
  estado='lote'. Helpers: `collection.tomos_from_rows` (SIEMPRE con
  annotate_ambiguous) y `gui._titulo_desde_url` (respaldo por slug).
- **PUBLICACIONES VENDIDAS (2026-07-31)**: un anuncio vendido CONSERVA
  su precio en la página, así que sin mirarlo la vigilancia seguiría
  apuntando un precio que ya no se puede pagar. «Actualizar precios»
  (ficha del tomo y ventana de Lotes) pide el HTML (`want_html=True`) y
  pasa por `utils.listing_sold`. MANDA EL METADATO de disponibilidad:
  los tres sitios vigilados usan el vocabulario de schema.org y lo
  publican (barrido de las 220 publicaciones reales, 2026-07-31:
  Wallapop 49/50, IberLibro 19/19 — con `itemprop="availability"
  href=` —, Todocolección 131/151 en JSON-LD; las 20 de TC sin metadato
  y 1 de Wallapop que no cargó). Si dice InStock, NO está vendido
  aunque la página mencione ventas — así el carrusel de «también te
  puede interesar», lleno de anuncios vendidos AJENOS, no retira un
  precio bueno. Sin metadato deciden FRASES ANCLADAS ceñidas al texto
  del anuncio («artículo vendido», «este ejemplar ya se ha vendido»,
  «ya no está disponible», «subasta finalizada»): JAMÁS el substring
  suelto "vendido", que "vendedor" lo contiene. Una página que devuelve
  0 caracteres NO es una venta (puede ser la red) y no se toca nada. Al
  detectarlo: `mark_link_sold` (columna `vendido` en `tomo_links` y
  `lotes`, precio a NULL) + `delete_price_points(clave, url)`, que
  retira SOLO los puntos de esa URL — la serie del tomo conserva los de
  otros vendedores. Comprobado contra los 4 anuncios reales del
  usuario: ninguno falso positivo.
- **Quitar un lote** borra la serie y TODAS las publicaciones con ese
  título: el aviso dice cuántas son y cada baja queda en el log
  (se perdieron lotes registrados y no había ni rastro, 2026-07-31).
- **IDEMPOTENCIA POR CORREO (2026-07-26)**: el UID evita reprocesar; el
  `Message-ID` (`utils.message_key`, con huella sha1 de respaldo) evita
  REINSERTAR. Columna `mensaje_id` en `history` y `price_history`:
  `add_history` no repite fila con el mismo (mensaje_id, estado) y
  `add_price_point`/`add_lot_price_point` no repiten punto en la misma
  serie; `tomo_links`/`lotes` deduplican por `clean_ad_url`. Los puntos
  ANTERIORES a la columna se ADOPTAN (mismo precio+URL sin mensaje_id →
  se les asigna el id) para que un volcado del histórico no duplique
  gráficas ya guardadas. `email_inserted_status(msg_id)` da el recuento
  por tabla (historial/precios/lotes/enlaces/lotes_vigilados) y
  `email_already_inserted` resume si ya se analizó. En el monitor, un
  correo con fila de historial previa es un RE-ANÁLISIS: se recalcula
  e inserta lo que falte pero NO se vuelve a notificar (si no, volcar
  el backlog dispararía cientos de toasts repetidos). Los índices de
  estas columnas se crean DESPUÉS del ALTER TABLE (dentro del script de
  creación reventaban el arranque en una BD antigua).
  Estado real de la BD tras analizar los 300 correos más recientes
  (2026-07-26): 303 filas de historial, 106 series de precios (262
  puntos), 206 publicaciones vigiladas en 105 tomos y 1 lote. NO crear
  herramientas de volcado masivo: el usuario las rechaza — para
  reprocesar, se marcan los correos como NO LEÍDOS y el propio monitor
  los repasa (`_recheck_marked_unread`, 50 por ciclo).
- **GUARDADO AUTOMÁTICO desde los correos (2026-07-26)**: toda oferta
  que el monitor IDENTIFICA con la colección guarda sola su
  publicación en la ficha del tomo (`add_tomo_link_if_new`, Colección →
  doble clic) además del punto en la serie (Precios); todo lote
  detectado queda vigilado en la pestaña Lotes (`add_lote_if_new`) con
  enlace y precio. Dedup por `utils.clean_ad_url` (URL sin query: los
  avisos traen cola utm_*, y sin limpiarla cada correo del MISMO
  anuncio añadía una fila nueva); si ya existía, solo se refresca el
  precio. Se guarda la URL LIMPIA.
- **DESCRIPCIONES CON IA Y BUSCADOR DE LA COLECCIÓN: RETIRADOS DE LA
  INTERFAZ (2026-08-05)**. El usuario no tiene clave de OpenAI y pidió
  quitar las dos partes. Ya NO existen: el botón «Describir» de
  Colección, `_DescribeWorker`, `SearchDialog` y la línea «Buscar en la
  colección» de la ventana principal. Para encontrar un tomo por autor
  o título está el filtro «Buscar:» de la propia ventana de Colección;
  para buscar dentro del libro, `BuscarTextosDialog`. NO reponerlos sin
  que el usuario lo pida.
  También se retiró (mismo día) el campo «Clave de OpenAI» y el de
  «Modelo» de Configuración: sin «Describir» no quedaba nada que usara
  la API, y un campo que no hace nada es peor que ninguno. Volver a
  usarla exige reponer ese campo.
  Sigue en pie la CAPA DE DATOS, por si algún día vuelve: `app/ai.py`
  (HTTPS con `urllib`, sin SDK, para no tocar el empaquetado; el prompt
  va ANCLADO a los datos reales y `parse_response` valida SIEMPRE antes
  de guardar), las columnas `descripcion`/`temas`/`desc_modelo`/
  `desc_fecha` de `tomos` —que `replace_tomos` RE-APLICA tras reimportar
  el Excel—, `Database.buscar_tomos` (todas las palabras, peso por
  campo; con 423 filas un barrido normal es instantáneo: NO meter FTS5
  aquí) y la clave cifrada con DPAPI (`openai_api_key_dpapi`).
  `TomoDialog` sigue MOSTRANDO la descripción si la hay. Todo eso está
  hoy sin usar desde la interfaz.
- **TEXTO DE LOS TOMOS (`app/pdftext.py` + `TextosDialog`, 2026-07-28)**:
  el PDF de cada tomo se analiza pero NO se guarda ni se copia — solo su
  texto (`BDtomos/TextosTomos/NNN - Autor - Obra.jsonl`, ~10× más
  ligero), para poder borrar el PDF después. JAMÁS escribir un .txt
  corrido: sin el número de página no hay cita posible, que es el
  objetivo de todo esto. Una línea por página con página de PDF, página
  IMPRESA, sección, cuerpo y notas aparte. Flujo: lista de los 423 con
  ✔ de seguimiento → doble clic → elegir PDF → análisis en `QThread` →
  aviso con las dificultades y dos botones («Guardar así» / «Probar con
  otro PDF», que reabre el selector). Medido en PDF reales: el
  emparejado va por `match_tomo` sobre el nombre limpio — los números
  del nombre son AJENOS a la BCG (la Ilíada venía como "470" y es el
  tomo 150); las notas al pie se separan por tamaño de letra RELATIVO
  al documento (9 pt es cuerpo en un tomo y nota en otro); 1 de cada 6
  PDF es un escaneo sin texto (se detecta, se avisa y no se guarda);
  un tercio no trae índice interno (secciones deducidas de los títulos
  impresos); el desfase entre página de PDF e impresa es constante y se
  lee de los márgenes.
  **ÍNDICE DE NOMBRES** (la concordancia del traductor, lo que permite
  responder "¿qué tomo habla de los lacedemonios?" SIN IA): tres cosas
  medidas en PDF reales que hay que respetar. (1) Se cita de DOS
  maneras: por página ("Aquiles, 12, 45-47") o por CANTO y verso
  ("Amazonas, III 189; VI 186") — exigir números sueltos sacaba 1
  entrada de 1.061 en la Ilíada. (2) En algunos tomos el índice va en
  letra MENOR que el texto y el separador de notas al pie se lo lleva
  entero: hay que mirar cuerpo Y notas (Jenofonte, 12,9 pt frente a
  15,4 → de 0 a 244 entradas). (3) Debe ANCLARSE en el rótulo "ÍNDICE
  DE NOMBRES": la bibliografía tiene la misma forma (autor, coma,
  números) y colaba entradas falsas en tomos que ni siquiera llevan
  índice; además, los marcadores del PDF MIENTEN sobre dónde empieza
  (en el Plutarco, doce páginas antes). Los números altos sin romano
  delante se descartan: son años de edición, no páginas. (4) Hay dos
  ESTILOS de índice: el de referencias pegadas al nombre y el
  DESCRIPTIVO, que explica cada nombre y pone la localización al final
  («Ábaris: guerrero de Fineo, muerto por / Perseo, V 86») y puede
  partirse en dos líneas — `_ENTRADA_DESC` + margen de dos líneas de
  «hambre» antes de cerrar la entrada; sin eso, Ovidio daba 1 entrada
  de 1.478 y Luciano 0 de 620.
  (5) TRES estilos de cita, y el índice los usa: por página, por canto
  y verso, y por LIBRO, capítulo y párrafo — "Sábata XVI, 4,2", sin
  coma tras el nombre (Estrabón, 2026-07-31): `_ENTRADA_LIBRO` se
  prueba ANTES que las demás (si no, la regla clásica se lleva el
  romano dentro del nombre) y sus referencias se leen enteras
  ("XVI 4, 2"). Un romano suelto NUNCA es un nombre: el OCR parte
  "XVI, 4, 14" en "XV I,4, 14" y creaba entradas fantasma. (6) En los
  escaneos los RÓTULOS van con las letras espaciadas
  ("Í N D I C E  D E  T O P Ó N I M O S"): `_tipo_seccion` y el ancla
  del índice se prueban también SIN espacios, y la lista de rótulos
  incluye topónimos, étnicos, lugares, geográfico y onomástico — no
  solo "índice de nombres" (así el Estrabón pasó de 0 a 1.150 entradas).
  **OCR PARCIAL (2026-07-28)**: un PDF puede traer texto en casi todas
  sus páginas y faltarle unas pocas — y suelen ser las que importan (en
  el Plutarco, 2 de las 17 ilegibles eran justo las de su índice de
  nombres). Si la cobertura llega al `UMBRAL_OCR_PARCIAL` (75 %), la
  ventana ofrece reconocer SOLO esas páginas (`completar_con_ocr`) y
  rehace el índice; por debajo, recomienda buscar otra copia en vez de
  reconocer medio tomo. Tesseract NO se empaqueta (son ~50 MB de un
  programa aparte para un uso ocasional): se busca en el PATH y en las
  rutas típicas de Windows, y si no está se explica cómo instalarlo
  —marcando el idioma «Spanish»— o se indica su ruta en Configuración
  (`tesseract_path`). Sin Tesseract todo lo demás funciona igual.
  **Trampas del OCR (medidas el 2026-07-28 con la Ilíada)**: (1)
  Tesseract se instala por defecto SOLO con inglés — pedirle "spa" sin
  `spa.traineddata` falla en TODAS las páginas ("Tesseract language
  initialisation failed"); hay que mirar `idiomas_ocr()`, avisarlo en
  el propio ofrecimiento, caer a "eng" y anotarlo como dificultad; un
  fallo de idioma CORTA el trabajo (no es cosa de esa página). (2) el
  resultado se marca en `Analisis.ocr_intentado`, JAMÁS en
  `paginas_ocr`: un reconocimiento que no rescata nada dejaba la lista
  vacía y la ventana volvía a ofrecer lo mismo en bucle. (3) Si no sale
  texto hay que DECIR por qué (`ocr_fallo`) — las páginas mudas suelen
  ser láminas, mapas o cubiertas y no se pierde nada del tomo.
  **EDICIONES DIGITALES (EPUB→PDF; Aristófanes I y II, 2026-07-29)**:
  varios tomos circulan como libro electrónico convertido — hoja A4
  uniforme, SIN folio impreso, con un pie «Página N» (N = hoja del PDF)
  en todas. `detecta_formato` lo marca (`Analisis.formato = "ebook"`) y
  eso cambia CUATRO cosas: (1) `_sin_marcador` quita el pie — si no,
  entraba en el texto de las 859 hojas; (2) NO se calcula desfase de
  folio: las cifras del margen son VERSOS y daban «página impresa =
  PDF más 949»; (3) las cifras sueltas del margen lateral se guardan
  TODAS en `registro["versos"]` — antes caían enteras en las notas al
  pie por ser de letra menor. Son LA referencia de cita: versos en los
  poetas, parágrafos en la prosa (Isócrates, Jenofonte), y por eso se
  guarda la lista entera y no solo el mínimo y el máximo. Auditado
  2026-07-29 sobre los 7 tomos de `Libros/`: 99,2-100 % del texto del
  PDF recuperado y CERO líneas no numéricas perdidas — lo que queda
  fuera de `cuerpo`/`notas` son esas cifras y los encabezados; (4) las hojas cortas son NORMALES (portadilla de cada
  obra, rótulo «Notas», cada nota final suelta): una hoja solo cuenta
  como escaneada si tiene una IMAGEN que cubra >40 % (`_es_hoja_
  escaneada`) — con el criterio viejo (<100 caracteres) salían 393
  «sin reconocer» de un tomo íntegro y la app recomendaba buscar otra
  copia. `muestra()` toma las hojas con MÁS texto (el cuarto del libro
  podía caer en las notas finales y el tomo se etiquetaba «ocr bueno»).
  Un volumen trae VARIAS obras: `obras_del_indice` (nivel 1 del índice
  interno) da `registro["obra"]` cuando no hay encabezado impreso, la
  fusión de secciones seguidas ya no se traga el comienzo de una obra
  nueva, y en notas/índices/bibliografía se deja `obra` VACÍA (heredaba
  la comedia anterior). `paginas_rescatables` excluye cubierta y
  láminas del final (±3 hojas): no son texto perdido y no se ofrece
  reconocerlas. Estas ediciones no traen índice de nombres — se dice
  así, para no mandar al usuario a buscar otra copia por eso.
  Variantes del pie medidas: «Página N», «www.lectulandia.com -
  Página N» y NINGUNO (Ovidio, Luciano) — sin pie, el formato se
  reconoce por la cola de notas a hoja por nota SIN un solo folio
  impreso (`cola_de_notas` + `_hay_folios`). La cola se detecta por el
  TEXTO («[65] Deuteronomio 32, 9.»), no por el índice interno: muchos
  volúmenes no traen entrada para ella.
  **HOJA ≠ PÁGINA**: en estas ediciones las notas van a hoja POR NOTA
  (1.072 de las 1.436 de Aristófanes II, cuyo tomo en papel tiene 528):
  el informe dice «Hojas del PDF: 1.436 (364 de texto y 1.072 de notas
  finales)» + las páginas reales del Excel, nunca «Páginas: 1.436».
  `hojas_de_texto` corta donde empieza la cola de notas/índice.
  **SECCIONES**: con índice interno se respetan TODAS sus entradas —
  fundir las seguidas del mismo tipo metía el texto de cada comedia
  bajo el rótulo de la bibliografía anterior («LAS NUBES» quedaba
  dentro de «Ediciones, traducciones, comentarios»); la fusión solo se
  aplica a las secciones DEDUCIDAS de los títulos impresos, que salen a
  docenas. Dos entradas en la misma hoja no pueden dejar el rango del
  revés (`hasta = max(desde, pag-1)`).
  **RENDIMIENTO (2026-07-29)**: `get_text("dict")` DECODIFICA la imagen
  de cada hoja — en un tomo escaneado son 57 ms por página. Se pide
  siempre con `TEXTFLAGS_DICT & ~TEXT_PRESERVE_IMAGES` (texto idéntico,
  31× más rápido) y las hojas que son solo imagen se detectan con
  `get_image_info`, que no descodifica. El texto plano se cachea por
  hoja (`_Lector.texto`). Moralia X pasó de 44,7 s a 2,6 s.
  **NOTAS AL PIE, hoja a hoja**: el umbral de "letra menor" se calcula
  POR PÁGINA (`_umbral_de_nota`), no una vez para todo el tomo: en los
  escaneos la escala cambia entre páginas y el umbral global mandaba a
  las notas el 87 % del texto (Moralia X: 19.907 palabras de las
  146.494 reales). Si el cuerpo pequeño ocupa más de un tercio de la
  hoja, no se separa nada.
  **MÉTODO PARA EL FORMATO DOMINANTE (auditoría del corpus,
  2026-07-29)**: de los 132 tomos extraídos, 104 son ediciones
  digitales. Tres consecuencias medidas y ya resueltas: (1) las
  REFERENCIAS del margen —verso/parágrafo ("805"), página de Estéfano
  en Platón y Juliano ("229D"), de Bekker en Aristóteles ("1094a")— las
  suelta el conversor como líneas al principio de la hoja, sin margen
  que mirar: `_saca_referencias` se queda con esa cabecera y van a
  `registro["versos"]` como TEXTO (pueden llevar letra); (2) la llamada
  de nota va pegada a la palabra ("escandalizarse[61] según") y partía
  la frase: se guarda en `llamadas` y el cuerpo queda limpio; (3) cada
  nota final ocupa su hoja y ahora lleva su número en `nota` (65.404 en
  el corpus), sin el "<<" de vuelta al texto. El RENGLÓN no se toca —
  en los poetas es el verso y es la unidad de cita—, así que buscar
  frases largas (el renglón medio son 64 caracteres) se hace sobre
  `texto_para_buscar(registro)`, que aplana cuerpo y notas. Regla
  general: el texto guardado es fiel al tomo; quien normaliza es la
  búsqueda, no el guardado.
  **LA LISTA NO ABRE LOS TEXTOS (2026-07-31)**: los .jsonl son el
  ALMACÉN para el análisis posterior (108 MB en 132 archivos), no algo
  que la interfaz deba cargar. Abrirlos uno a uno para pintar el ✔
  colgaba la ventana de Textos varios segundos (Windows inspecciona
  cada archivo al abrirlo). `estado_de_los_tomos` se apoya en
  `TextosTomos/_indice.json` (121 KB) con lo poco que la lista enseña
  —canónico, hojas, palabras, calidad, dificultades— y solo relee la
  cabecera de los archivos nuevos o cuyo tamaño/fecha haya cambiado:
  0,07 s la primera vez, 0,02 s después. Si el índice falta o está
  roto, se reconstruye solo.
  **TEXTO LATINO LEÍDO CON LA TABLA DEL GRIEGO (2026-08-05)**: 50 de
  los 172 tomos traían las vocales acentuadas convertidas en letras
  griegas — «compaρeros», «mαs», «tambiιn», «caballerνa», «HIERΣN».
  NO es el OCR leyendo mal: son bytes latinos descodificados con
  Windows-1253, y se comprobó carácter a carácter (α=á, ι=é, ν=í, σ=ó,
  ρ=ñ, Σ=Ó, Ν=Í: 7 de 7), así que `repara_mojibake` es EXACTA, no una
  adivinanza — se genera desde las dos tablas de códigos, nunca a mano.
  Excepción medida aparte: la «ú» salía como el dígito árabe ٥ (370
  casos), que viene de la fuente del PDF y no de la tabla. REGLA que no
  se puede romper: se repara PALABRA A PALABRA y solo si la palabra ya
  tiene alguna letra latina — una palabra griega auténtica no lleva
  ninguna, y así quedan intactos el aparato crítico, las
  transcripciones con macron (phýsis, gnōsis), el alemán, el francés,
  el danés y el checo de las bibliografías. `limpia_invisibles` quita
  además el espacio duro (venía entre TODAS las palabras de algunos
  tomos: de ahí el texto desparramado) y el guion opcional, que solo
  une la palabra si al otro lado sigue una LETRA («qui¬\n5 sieran»
  daba «qui5 sieran»). Ambas van en `revisar_textos`; tras aplicarlas
  hay que REINDEXAR el RAG.
  **LOS DOS ALFABETOS REVUELTOS (`repara_alfabetos`, 2026-08-09)**: la
  regla de arriba («tiene alguna letra latina») no basta en una
  colección grecorromana, porque una palabra mezclada puede ser CUATRO
  cosas y cada una pide lo contrario. Medido sobre los 210 tomos: 8.706
  palabras mezclan los dos alfabetos, y ninguna regla suelta las
  distingue.
  (1) **ETIQUETA GEOMÉTRICA** (8.339, casi todas de Euclides). Los
  Elementos nombran los puntos con letras griegas y el PDF puso latinas
  donde la mayúscula se ve idéntica: «ABΓ» por ΑΒΓ, «el cuadrado ΣN»
  por ΣΝ. Que Σ (griega) y N (latina) convivan DENTRO de la etiqueta es
  la prueba de que la etiqueta es griega. → todo a griego.
  (2) **CANTO HOMÉRICO** (18, Apolonio Díscolo): los griegos numeran
  los cantos de la Ilíada con las 24 letras, así que «(Ι 649)» es el
  canto IX, verso 649. → NO se toca.
  (3) **NUMERAL ROMANO** (12): la MISMA iota, pero en un rótulo español
  («ΙII. contenido y estilo», «Ι-ΙΙ-ΙΙΙ LOS OLINTÍACOS»). → a latín.
  (4) **MOJIBAKE cp1253**: «INTRODUCCIΣN». → a español.
  Lo que separa 1-2-3 de 4 es si TODA letra latina de la palabra tiene
  gemela griega (INTRODUCCIΣN trae D, R, U, C, que no la tienen); lo que
  separa 2 de 3 es la LÍNEA (si alrededor manda el griego, la letra es
  griega). Dos guardas más, ambas puestas por daño MEDIDO:
  · **Manda quien tenga más letras DENTRO de la palabra.** Sin eso, las
  palabras griegas que ya traían intrusas latinas minúsculas —«Πρòς»
  (ò latina), «κομίζoí»— entraban por la rama española y les cambiaba
  los acentos griegos: «Πñòς», «κομíζoí». Estropear griego bueno para
  arreglar español que no estaba roto.
  · **Las vocales griegas con tono van por PARECIDO, no por la tabla**:
  cp1253 dice ό→ü, y «Actόrida» es «Actórida», no «Actürida». Se
  respeta la ü en güe/güi, que es la única del español.
  Y para las etiquetas hechas SOLO de gemelas latinas («AB», «EK»), que
  no llevan nada dentro que las delate, la regla se enciende POR TOMO
  (`cuenta_etiquetas_de_figura` ≥ 50 → tomo matemático; solo lo son los
  tres Euclides), con tres filtros medidos: 2-4 letras —fuera HEATH y
  MAZON, los editores de Euclides y de la Ilíada, que salen en la
  bibliografía—, todas DISTINTAS —los vértices de una figura no se
  repiten: fuera TITO y HAHN— y que no sea numeral romano —fuera II,
  XII, XXIII—. Límite conocido y aceptado: dentro de un tomo
  matemático, «NO» se lee como dos puntos, no como el noroeste.
  Aplicado al corpus el 2026-08-09: 51 tomos, 14.413 palabras; segunda
  pasada = 0 cambios (es idempotente). Copia previa en
  `TextosTomos.bak-alfabetos`. Tras esto hay que REINDEXAR (se hizo: 51
  tomos, 76.784 pasajes) — y entonces «αβγ» en minúsculas y sin acentos
  encuentra los tres Euclides, que antes no aparecían.
  **REVISIÓN DE LO YA GUARDADO (`revisar_textos`, botón «Revisar» de
  Textos, 2026-07-29)**: el analizador mejora con cada tomo raro, y los
  textos viejos se quedan con los defectos de su día. Casi todo se
  rehace del PROPIO texto guardado, sin el PDF (que el usuario borra):
  reclasificar secciones con las reglas de hoy, recalcular hojas de
  texto y palabras, devolver al cuerpo las hojas cuyas «notas» pesan
  más del doble que él (repara el umbral global de letra menor) y
  volver a leer el índice de nombres — solo se cambia si salen MÁS.
  Medido sobre los 132 tomos ya extraídos: Moralia X 19.916 → 151.530
  palabras, Sófocles 83.373 → 159.570, Juliano 305 → 450 nombres,
  Vidas paralelas I 5 → 148, Ilíada 1.061 → 1.069. Lo que NO se puede
  arreglar sin el PDF se dice («conviene volver a analizar»). Copia de
  seguridad antes de la primera pasada: `TextosTomos.bak-revision`.
  **Progreso y cancelación**: `analizar(..., progreso, cancelado)`
  informa por fases (`Extrayendo el texto`, hechas/total) para la
  `GlowProgress` de la ventana y se corta desde `aviso()`, que lanza
  `AnalisisCancelado`; cerrar `TextosDialog` cancela y espera el hilo
  (si no para en 3 s, se le quita el padre y se guarda en
  `_HILOS_SUELTOS`: destruir un QThread vivo tumba la app).
- **BUSCADOR DENTRO DE LOS TOMOS (`app/rag.py` + `BuscarTextosDialog`,
  2026-08-04)**: tareas RAG-1/2/3 de `docs/PLAN_RAG.md`. Índice FTS5
  (BM25) sobre los `.jsonl` en `BDtomos/textos.db` — archivo APARTE de
  `tc_monitor.db`, derivado y reconstruible: borrarlo no pierde nada, y
  los `.jsonl` NUNCA se tocan (solo se leen). 172 tomos → 240.249
  pasajes, 30.793 nombres, 250 MB, 42 s de construcción, 4-25 ms por
  consulta. TODO local: sin clave de API, sin red. Reglas medidas que
  no se pueden romper:
  · **Cada pasaje guarda su localización** (tomo, obra, sección, hoja,
  página impresa, verso). Sin eso no hay CITA, que es el objetivo —
  misma regla que prohíbe el .txt corrido en `pdftext`. La cita
  prefiere SIEMPRE la página impresa; en las ediciones digitales sin
  folio, el verso/parágrafo; «hoja N del PDF» solo como último recurso.
  · **Las notas van en pasajes aparte** (`clase='notas'`): son 66.781
  hojas de 101.402 y taparían el texto del autor. Se pueden excluir.
  · **Peso por longitud** (`_PESO_LARGO`): BM25 divide por el tamaño
  del pasaje, así que las hojas de RÓTULO ganaban — los 4 primeros
  resultados de «Aquiles» eran rótulos de dos palabras. No borrarlas,
  hundirlas.
  · **Palabras vacías fuera** de las consultas SUELTAS, jamás de lo que
  va entre comillas («el alma es inmortal» premiaba pasajes largos
  llenos de artículos).
  · **La gente PREGUNTA**: si `es_pregunta()`, se cae también el
  ANDAMIO (tomo, habla, dice, aparece…) — «¿qué tomo habla de los
  lacedemonios?» daba CERO; fuera de una pregunta esas palabras son
  texto del tomo («el libro de los muertos») y se respetan. Y si aún así
  no hay pasaje con todas, se sueltan las COMUNES y se exigen las RARAS
  (`_por_rareza`), avisándolo en la ventana. PROHIBIDO el respaldo con
  OR: manda BM25 y gana quien repite mucho «tomó» sin un lacedemonio.
  · **RESULTADOS EN DOS NIVELES (2026-08-05)**: la lista de
  `BuscarTextosDialog` son TODOS los tomos donde aparece lo buscado
  (`tomos_con`, un GROUP BY sin tope), y el doble clic abre los pasajes
  de ESE tomo en `PasajesDeTomoDialog`. Pidiendo pasajes sueltos había
  que cortar por los 400 mejores de BM25 y los tomos que caían fuera NO
  APARECÍAN NUNCA: con «lacedemonios» (105 tomos, 2.143 pasajes)
  faltaban las obras menores de Jenofonte. Se probó a desplegarlos
  DENTRO de la misma tabla y se descartó: la localización y el pasaje
  quedaban cortados en una línea y no se podía leer nada. La ventana
  del tomo lleva la lista arriba y la PÁGINA ENTERA abajo, y basta con
  SELECCIONAR para leerla (pedir doble clic por pasaje era un clic de
  más). NO hay «Ver a solas» ni doble clic que abra otra ventana: se
  quitaron el 2026-08-08 por innecesarios, y con ellos `PasajeDialog`.
  Cada lista lleva su rótulo («ÍNDICE DE NOMBRES DEL TRADUCTOR» y «EN
  EL TEXTO DE LOS TOMOS»): eran dos tablas sin título y no se sabía
  cuál era cuál. `UserRole` de la col. 0 lleva `("tomo", canónico)`.
  · **PLIEGO DE DOS PÁGINAS** (2026-08-08, a petición del usuario): el
  lector no es un cuadro sino DOS, como un libro abierto. Se enseñan
  SIEMPRE las dos aunque el pasaje esté solo en una; si del otro lado
  no hay nada, esa página va en blanco. Lo buscado se resalta SOLO en
  la página donde está. El mínimo de la ventana (980 px) está puesto
  para el pliego: más estrecho, cada página queda en una columna que se
  lee peor que una sola.
  El lado lo decide `_donde_cae()`, NO la paridad par/impar del libro
  real: si la coincidencia está en el primer tercio de su página, el
  pliego se abre por la ANTERIOR (la página va a la derecha) para no
  leer sin saber de qué venía; si cae más abajo, ya trae su contexto
  delante y se empareja con la siguiente. Se probó primero con la
  paridad y el usuario pidió esto: perder el contexto se nota al leer,
  y que la página caiga a un lado o al otro, no. `_asomar()` deja
  además la coincidencia a un tercio de la altura, nunca pegada arriba.
  · **REALCE DE LO BUSCADO** (2026-08-08): cuerpo un pelín mayor
  (`_CUERPO_MARCA` 11,7 frente a `_CUERPO_PAGINA` 11,5), oro casi
  blanco (`ORO_MARCA`) y negrita. El salto es PEQUEÑO a propósito: con
  12,5 la letra alta se salía de la línea y Qt CORTABA LAS TILDES por
  arriba; por eso además el párrafo va a `line-height:175%`.
  · **UN SOLO `font-size` por trozo** (`estilo()`): antes se apilaban
  el del griego y el del realce y ganaba el último, así que una
  coincidencia GRIEGA perdía la compensación de Palatino, se salía de
  la línea y se comía los acentos. El cuerpo se calcula una vez:
  `(marca ? _CUERPO_MARCA : _CUERPO_PAGINA) × (griego ? _ESCALA_GRIEGA : 1)`.
  · **HALO CON ESTRELLAS, PINTADO ENCIMA** (`VisorDePagina`): sobre
  cada coincidencia respira un halo dorado (3 s) y por él cruzan
  pequeñas ESTRELLAS de cuatro puntas que nacen y se apagan, como los
  destellos del pan de oro de una encuadernación. Se pinta en
  `paintEvent` sobre el viewport, en modo `CompositionMode_Plus` (suma
  de luz, no pintura encima), y el DOCUMENTO NO SE TOCA. Antes se
  probaron dos cosas que el usuario descartó (2026-08-08): un foco que
  recorría las letras y un filete subrayado. Pintar encima además
  permite dibujar lo que el texto enriquecido no sabe hacer.
  Guardas: las posiciones se calculan UNA vez al poner el texto (sobre
  `toPlainText`, que va carácter a carácter con lo que se ve); los
  rectángulos salen de `cursorRect` y se descartan los que quedan fuera
  de la parte visible o partidos entre dos renglones; solo se repinta
  el trocito de cada coincidencia (`viewport().update(rect)`), nunca la
  página; el temporizador se para en `hideEvent`; con más de
  `_TOPE_MARCAS` (40) coincidencias no se anima. Cuesta 0,6 ms por
  fotograma.
  Las estrellas se siembran en `_sembrar_estrellas` y su sitio lo
  sortea `_sitio()`: el 80 % nace en los BORDES de lo resaltado, donde
  enmarcan la palabra sin taparle las letras. El azar se echa SOLO al
  nacer cada estrella, nunca en cada fotograma — sorteándolo por
  fotograma no brillarían, temblarían.
  · **EL PASAJE DEL DÍA CIERRA EN PUNTO Y APARTE**
  (`_acaba_en_parrafo`, 2026-08-08): más exigente que la regla del
  pliego, porque ahí solo se enseña UNA página y el final del texto es
  el final de la lectura. Retrocede hasta el último párrafo que cierra
  oración y lo deja ENTERO; nunca parte un párrafo por un punto de en
  medio, que es lo que sí hace `_acaba_en_frase`.
  · **EL PLIEGO EMPIEZA Y ACABA EN FRASE** (`_empieza_en_frase` /
  `_acaba_en_frase`): la página de la izquierda arranca tras un punto y
  la de la derecha cierra en punto, para no dejar al lector colgado en
  ninguno de los dos bordes. Al EMPEZAR hay tope (no se sacrifica más
  de `_MAXIMO_RECORTE` 22 % ni de `_RECORTE_ABSOLUTO` 140 caracteres);
  al CERRAR no lo hay —es donde se acaba de leer—, solo el suelo de
  `_MINIMO_QUE_QUEDA` para no vaciar la página. En VERSO no se parte el
  renglón: se descartan renglones enteros. El tope absoluto hace falta
  porque el porcentaje castiga a los textos cortos: perder 14 letras de
  46 es un 30 %, pero son 14 letras.
  · `_es_ruido_de_margen` saca de las notas los restos de paginación
  del editor («b c d e 94a», «20C»), que salían al pie como si fueran
  una nota de algo.
- **FORMATEADOR DE PÁGINA (`app/formato.py` + `tools/formatear.py`,
  2026-08-08)**: los `.jsonl` guardan el texto FIEL al PDF y NO se
  tocan; este módulo los compone al leerlos, como está compuesto un
  tomo de la BCG (Gredos, 1977; introducción, traducción con la
  referencia canónica al MARGEN —Estéfano, Bekker, libro y parágrafo,
  verso—, notas al PIE e índice de nombres). Tres defectos medidos en
  el corpus y lo que hace con cada uno:
  (1) **renglones sueltos en mitad del párrafo** —el más común—: en
  Isócrates, «…las islas42; Pélope» y «hijo de Tántalo, se» eran dos
  renglones. Regla: el párrafo SOLO se corta donde acaba una oración y
  el renglón no llega al margen; un renglón corto que NO cierra frase
  es un trozo suelto y se une con el siguiente.
  (2) **marcadores del margen** («b», «403d», el número de la pieza)
  intercalados en el texto → van al margen.
  (4) **EPÍGRAFES DEL MARGEN (`_es_epigrafe`, 2026-08-09)**: la BCG
  lleva al margen unos resumencitos —«Herípidas ataca a Farnabazo»,
  «Entrevista de Agesilao y Farnabazo»— y el conversor a libro
  electrónico los soltó DENTRO del texto corrido. Sin reconocerlos se
  pegaban al párrafo anterior y la hoja entera quedaba en UN bloque de
  dos mil caracteres; como `acaba_en_parrafo` y `empieza_en_frase`
  cortan ENTRE bloques, no había frontera y el pasaje del día se
  quedaba colgado a media frase (Helénicas, hoja 85). Cinco condiciones,
  y la quinta es la que separa el epígrafe de un renglón partido, que es
  lo único con lo que se puede confundir: corto (< 72 % de la caja),
  empieza en mayúscula, no cierra frase ni acaba en coma, el renglón
  SIGUIENTE empieza en mayúscula, y no acaba en palabra de enlace
  (`_NO_CIERRAN`: que, de, y, el, la…) — «Pero Agesilao, que» tiene la
  forma de un epígrafe hasta que se mira esa última palabra.
  Por encima de todas: lo que va abierto tiene que CERRAR FRASE, y eso
  se comprueba mirando la oración, NO el ancho del renglón: el que
  precedía al segundo epígrafe medía 70 sobre una caja de 82 y el
  umbral de «lleno» está en 69,7 — lo pasaba por tres décimas de
  carácter y se tragaba el epígrafe. Corpus: 611.551 → 615.184 bloques
  (+3.633; el resto de los 15.807 que reconoce ya los cazaba
  `_mayusculas`), ratio intacto en 3,0 renglones por bloque. En
  Aristófanes son además los NOMBRES DE PERSONAJE del diálogo.
  (3) **llamadas de nota pegadas a la palabra** («las islas42;»): se
  despegan a `llamadas`. El patrón exige letra delante y signo detrás,
  para no tocar «Bekker 1094a» ni «Libro 42».
  **PROSA O VERSO no se decide por el largo del renglón** —la Ilíada
  mide 57 y hay prosa de 55—, sino por la RACHA de renglones que llenan
  la caja: la prosa justificada los encadena y el verso no. Medido:
  Isócrates 86 % llenos y racha 7; Séneca (tragedia) 26 % y racha 1;
  una cronología de Plutarco 30 % y racha 2. Umbral: racha ≥ 4 o 60 %.
  `tools/formatear.py` enseña una hoja compuesta (`--hoja N`), mide un
  tomo o todos (`--informe`) y lista las páginas que peor quedan
  (`--sospechosas`). Corpus entero: 181.680 hojas, 1.819.971 renglones
  del PDF → 611.551 bloques (3,0 renglones por bloque) y 20.431
  llamadas de nota despegadas.
  La GUI NO duplica nada: importa de aquí (`componer_pagina`,
  `_partir_notas`, los recortes por frase).
  · **LA PÁGINA SE COMPONE, no se vuelca** (`componer_pagina`,
  `html_de_hoja`, 2026-08-08): el texto guardado es FIEL al PDF —
  renglones cortados a lo ancho de la caja y, sueltos entre medias, los
  marcadores del margen («b», «c», «403d», el número de la carta). Así
  se leía fatal. Ahora: las marcas van arriba en pequeño (son la
  paginación del EDITOR, no texto del autor), los renglones partidos se
  unen en párrafos justificados, el renglón que sigue a un NÚMERO
  suelto y empieza en mayúscula es el encabezado de la carta, y las
  notas van al pie con su número en superíndice (`_partir_notas`).
  REGLA que costó dos intentos: para saber si unir NO se clasifica la
  página como prosa o verso —se probó midiendo la mediana del renglón y
  no se puede: la Ilíada mide 57 caracteres y hay prosa de 55—, sino
  que se mira renglón a renglón: se une solo el que viene LLENO hasta
  la caja (85 % del percentil 90, con un suelo de `_CAJA_MINIMA`=55) y
  sin cerrar frase. Sin ese suelo, en una página de verso el propio
  verso más largo hacía de «ancho» y el poema se unía en un párrafo.
  · **ORDEN ALFABÉTICO por defecto** (combo «Ordenar», 2026-08-05): por
  el canónico —que empieza por el autor— y SIN tildes, que si no
  «Ésquilo» acaba detrás de «Zenón». «Más coincidencias» sigue a mano.
  · Tras `revisar_textos` o cualquier cambio en los `.jsonl` hay que
  REINDEXAR. Reindexar borra e inserta y SQLite se queda las páginas
  libres (256 → 325 MB sin un pasaje más): `indexar()` compacta solo
  cuando ha rehecho 20 tomos o más.
  · **ÍNDICE DE COBERTURA `idx_pasajes_resumen(id, tomo_id, clase,
  hoja)`**: sin él, contar por tomo leía la fila entera de cada pasaje
  —y la fila lleva el TEXTO—, así que «hombre» (15.433 pasajes) tardaba
  4,1 s con el disco frío; con el índice, 135 ms. Más `cache_size` de
  64 MB y `mmap_size`: la caché de serie (2 MB) mandaba al disco en
  cada palabra nueva.
  · **El renglón no se toca**: se trocea por palabras CON su separador
  pegado (`re.findall(r"\S+\s*")`), porque en los poetas el renglón es
  el verso. 180 palabras por pasaje con 40 de solape; el solape se
  retira al recomponer la hoja y se devuelve UNA hoja por resultado
  (máx. 3 por tomo), o salía la misma página tres veces.
  · **Reindexado incremental** por fecha+tamaño: se puede seguir
  analizando PDF, lo nuevo entra en la siguiente pasada (`pendientes()`
  lo avisa en la ventana). El indexado va en `_IndexarWorker(QThread)`.
  · `a_consulta_fts` ESCAPA todo (`*`, `:`, `^`, `NEAR`): sin eso, un
  signo suelto reventaba la consulta con un error de SQLite.
  · **BANCO DE 82 CONSULTAS (2026-08-08)**: se probó el buscador con
  acentos, mayúsculas, griego, frases, prefijos, preguntas, vacíos,
  sintaxis de FTS, inyección SQL, signos, emoji, caracteres de control,
  NFC/NFD y palabras larguísimas. Cero caídas. Salieron CUATRO defectos
  reales, todos con prueba de regresión:
  (1) **Acento suelto (NFD)**: pegando texto de un PDF, «nómos» llega
  como «o»+U+0301, el signo caía en la limpieza de puntuación y PARTÍA
  la palabra («no mos»): 1 tomo en vez de 50. `_compone` recompone en
  NFC y, si no hay forma junta, quita el signo (el índice ya ignora
  tildes).
  (2) **Prefijo de una o dos letras**: «a*» barría 121.320 pasajes en
  3,2 s. Con menos de `_MINIMO_PREFIJO`=3 letras se busca la palabra
  tal cual.
  (3) **Consulta de solo palabras vacías** («de los» → 137.939
  pasajes): `solo_palabras_vacias` lo detecta y la ventana lo explica.
  (4) **GRIEGO SIN ACENTOS: no encontraba NADA.** `remove_diacritics 2`
  de SQLite solo quita tildes LATINAS; el índice guarda «λόγοσ» y
  «πρᾶξισ» con las suyas, y el politónico es casi imposible de teclear.
  Arreglo (VERSION 2): a cada pasaje con griego se le guarda en la
  columna `busqueda` una copia con sus palabras griegas SIN acentos, y
  el índice FTS lee de la VISTA `pasajes_indexados`
  (`COALESCE(busqueda, texto)`) — así el pasaje se guarda tal cual y se
  busca por la versión ampliada, sin duplicar los 87 M de caracteres:
  solo el 1,2 % de los pasajes lleva griego (2 MB). Los disparadores
  deben pasar a FTS ESE MISMO `COALESCE`, o al reindexar quedan restos.
  · **Migrar de versión se hace EN EL SITIO** (`_preparar_para_esta_
  version`), nunca borrando el archivo: la aplicación puede tenerlo
  abierto y Windows entonces no deja. Y hay que VACIAR las tablas con
  los disparadores ya fuera — si se deja para después de recrear el
  índice vacío, cada borrado intenta quitar una fila que no está y
  SQLite responde «database disk image is malformed».
  · `_plano()` (no `normaliza`) para resaltar: la marca se calcula sin
  tildes y se pinta sobre el original, así que las posiciones deben
  coincidir carácter a carácter — `normaliza` junta los espacios y
  descolocaba todo en textos llenos de saltos de verso.
  · La hoja entera se pide al ÍNDICE (`hoja_completa`), nunca al
  `.jsonl`: abrir 100 MB desde la GUI es lo que se quitó el 2026-07-31.
  · Instancia ÚNICA `rag.indice_compartido()`; `rag.cerrar_indice()` al
  salir (deja un WAL abierto). Nunca una conexión por diálogo.
- **PASAJE DEL DÍA (`rag.pasaje_del_dia` + `BotonDelDia` +
  `PasajeDelDiaDialog`, 2026-08-08)**: un pasaje al azar de la
  colección, el MISMO hasta las DOCE DE LA NOCHE. No son «24 h» contadas
  desde que se abre: va por FECHA — la pone la ventana y el índice
  guarda la elección en `meta`—, así que cambia a medianoche aunque se
  haya leído a las once.
  **EL RELOJ DEL DIÁLOGO (2026-08-09)**: eso basta para el botón, que
  pide la fecha al pulsarlo, pero NO para la ventana ya abierta — y esta
  aplicación vive en la bandeja, así que puede quedarse abierta desde
  ayer. `PasajeDelDiaDialog` lleva un `QTimer` de UN disparo apuntado al
  INSTANTE del cambio (`_armar_para_medianoche`), no un sondeo cada
  minuto: es exacto y no gasta nada. Dos detalles que hacen falta: se
  añade UN SEGUNDO de propina —disparando clavado a las 00:00:00,
  `date.today()` puede devolver todavía el día de ayer— y el disparo se
  vuelve a armar solo, para las ventanas que aguantan varios días o para
  cuando el equipo estuvo suspendido y el aviso llega tarde (da igual
  cuántos días pasaran: se pide el de HOY). Por eso `_pintar()` está
  separado del `__init__` y la línea de nombres se crea SIEMPRE y se
  esconde si el tomo nuevo no trae índice: un widget que entra y sale
  del layout descoloca todo lo de debajo.
  **SIN LÍNEA DE RESUMEN (2026-08-09)**: había una frase en cursiva
  bajo el título, ENTRESACADA del pasaje (`descripcion_de_pasaje`, que
  sigue existiendo y con pruebas, pero ya no se enseña). No era un
  resumen sino una cita, y volvía a salir unos renglones más abajo
  DENTRO de la propia página: se leía como una promesa incumplida
  —anunciaba algo que no era lo primero que se leía—. Se probó primero
  a que la página empezara justo en el pasaje del que sale la frase
  (`desde_el_pasaje`) y la disonancia seguía, así que el usuario pidió
  quitarla. El título y la línea de nombres ya dicen de qué va. NO
  reponerla sin que la pida.
  **NO hay IA y el título NO se inventa**: sale de los NOMBRES que el
  traductor puso en el índice del propio tomo, y si no los hay, del
  rótulo de la sección (cuando dice algo: `_rotulo_sirve` descarta
  «Libro I», «III», «Vol. II»), y en último caso del título del tomo.
  El resumen se ENTRESACA —la frase con más nombres del índice—, no se
  redacta: un resumen inventado en una biblioteca es peor que ninguno.
  Tres cribas del sorteo, las tres salidas de mirar lo que devolvía:
  (1) SOLO tomos con índice de nombres (60 de 210) — sin él el título
  se quedaba en el del tomo, que no dice nada del pasaje;
  (2) nada del primer `_PRELIMINARES` (18 %) del tomo, que es donde
  están la introducción y la nota bibliográfica del editor, y hablan DE
  la obra en vez de ser la obra;
  (3) fuera los rótulos de aparato (`_RUTINA`: bibliografía, ediciones,
  argumento, sinopsis…).
  Se elige por IDENTIFICADOR, nunca con OFFSET (106.583 candidatos), y
  DENTRO de la tanda manda el propio dado: cogiendo «el primero», dos
  días seguidos caían cerca y repetían pasaje (30 días → 30 pasajes
  distintos). Si con esas cribas no sale nada —pocos tomos indexados—,
  vale cualquier pasaje de cuerpo: mejor eso que quedarse sin pasaje.
  La página se enseña arrancando en SU pasaje (`desde_el_pasaje`): una
  hoja da dos o tres, y enseñándola desde arriba el resumen citaba algo
  de más abajo y no cuadraba con lo primero que se leía (2026-08-08).
  El BOTÓN va solo y con aire ENCIMA de la botonera, ancho pero bajo
  (32 px), y junta los dos
  brillos que ya existían: la veladura metálica de la fila seleccionada
  de las tablas (con su destello especular siguiendo al puntero) y las
  estrellas del resaltado de los pasajes, que comparten
  `pintar_estrella` — duplicarla sería que se separasen al tocar una.
  La veladura y el destello van atados al CALOR del botón, no pintados
  siempre: `GlowButton.leaveEvent` apaga el calor pero NO borra la
  posición del ratón, así que el botón se quedaba encendido después de
  sacar el cursor (por eso `BotonDelDia` sobrescribe `leaveEvent`).
- Rangos especiales (dato del usuario): tomos 360-415 = los MÁS RAROS
  (💎 en tabla y toast); 416-420 = apéndice, NO pertenecen propiamente
  a la colección (atenuados). Constantes en collection.py
  (RARE_RANGE/APPENDIX_RANGE, is_rare/is_appendix).
- Umbrales de notificación, por prioridad: tomo ⭐ Deseado →
  `wished_discount_percent` (20 % por defecto; 0 = cualquier bajada) >
  patrón de thresholds > global. `precio_objetivo` dispara por PRECIO
  ABSOLUTO (€) al margen del %, con nota "precio objetivo alcanzado"
  en el toast (SIN emoji de diana — retirado por poco profesional,
  2026-07-26). Verificado de punta a punta el 2026-08-09 con el aviso
  real de Ausonio (70 € → 7 €, 90 %): notifica con el umbral global
  puesto al 95 %, el límite se incluye (`<=`) y se salta la puerta de
  `is_reliable()` porque el objetivo mira un PRECIO extraído, no un
  porcentaje. Cinco pruebas en `tests/test_lots.py`.
  **LA COLECCIÓN SE RELEE EN CADA VUELTA (2026-08-09)**: `self._tomos`
  se cargaba UNA vez, en `run()`. Lo que el usuario cambia desde la
  ventana —precio objetivo, ⭐ Deseado, Obtenido— vive en esas filas, así
  que poner un precio objetivo con el programa abierto NO surtía efecto
  hasta reiniciarlo. `_cargar_coleccion()` se llama ahora al principio
  de `_check_new_mail`, que es por donde pasa todo correo; son 423 filas
  y solo se leen cuando llega correo. Si la lectura falla se CONSERVA la
  copia anterior: quedarse sin colección dejaría de etiquetar ofertas.
- `config.py`: la contraseña se guarda CIFRADA con DPAPI
  (`email_password_dpapi`, base64; ligada al usuario de Windows) —
  jamás en claro en config.json; migración automática al cargar.
- MainWindow: health-check al arrancar (credenciales/Excel/colección/
  toasts/bs4 con ✔✖ en la línea de mensajes) e icono de bandeja con
  estado (SOLO punto verde/rojo, sin contador de alertas — se probó y
  el usuario lo quitó 2026-07-26; actualizado en start/stop).
