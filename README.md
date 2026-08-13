# 📚 Monitor BCG — Biblioteca Clásica Gredos

Aplicación de escritorio para **Windows 10** que vigila tu correo y te avisa
**al instante** con una **notificación nativa de Windows** cuando un libro que
tienes en favoritos en Todocolección recibe un **gran descuento** (≥ 50 % por
defecto).

La fuente de información es **exclusivamente el correo electrónico**: no se
hace scraping de la web. Todos los correos de ofertas llegan siempre desde la
propia página de Todocolección, así que el programa filtra por remitente
(`todocoleccion`) y por palabras clave de bajada/cambio de precio o favoritos.

```
📚 Biblioteca Clásica Gredos
Plutarco - Vidas Paralelas II
40 € → 4 €
Descuento: 90 %
```

Al hacer clic en la notificación se abre directamente el anuncio en tu navegador.

---

## Características

- **IMAP IDLE (tiempo real)**: si tu servidor lo soporta (Gmail, Outlook…),
  el servidor avisa a la aplicación en cuanto llega el correo. La notificación
  aparece en **segundos** y se consumen menos recursos que sondeando.
- **Fallback automático a sondeo** cada 30 s (configurable) si IDLE no está
  disponible o lo desactivas en `config.json`.
- **Notificaciones nativas** del Centro de actividades de Windows 10, con
  icono, sonido y clic que abre el anuncio. El **resumen del día** abre
  la propia aplicación por el **Historial** (enlace `bcgmonitor://`). Sin ventanas emergentes ni diálogos.
  Cadena robusta de 3 niveles: winotify → PowerShell + WinRT (sin
  dependencias) → globo de la bandeja del sistema.
- **Detección de LOTES**: si un correo de Todocolección contiene un lote de
  `min_lot_books` (5 por defecto) o más libros — "LOTE DE 7 LIBROS",
  "5 TOMOS", "LIBROS I AL IX" — se envía una notificación específica 📦 y
  se registra en el historial con estado `lote`.
- **Interfaz "tomo Gredos" premium**: ventana sin el marco de Windows,
  barra de título propia, esquinas redondeadas, sombra, brillos dorados
  animados en botones y título, y paneles con pespunte de "bordado" en oro.
- **Sin duplicados**: se recuerda el UID IMAP de cada correo procesado
  (persistido en SQLite), incluso entre reinicios. Y si un correo se
  vuelve a analizar a propósito (marcándolo como no leído), su
  `Message-ID` evita reinsertar lo que ya dejó en historial, precios,
  lotes y publicaciones vigiladas.
- **Historial en SQLite** (`tc_monitor.db`): fecha, título, precio antiguo,
  precio nuevo, descuento, enlace y estado (notificado/ignorado).
- **Registro** en `log.txt`: inicio, errores, correos leídos, descuentos
  detectados y notificaciones enviadas.
- **Bandeja del sistema**: cerrar la ventana no cierra el programa; sigue
  vigilando desde la bandeja. "Salir" solo desde el menú del icono.
- Interfaz PySide6 con estado, última revisión, contadores y botones
  Iniciar / Detener / Configuración / Historial / Notificaciones /
  Precios / Colección / Lotes / Textos.
- **Buscador dentro de los tomos**: busca una frase en el texto de los
  tomos ya analizados y devuelve el pasaje con su tomo, su obra y su
  **página impresa**; doble clic abre la página entera con lo buscado
  resaltado. Si lo que buscas es un nombre propio, encima aparece la
  respuesta del **índice de nombres del propio traductor**, con su cita
  canónica. Todo local: no sale nada de tu equipo y no necesita ninguna
  clave de API.

## Estructura del proyecto

El **código** vive en `app/`, las **herramientas de desarrollo** en
`tools/`, y los **datos de ejecución** en la raíz —que es también la
carpeta del `.exe` una vez empaquetado—.

```
Codigo BCG/
├── main.py              # Punto de entrada (python main.py [--tray])
├── MonitorBCG.spec      # Empaquetado: pyinstaller MonitorBCG.spec
├── requirements.txt
│
├── app/                 # Código de la aplicación
│   ├── gui.py           # Interfaz PySide6 + bandeja del sistema
│   ├── imap_monitor.py  # Hilo de vigilancia IMAP (IDLE + sondeo)
│   ├── notification.py  # Notificaciones nativas de Windows
│   ├── database.py      # SQLite: historial, precios, colección
│   ├── utils.py         # Parseo de correos y publicaciones
│   ├── collection.py    # Colección BCG (Excel + cruce de títulos)
│   ├── config.py        # config.json (contraseña cifrada DPAPI)
│   ├── autostart.py     # Arranque con Windows
│   ├── deeplink.py      # Enlaces bcgmonitor:// de las notificaciones
│   ├── pdftext.py       # Extracción del texto de los PDF de los tomos
│   ├── formato.py       # Composición de la página (párrafos y notas)
│   ├── rag.py           # Buscador dentro del texto (índice FTS5)
│   ├── dataset.py       # Banco de correos de prueba
│   └── ai.py            # Capa de datos de la IA (hoy sin usar)
├── tools/               # Utilidades de desarrollo (no van al .exe)
│   ├── debug_panel.py   # Panel de depuración del parser
│   ├── formatear.py     # Compone un tomo y mide cómo queda
│   └── generate_expected.py
├── tests/               # Batería pytest + correos de regresión
├── assets/              # icon.ico, icon.png, version_info.txt
├── docs/                # PLAN_RAG.md y demás
│
└── (datos de ejecución, junto al .exe)
    ├── config.json      # Ajustes; la contraseña va cifrada con DPAPI
    ├── tc_monitor.db    # Historial, precios, colección, vigilados
    ├── log.txt
    ├── BDtomos/
    │   ├── titulosBCG.xlsx      # Los 423 tomos de la colección
    │   ├── TextosTomos/*.jsonl  # Texto extraído de los PDF
    │   └── textos.db            # Índice del buscador (reconstruible)
    └── Libros/          # Donde dejar los PDF por analizar
```

**Lo que se puede borrar sin perder nada** (se rehace solo):
`textos.db` —el índice; se reconstruye con «Reindexar» en unos 40 s—,
`__pycache__/`, `.pytest_cache/` y `log.txt`. Lo demás, no:
`TextosTomos/*.jsonl` es el corpus extraído de PDF que quizá ya no
tengas, y `tc_monitor.db` guarda el historial y las series de precios.

## Instalación

Requiere **Python 3.10+** en Windows 10.

```bat
cd "Codigo BCG"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Para analizar PDF escaneados hace falta además **Tesseract OCR**, que no
se instala con pip (ver `requirements.txt`). Sin él todo lo demás
funciona igual.

## Configuración

Edita `config.json` (o usa el botón **Configuración** de la propia app):

| Clave | Descripción |
|---|---|
| `imap_server` / `imap_port` | Servidor IMAP y puerto SSL (Gmail: `imap.gmail.com` / `993`) |
| `email_user` | Tu dirección de correo |
| `email_password` | **Contraseña de aplicación** (ver abajo) |
| `mail_folder` | Carpeta a vigilar (`INBOX` por defecto) |
| `min_discount_percent` | Umbral mínimo de descuento para notificar (50 por defecto) |
| `check_interval_seconds` | Intervalo de sondeo si no hay IDLE (30 s por defecto) |
| `use_imap_idle` | `true` = tiempo real con IMAP IDLE cuando el servidor lo soporte |
| `enable_sound` | Sonido en la notificación |
| `auto_open_link` | Abrir el anuncio automáticamente al notificar (`false` por defecto) |
| `sender_filter` | Texto que debe contener el remitente (`todocoleccion`) |
| `subject_keywords` | Palabras clave de bajada/cambio de precio o favoritos |

### Contraseña de aplicación (Gmail)

Gmail no acepta tu contraseña normal por IMAP. Debes:

1. Activar la **verificación en dos pasos** en tu cuenta de Google.
2. Crear una **contraseña de aplicación** en
   *Cuenta de Google → Seguridad → Contraseñas de aplicaciones*.
3. Pegar esa contraseña de 16 caracteres en `email_password`.

Outlook/otros proveedores tienen mecanismos equivalentes.

## Uso

```bat
python main.py
```

- El monitor arranca automáticamente al abrir el programa.
- Cierra la ventana con la ✕: el programa **sigue funcionando** en la bandeja.
- Clic en el icono de la bandeja → reabrir la ventana. Clic derecho → **Salir**.

## Crear el .exe con PyInstaller

Se empaqueta **siempre con el .spec**, nunca con opciones sueltas en la
línea de órdenes: el `.spec` es quien mete los iconos, el número de
versión y las exclusiones, y quien deja fuera `config.json`.

```bat
pip install pyinstaller
pyinstaller MonitorBCG.spec
```

El resultado queda en `dist\MonitorBCG.exe`.

### Qué copiar junto al .exe

El programa busca sus datos **en su propia carpeta**. Al lado del `.exe`
tienen que estar:

| Qué | Para qué | Si falta |
|---|---|---|
| `config.json` | Ajustes y credenciales | No puede conectarse al correo |
| `tc_monitor.db` | Historial, precios, colección | Empieza de cero |
| `BDtomos\titulosBCG.xlsx` | Los 423 tomos | No hay colección que cruzar |
| `BDtomos\TextosTomos\` | Texto de los tomos | No hay nada que buscar |
| `BDtomos\textos.db` | Índice del buscador | Se rehace con «Reindexar» |

> **`config.json` no viaja dentro del .exe a propósito**: lleva la
> contraseña del correo (cifrada con DPAPI, ligada a tu usuario de
> Windows). Meterla en el paquete la repartiría con el programa.

Ten en cuenta el tamaño: con el corpus y su índice, `BDtomos\` ronda los
**700 MB**. El `.exe` es lo pequeño de este conjunto.

### Arranque con Windows

Lo gestiona el propio programa desde **Configuración** (clave `Run` de
`HKCU`, con `--tray` para arrancar en la bandeja). No hace falta tocar
`shell:startup`.

> **Primer arranque lento**: el `.spec` genera un `.exe` único, que se
> descomprime en una carpeta temporal cada vez que se abre. Con Qt
> WebEngine dentro eso son unos segundos. Si molesta, se cambia a
> carpeta (`--onedir`): arranca al instante, pero en vez de un archivo
> queda una carpeta con el `.exe` y sus bibliotecas.

## Cómo decide el programa qué notificar

1. Llega un correo nuevo (IDLE avisa al instante, o lo detecta el sondeo).
2. ¿El remitente contiene `todocoleccion`? Si no → se ignora.
3. ¿El asunto o cuerpo contiene palabras de bajada/cambio de precio o
   favoritos? Si no → se ignora.
4. Se extraen título, precio anterior, precio nuevo y enlace. Si el correo no
   trae el porcentaje, **se calcula**: `(antes − ahora) / antes × 100`.
5. Descuento ≥ umbral → **notificación de Windows** inmediata.
   Descuento menor → se guarda en el historial como *ignorado*, sin molestar.
6. El UID del correo queda registrado: **nunca** se notifica dos veces.

Como todos tus favoritos son tomos de la **Biblioteca Clásica Gredos**, el
programa no clasifica los libros: asume que toda bajada de precio pertenece a
tu colección y se centra en avisarte lo antes posible.

## Ya implementado sobre esa base

- **Historial de precios por libro** (`price_history`) con el título
  canónico de la colección: el mismo tomo vendido por distintos
  vendedores comparte serie, y cada punto abre su publicación.
- **Colección BCG** (`tomos`, importada de `BDtomos/titulosBCG.xlsx`) con
  cruce difuso de títulos, etiquetas Obtenido/Deseado y precio objetivo.
- **Lotes** en su propio espacio de series, con publicaciones vigiladas.
- **Umbrales por libro** (`thresholds`), que ganan al umbral global.

Ampliaciones que la arquitectura deja a mano:

- **Filtros por vendedor** → columna `vendedor` en `history` y un campo
  más en `utils.parse_alert_email`.
- **Estadísticas de descuentos** → consultas sobre `history` y una
  ventana más en la GUI (mismo patrón que `PriceHistoryDialog`).
