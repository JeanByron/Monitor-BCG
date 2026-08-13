# Plan de desarrollo — Buscador RAG sobre los textos de la BCG

> **Objetivo**: poder preguntar *«¿qué tomo habla de la vida y costumbres
> de los lacedemonios?»* y obtener la respuesta con su **cita exacta**
> (tomo, obra, sección y página impresa o verso), a partir de los textos
> ya extraídos de los PDFs.
>
> **Cómo se usa este documento**: cada tarea lleva su **prompt de
> activación**. Al escribirlo, se explicará en tres o cuatro líneas qué
> se va a tocar y **se esperará tu aceptación** antes de escribir una
> sola línea de código. Ninguna tarea arranca sola, y ninguna depende de
> que las siguientes se hagan.

---

## 1. Con qué se cuenta (medido el 2026-08-04)

| Dato | Cifra |
|---|---|
| Tomos con texto extraído | 132 de 423 |
| Hojas guardadas | 101.402 |
| Palabras | 14.328.645 |
| Caracteres de texto | 87,6 millones |
| Tamaño en disco (`BDtomos/TextosTomos`) | 108 MB |
| Entradas de índice de nombres | 25.342, en 45 tomos |
| Referencias de margen (verso, parágrafo, Estéfano, Bekker) | 73.785 |
| Notas finales con su número | 66.050 |
| Hojas con página impresa conocida | 10.202 (los 28 tomos con maqueta de papel) |

Reparto de las hojas por sección: `notas_finales` 66.781 · `texto`
28.342 · `introduccion` 4.905 · `indice_nombres` 646 · `indice_general`
374 · `bibliografia` 320.

**Lo que ya existe y no hay que rehacer**

- Un `.jsonl` por tomo, una línea por hoja, con `cuerpo`, `notas`,
  `obra`, `seccion`, `impresa`, `versos`, `llamadas`, `nota`.
- `pdftext.texto_para_buscar(registro)` — aplana el texto para buscar
  frases que cruzan el renglón (el renglón se conserva porque en los
  poetas **es el verso**).
- `pdftext.indice_de_nombres` — la concordancia del traductor, ya
  extraída y normalizada.
- `Database.buscar_tomos` — búsqueda por palabras sobre los METADATOS de
  la colección (autor, obra, notas, descripción, temas). Es otra cosa:
  esto busca *tomos*, el RAG busca *pasajes*. **Retirada de la interfaz
  el 2026-08-05** junto con `SearchDialog`; el código sigue ahí, sin
  usar.

**Restricciones que manda el proyecto** (ver `CLAUDE.md`)

1. Nada de dependencias pesadas sin justificarlas: la aplicación se
   empaqueta con PyInstaller en un `.exe`. Meter PyTorch (≈2 GB) es
   inviable.
2. Los textos son de PDFs del usuario: **el corpus no se sube a ningún
   servicio** salvo que se decida explícitamente, y aun entonces solo
   los fragmentos que hagan falta.
3. Las claves de API van cifradas con DPAPI, jamás en claro.
4. Lo que se muestre del texto es una **cita corta** (dos o tres
   frases) con su localización, nunca páginas enteras.

---

## 2. ¿«Una pequeña IA adaptada» o RAG?

Merece una respuesta clara antes de elegir el camino.

### 2.1 Entrenar (o afinar) un modelo con los tomos — **no recomendado**

- **Coste**: afinar un modelo pequeño (3-7 B parámetros) con 14 M de
  palabras exige GPU durante horas y repetirlo cada vez que añadas
  tomos. En CPU no es viable.
- **Peor resultado para tu objetivo**: un modelo afinado *aprende
  estilo*, no memoriza pasajes de forma fiable. Preguntar «¿qué tomo
  habla de los lacedemonios?» a un modelo afinado da una respuesta
  plausible y **sin cita comprobable**; es exactamente el fallo que hay
  que evitar en una biblioteca.
- **No se puede corregir**: si se equivoca, no hay dónde mirar.

### 2.2 RAG (recuperar y luego redactar) — **el camino**

Se busca primero en el texto real, y solo se redacta a partir de los
pasajes encontrados. La respuesta **siempre** viene con su cita, y si no
se encuentra nada, se dice. Es lo que encaja con un corpus que ya está
troceado por hoja, obra y sección.

### 2.3 Dónde sí cabe una IA pequeña y local

No para «saberse los libros», sino como **piezas del buscador**:

| Pieza | Qué hace | Tamaño | Recomendación |
|---|---|---|---|
| Modelo de *embeddings* (multilingual-e5-small o similar, ONNX int8) | Convierte cada pasaje en un vector para buscar por SIGNIFICADO, no por palabra exacta | ~120 MB | **Sí**, es la que aporta de verdad |
| *Reranker* (cross-encoder pequeño, ONNX) | Reordena los 50 mejores candidatos | ~90 MB | Opcional, mejora la precisión |
| LLM local (Qwen/Llama 3-4 B cuantizado, llama.cpp) | Redacta la respuesta a partir de los pasajes | 2-4 GB | Única vía para redactar sin clave de API; lento en CPU |

**Recomendación**: todo el motor en local. Para *encontrar*, FTS5 +
embeddings ONNX (cero coste, sin conexión, sin clave). Para *redactar*
la respuesta no hay clave de API disponible (2026-08-04: el usuario no
tiene clave de OpenAI, así que el botón «Describir» de Colección
tampoco está en uso), de modo que RAG-5 se plantea con LLM local o,
mejor todavía, se pospone: los pasajes con su cita ya responden.

### 2.4 Comparativa de motores de búsqueda

| Enfoque | Encuentra | Coste | Depende de la red | Veredicto |
|---|---|---|---|---|
| **FTS5 de SQLite** (léxico, BM25) | Palabras y frases exactas | 0 € | No | **Base obligatoria** |
| Índice de nombres ya extraído | Nombres propios con su cita canónica | 0 € | No | **Gratis, ya está hecho** |
| **Embeddings locales (ONNX)** | Sinónimos, paráfrasis, ideas | 0 € (≈40 min de CPU una vez) | No | **Recomendado** |
| Embeddings de OpenAI | Igual, algo mejor | ≈0,40 € una vez (19 M tokens) | Sí, y sube el texto | **Descartada**: no hay clave, y subiría el corpus |
| Vector DB externa (Chroma, FAISS…) | — | 0 € | No | **Innecesaria**: 100 k vectores caben en un `.npy` de 150 MB |

---

## 3. Arquitectura propuesta

```
BDtomos/TextosTomos/*.jsonl        (fuente, ya existe: 108 MB)
            │
            ├─► textos.db  (SQLite aparte, NO tc_monitor.db)
            │     ├── pasajes         ~40.000 filas (hojas de texto troceadas)
            │     ├── pasajes_fts     índice FTS5 (BM25)
            │     └── nombres         25.342 entradas del índice del traductor
            │
            └─► vectores.npy + vectores.idx   (embeddings, fase 3)

Consulta ──► 1. nombres (respuesta exacta si la hay)
             2. FTS5   (BM25, palabras y frases)
             3. vectores (significado)
             └─► fusión RRF ──► 8 pasajes ──► respuesta con citas
```

**Decisiones de diseño y su porqué**

- **Base de datos aparte** (`BDtomos/textos.db`): `tc_monitor.db` es
  pequeña, se copia y se respalda; el índice de textos rondará los
  200 MB y se puede **reconstruir** desde los `.jsonl` en cualquier
  momento. No mezclar.
- **Pasaje ≠ hoja**: una hoja de la edición digital tiene ~450 palabras
  y una nota final tiene 20. Se troceará en pasajes de ~180 palabras con
  solape de 40, respetando el renglón. Cada pasaje conserva **tomo,
  obra, sección, hoja, página impresa y referencias de margen**: sin eso
  no hay cita.
- **Las notas finales se indexan aparte** (66.781 hojas): son el
  aparato crítico y muchas veces responden mejor que el texto, pero no
  deben tapar el texto del autor en los resultados.
- **Nada de servidores**: todo son archivos locales; el buscador vive
  dentro de la aplicación.

---

## 4. Tareas

Cada tarea es independiente y termina en algo que puedes probar. El
**prompt de activación** es lo que tienes que escribir para lanzarla;
antes de tocar nada se te explicará qué implica y se pedirá tu OK.

---

### RAG-1 · Construir el índice literal (FTS5) — HECHA (2026-08-04)

**Prompt**: `RAG-1`

**Qué hace**: crea `BDtomos/textos.db` con los pasajes troceados de los
tomos y su índice FTS5, con reconstrucción **incremental** (solo relee
los `.jsonl` nuevos o cambiados, por fecha y tamaño).

**Por qué primero**: es la base gratuita, sin dependencias nuevas
(FTS5 viene en el SQLite de Python) y ya resuelve las búsquedas por
palabra exacta, que son la mayoría.

**Entregado**: `app/rag.py` + `tests/test_rag.py` (41 pruebas).

**Medido sobre el corpus real** (172 tomos, no 132: se siguieron
analizando PDF mientras tanto):

| | |
|---|---|
| Tomos indexados | 172 |
| Pasajes | 240.249 |
| Nombres del traductor | 30.793 |
| Tamaño de `textos.db` | 250 MB |
| Construcción completa | 42 s |
| Una consulta | 4-25 ms |

Decisiones que no estaban en el plan y salieron de medir:

- **Las hojas de rótulo hundían el ranking**. BM25 divide por la
  longitud del pasaje, así que «LVIII / AQUILES» (dos palabras) salía
  por delante del canto entero donde Aquiles habla — los CUATRO
  primeros resultados de «Aquiles» eran rótulos. Se corrige con un peso
  por longitud (`_PESO_LARGO`); los rótulos siguen ahí, pero detrás.
- **Las palabras vacías estropeaban la consulta**. «el alma es
  inmortal» exigía también «el» y «es», y eso premiaba pasajes largos
  llenos de artículos. Se quitan de las palabras sueltas, nunca de lo
  que va entre comillas.
- **El troceado respeta el renglón**: en los poetas es el verso y es la
  unidad de cita. Se parte por palabras CON su separador pegado, así el
  salto de línea queda donde estaba.
- **Basura del índice de nombres**: el OCR cuela «y ss» y romanos
  sueltos partidos («XV I»). Se filtran al indexar.
- **La gente PREGUNTA, no teclea palabras clave**. «¿Qué tomo habla de
  los lacedemonios?» exigía las cinco palabras y devolvía CERO. Dos
  arreglos: (1) en una consulta con forma de pregunta se cae el andamio
  («tomo», «habla», «dice», «aparece»…) — fuera de una pregunta, «libro»
  y «dice» son palabras de los tomos y se respetan; (2) si aun así no
  hay ningún pasaje con todas, se sueltan las palabras más COMUNES y se
  siguen exigiendo las raras, y la ventana dice con cuáles buscó. Se
  probó antes con OR y era peor: mandaba BM25 y ganaba quien repetía
  mucho «tomó», sin un solo lacedemonio.

---

### RAG-2 · Buscador de textos en la interfaz — HECHA (2026-08-04)

**Prompt**: `RAG-2`

**Qué hace**: ventana «Buscar en los textos» con la estética de la app
(FramelessDialog + GlowTable + GlowHeader + GlowProgress): campo de
búsqueda, casilla «Con notas», resultados con la cita completa y el
pasaje recortado alrededor de lo buscado. Doble clic abre la **hoja
entera** con lo buscado resaltado en oro y un botón para copiar la cita.

**Entregado**: `BuscarTextosDialog` y `PasajeDialog` en `app/gui.py`,
más `_IndexarWorker` (el indexado va en su hilo: 42 s congelarían la
ventana). Se llega desde dos sitios: la línea «Buscar en los textos» de
la ventana principal y el enlace `bcgmonitor://buscar`. (El botón
«Buscar dentro» de Textos se retiró el 2026-08-09: repetía lo que ya
hace la ventana principal.)

**Medido**: abrir la ventana y buscar sobre los 240.249 pasajes tarda
0,56 s.

**Revisión del 2026-08-05 — resultados COMPLETOS y carga perezosa**

Probando con «lacedemonios» faltaban tomos enteros (las obras menores
de Jenofonte, con 27 pasajes). Tres topes se sumaban: 60 resultados, 3
pasajes por tomo y —el grave— 400 candidatos de FTS antes de reordenar;
como esa palabra sale en 2.510 pasajes, los tomos que no entraban en
esos 400 no aparecían jamás.

Rehecho en dos niveles:

1. La lista es ahora de **tomos**, y es completa: `tomos_con()` hace un
   GROUP BY sin ningún tope. «Lacedemonios» → **105 tomos, 2.143
   pasajes, 83 ms**, con Jenofonte — Helénicas (262) arriba y las obras
   menores (27) en su sitio.
2. Los **pasajes de un tomo se cargan al desplegarlo** con doble clic
   (23-134 pasajes en 30-130 ms). Con tres tomos o menos se abren
   solos; con más, ninguno — cargar el texto de 105 tomos para leer uno
   era justo lo que había que evitar.

Y una lección de rendimiento: contar por tomo obligaba a leer la fila
entera de cada pasaje, y la fila lleva el TEXTO. Con un índice de
cobertura `(id, tomo_id, clase, hoja)`, «hombre» (15.433 pasajes) pasó
de **4,1 s a 135 ms** con el disco frío. El índice creció de 250 a
256 MB.

Detalles que importan:

- La cita SIEMPRE prefiere la **página impresa**; si el tomo es una
  edición digital sin folio, manda el verso o el parágrafo, y solo como
  último recurso se dice «hoja N del PDF».
- La hoja entera se pide al índice, no al `.jsonl`: abrir un archivo de
  100 MB desde la interfaz es justo lo que se quitó el 2026-07-31.
- Los pasajes se solapan 40 palabras a propósito; al recomponer la hoja
  se retira el solape, y en la lista se devuelve **una hoja por
  resultado** y como mucho tres por tomo.

---

### RAG-3 · La concordancia del traductor como respuesta directa — HECHA (2026-08-04)

**Prompt**: `RAG-3`

**Qué hace**: aprovecha las 30.793 entradas de índice de nombres ya
extraídas. Si la consulta es un nombre propio («Alcibíades»,
«lacedemonios»), encima de los pasajes aparece **la respuesta del
propio traductor**, con su cita canónica (`XXII 330`, `XVI 4, 2`,
`III 189`) y ordenada por número de citas. Doble clic en un tomo de esa
tabla lista los pasajes de ESE tomo.

**Por qué**: es información verificada por el editor del tomo y no
cuesta nada; es la respuesta de más calidad que puede dar el sistema.

**Entregado**: tabla `nombres` en `textos.db` + panel superior de la
ventana. Ejemplo real: «Aquiles» → Homero, Ilíada (160 citas), Ovidio,
Metamorfosis XI-XV (36), Dion de Prusa (21)…

**Nota**: el panel solo aparece con consultas de hasta tres palabras.
Nadie indexa una frase entera como nombre propio.

---

### RAG-4 · Búsqueda por significado (embeddings locales)

**Prompt**: `RAG-4`

**Qué hace**: añade `onnxruntime` + `tokenizers` (≈60 MB de
dependencias, sin PyTorch), descarga un modelo multilingüe pequeño
(≈120 MB) y calcula el vector de cada pasaje. La búsqueda pasa a ser
híbrida: FTS5 + vectores, fusionados con *Reciprocal Rank Fusion*.

**Por qué**: es lo que permite encontrar «costumbres de los espartanos»
en un texto que dice «usos de los lacedemonios». Sin esto, el buscador
solo encuentra lo que escribas literalmente.

**Entrega**: `app/rag_vectores.py`, vectores en `.npy`, y una
comparación medida de diez consultas con y sin vectores.

**Antes de aceptar, ten en cuenta**: ~40 minutos de CPU la primera vez
(en segundo plano, cancelable), +180 MB en disco y +60 MB en el `.exe`
empaquetado. Se puede posponer sin bloquear nada.

---

### RAG-5 · Respuesta redactada con citas · APLAZADA

**Prompt**: `RAG-5`

**Estado (2026-08-04)**: sin clave de API. Esta tarea es la ÚNICA del
plan que necesitaba una, así que queda aplazada; nada más depende de
ella. Se documenta para no perder el diseño. (El 2026-08-05 se retiró
además de la interfaz el botón «Describir», que era el otro sitio donde
se usaba la clave.)

**Qué hace**: sobre los 8 pasajes recuperados, redacta una respuesta de
cuatro o cinco líneas **con las citas al lado**. Si la recuperación no
encuentra nada suficientemente bueno, dice que no lo sabe: prohibido
inventar. El redactor NUNCA aporta conocimiento propio — solo reordena
en prosa lo que dicen los pasajes recuperados.

**Por qué**: convierte «aquí tienes 8 pasajes» en «el tomo 75 de
Jenofonte trata las costumbres espartanas en su *República de los
lacedemonios*, cap. 2».

**Dos vías, ninguna disponible hoy sin decidir antes**:

1. **LLM local** (`llama.cpp` + Qwen 2.5 3B o Llama 3.2 3B cuantizado
   a Q4): 2-4 GB en disco, ~10-20 s por respuesta en CPU, sin conexión
   y sin coste. Es la vía coherente con el resto del plan, pero
   multiplica por veinte el tamaño del programa empaquetado: hay que
   distribuirlo como descarga aparte, no dentro del .exe.
2. **API de pago** (OpenAI u otra): céntimos por consulta, solo se
   envían los pasajes (~3.000 tokens), nunca el corpus. Requiere una
   clave; `app/ai.py` ya tiene el cifrado DPAPI y la validación de
   respuestas hechos, así que sería trabajo menor.

**Entrega**: `app/rag_respuesta.py` + el bloque de respuesta en la
ventana de búsqueda.

**Recomendación**: no hacerla todavía. Con RAG-2 y RAG-3 ves el pasaje
literal con su tomo, obra y página impresa — que para citar es MÁS
fiable que una redacción. Se retoma si al usarlo echas de menos el
resumen.

---

### RAG-6 · Mantenimiento y calidad

**Prompt**: `RAG-6`

**Qué hace**: reindexado automático al analizar un PDF nuevo o al pulsar
«Revisar» en Textos; un banco de 25 preguntas reales con su respuesta
esperada para medir si el buscador mejora o empeora con cada cambio; y
un informe de cobertura (qué tomos faltan por extraer).

**Por qué**: sin una medida objetiva, cada ajuste del buscador es a
ciegas.

**Entrega**: `tests/test_rag.py` + un botón de reconstrucción en la
ventana de Textos.

---

## 5. Orden recomendado y esfuerzo

| Tarea | Depende de | Esfuerzo | Valor que aporta |
|---|---|---|---|
| RAG-1 | — | medio | **HECHA** 2026-08-04 |
| RAG-2 | RAG-1 | medio | **HECHA** 2026-08-04 |
| RAG-3 | RAG-1 | bajo | **HECHA** 2026-08-04 |
| RAG-4 | RAG-1 | alto | Medio-alto: encuentra por significado |
| RAG-5 | RAG-1 (mejor con 4) | medio | Aplazada: sin clave de API |
| RAG-6 | cualquiera | bajo | Alto a medio plazo |

**Camino corto recomendado**: RAG-1 → RAG-2 → RAG-3. Con eso ya
respondes tu pregunta de ejemplo, sin ninguna dependencia nueva, sin
coste, sin conexión y sin clave de API. **Hecho el 2026-08-04.** RAG-4
se decide después, viendo lo que falla en el uso real; RAG-5 queda
aplazada.

### Lo que ya se ve que falla (para decidir RAG-4)

- La búsqueda es LITERAL, y el castellano flexiona mucho: «cómo se
  educaba a los niños en Esparta» se queda en los 27 pasajes que dicen
  literalmente «educaba» y no ve los que dicen «educación», «criaban» o
  «crianza». Es el techo del método y es exactamente lo que arreglan
  los embeddings de RAG-4.
- Alguna cita hereda un rótulo equivocado de la extracción («Tomo 150 ·
  a) Ediciones · pág. 523» en un pasaje de la Ilíada): el fallo está en
  las secciones deducidas de `pdftext`, no en el buscador. La página
  impresa sí es correcta.

---

## 6. Lo que este plan NO hará

- No subirá tus textos completos a ningún servicio. De hecho, el camino
  recomendado (RAG-1 a RAG-4) **no envía nada a ninguna parte ni
  necesita clave de API**: funciona entero sin conexión.
- No mostrará páginas enteras: citas de dos o tres frases con su
  localización.
- No tocará `tc_monitor.db` ni los `.jsonl` originales (el índice es
  derivado y reconstruible).
- No añadirá dependencias pesadas sin avisarte del coste en disco, en
  tiempo y en el empaquetado.
