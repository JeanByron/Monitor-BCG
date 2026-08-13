# tests/emails/ — Correos reales para las pruebas de regresión

Guarda aquí los correos reales de Todocolección exportados desde Gmail.
La batería de `pytest` los carga **todos automáticamente**: no hay que
registrarlos en ningún sitio, basta con dejar el archivo `.eml` en esta
carpeta.

## Archivo automático (dataset.py)

Si el monitor IMAP está integrado con `DatasetManager` (ver dataset.py),
cada correo procesado se archiva aquí automáticamente con nombre
`YYYYMMDD-HHMMSS_<clave>.eml`, deduplicado por Message-ID o hash del
contenido. Los recién llegados quedan en la **cola de validación**
(sin `.expected.json`) hasta que los confirmes con
`python generate_expected.py`.

## Rotación y casos protegidos

Para que el repositorio no crezca sin límite, al superar los 50 correos
se eliminan automáticamente los 20 más antiguos (con su `.expected.json`),
quedando siempre entre 30 y 50 casos recientes. Cada borrado se anota en
`log.txt`.

Los archivos con prefijo **`keep_`** están PROTEGIDOS: la rotación nunca
los toca y no cuentan para el límite. Úsalo para casos de regresión
históricos que documentan bugs corregidos y no deben perderse.

## Estadísticas

`python dataset.py stats` muestra (y `dataset_stats.json` almacena)
los totales de procesados/archivados/validados/rotados, los casos de
regresión activos, la fecha del último entrenamiento y la **cobertura
por estrategia** (HTML especializado / semántico / regex / heurísticas):
si las heurísticas crecen, Todocolección está cambiando el formato.

## Cómo exportar un correo desde Gmail (manual)

1. Abre el correo en Gmail.
2. Menú de los tres puntos (arriba a la derecha del mensaje).
3. **"Descargar el mensaje"** (o "Mostrar original" → "Descargar original").
4. Guarda el archivo `.eml` en esta carpeta con un nombre descriptivo,
   por ejemplo: `2026-07-vidas-paralelas-90pct.eml` (o `keep_...` si
   quieres protegerlo de la rotación).

## Aserciones

Para cada `.eml` la batería comprueba automáticamente lo básico:
título no vacío, precio nuevo presente, enlace a todocoleccion.net y
confianza >= 0.6.

## Valores esperados exactos (opcional pero recomendado)

Si quieres fijar los valores correctos de un correo (regresión estricta),
crea junto al `.eml` un archivo con el mismo nombre y sufijo
`.expected.json`. Solo se comparan las claves que incluyas:

    2026-07-vidas-paralelas-90pct.eml
    2026-07-vidas-paralelas-90pct.expected.json

Contenido de ejemplo del JSON:

    {
      "title": "Plutarco. Vidas Paralelas II. Biblioteca Clásica Gredos 86",
      "old_price": 40.0,
      "new_price": 4.0,
      "discount_percent": 90.0,
      "link": "https://www.todocoleccion.net/...~x123456789",
      "cover_image_url": "https://images.todocoleccion.net/...jpg",
      "min_confidence": 0.9
    }

Claves admitidas: `title`, `old_price`, `new_price`, `discount_percent`,
`link`, `cover_image_url`, `min_confidence`. Los precios y porcentajes
se comparan con una pequeña tolerancia numérica.

## Informe

Tras cada ejecución de `pytest`, se imprime un informe por correo
(campos detectados, estrategia usada y confianza) y se guarda en
`tests/last_report.md`.
