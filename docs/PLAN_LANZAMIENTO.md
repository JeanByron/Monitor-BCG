# Plan de lanzamiento — qué se retira cuando la app esté lista

> **NO EJECUTAR TODAVÍA.** Este plan se aplica SOLO cuando el usuario
> diga que la aplicación está lista. Redactado el 2026-08-09, con la
> base de textos a medio llenar: **210 tomos de 423**.
>
> El encargo: cuando estén los 423 analizados, todo lo que sirve para
> LLENAR la base de textos deja de tener sentido y sale de la
> aplicación, junto con lo demás que solo valía durante el desarrollo.

## 0. Precondición — lo que hace el usuario antes

1. Analizar los **213 tomos que faltan** (hoy 210 de 423).
2. Pulsar **«Revisar»** una última vez (rehace lo que el analizador ha
   aprendido después: secciones, índices de nombres, notas desbordadas).
3. Pulsar **«Actualizar índice»** y comprobar que `pendientes()` da 0.
4. Copia de seguridad de `tc_monitor.db` y de `BDtomos/`.

Sin esos cuatro pasos NO se empieza: retirar el analizador antes de
tener los textos deja la colección incompleta para siempre.

## 1. Hallazgos de la investigación (2026-08-09)

Tres cosas medidas en el código, y las tres cambian el plan:

### 1.1 Los `.jsonl` no hacen falta en ejecución

`rag.hoja_completa` saca la página **del índice** (`textos.db`), nunca
del `.jsonl` — está así a propósito desde el 2026-07-31, porque abrir
100 MB desde la interfaz colgaba la ventana. Las únicas cosas que leen
los `.jsonl` son las que se van a retirar:

| Quién | Para qué |
|---|---|
| `rag.indexar()` / `pendientes()` | construir el índice |
| `pdftext.estado_de_los_tomos` | el ✔ de la lista de Textos |
| `pdftext.revisar_textos` | repasar lo guardado |
| `tools/formatear.py` | herramienta de desarrollo |

**Consecuencia**: la carpeta `BDtomos/TextosTomos/` (**178 MB**) puede
no enviarse. Bastaría `textos.db` (337 MB).

> **A DECIDIR CON EL USUARIO**: si `textos.db` se borra o se corrompe,
> sin los `.jsonl` no hay forma de reconstruirlo. Opciones: (a) enviar
> los `.jsonl` igualmente como respaldo frío, (b) no enviarlos y
> guardarlos aparte fuera del programa. **No decidir esto solo.**

### 1.2 Todo lo de PDF vive dentro de `TextosDialog`

Las seis llamadas a `pdftext` de la interfaz están en ese diálogo
(`app/gui.py` 4279-4710). **Nadie más importa `pdftext`** — `rag.py`
no lo hace. Retirando ese diálogo:

- `app/pdftext.py` queda huérfano entero (2.204 líneas)
- **PyMuPDF sale del `.exe`** (es la dependencia pesada)
- el OCR entero sale (`ocr_disponible`, `idiomas_ocr`,
  `completar_con_ocr`) y con él el campo `tesseract_path`

### 1.3 Hay código YA inalcanzable

`open_dataset_stats` y `open_debug_panel` están definidos en
`MainWindow` pero **no los llama nada**: ni botón, ni atajo de teclado,
ni el menú de la bandeja (que solo tiene «Mostrar» y «Salir»).

## 2. Qué se retira, por fases

### Fase 1 — El llenado de la base de textos

Sale de la interfaz:

1. Botones **«Analizar PDF…»** y **«Revisar»** de `TextosDialog`.
2. Botón **«Actualizar índice»** de `BuscarTextosDialog` (`_indexar`,
   `_IndexarWorker`).
3. Campo **«Ruta de Tesseract»** de Configuración.

> **A DECIDIR CON EL USUARIO**: ¿la lista de los 423 tomos con su ✔ se
> queda como consulta (cuánto texto tiene cada tomo), o desaparece el
> botón «Textos» y solo queda «Buscar»? Si se queda, hay que sustituir
> `estado_de_los_tomos` (que lee los `.jsonl`) por una consulta al
> índice, o la lista se queda vacía al no enviar la carpeta.

Queda huérfano en el código:

- `app/pdftext.py` (menos lo que use `tools/`, que no se empaqueta)
- `rag.indexar`, `rag.pendientes`, `rag.TEXTOS_DIR`
- `PyMuPDF` de `requirements.txt` y del `.exe`

### Fase 2 — Código muerto ya detectado

| Qué | Por qué sale |
|---|---|
| `open_dataset_stats`, `DatasetDialog`, `app/dataset.py`, `dataset_stats.json` | inalcanzable; banco de correos de prueba |
| `open_debug_panel`, `tools/debug_panel.py` | inalcanzable; y es lo único que usa `tkinter` |
| `app/ai.py`, `Database.buscar_tomos`, columnas `descripcion`/`temas`/`desc_modelo`/`desc_fecha` | sin interfaz desde el 2026-08-05 (el usuario no tiene clave de OpenAI) |
| `tests/test_ai.py`, `tests/test_dataset.py` | prueban lo anterior |

### Fase 3 — El Excel de la colección

`CollectionDialog` auto-importa si la tabla está vacía, y hay botón
**«Reimportar Excel»**. Si `tc_monitor.db` ya viaja con los 423 tomos,
`titulosBCG.xlsx` y `openpyxl` podrían no enviarse.

> **A DECIDIR CON EL USUARIO**: los ✔ de Obtenido/Deseado sobreviven a
> los reimportes por diseño, así que quizá quiera conservar el botón.
> **Recomendación**: conservarlo — pesa poco y es la única forma de
> recuperarse si la tabla `tomos` se estropea.

### Fase 4 — Empaquetado

1. `MonitorBCG.spec`: añadir a `excludes` lo que quede muerto.
   **Seguir sin excluir módulos de Qt** (QtWebEngine carga QtQml/QtQuick
   por dentro; sin ellos muere `ListingPriceFetcher`).
2. `requirements.txt`: fuera PyMuPDF; openpyxl según la fase 3.
3. Medir el `.exe` antes y después, y dejar la cifra escrita.

## 3. Qué NO se toca, pase lo que pase

- El monitor IMAP entero, las notificaciones y los enlaces
  `bcgmonitor://`.
- Historial, precios, lotes, colección, publicaciones vigiladas.
- El buscador: `rag.buscar`, `tomos_con`, `hoja_completa`,
  `buscar_nombres`, `pasaje_del_dia`.
- `app/formato.py` — compone la página al LEER, no al guardar; se usa
  en cada pasaje que se enseña.
- El sistema de diseño (cuero, oro, `GlowButton`, `GlowTable`…).

## 4. Verificación después del recorte

1. `python -m pytest tests/ -q` en verde (hoy: 339 pasan, 4 se saltan).
2. `python -m pyflakes app tools tests main.py` sin un aviso.
3. Empaquetar y, **con el `.exe`**, comprobar a mano:
   - buscar una frase y abrir un tomo del resultado (pliego de dos
     páginas, resaltado con estrellas);
   - abrir el **pasaje del día**;
   - abrir la ficha de un tomo y consultar el precio de una publicación
     (es lo que prueba que QtWebEngine sigue entero);
   - recibir un aviso y pulsarlo (prueba el enlace `bcgmonitor://`).

El tercero y el cuarto son los que de verdad importan: son los únicos
fallos que no se ven hasta tener el programa empaquetado.
