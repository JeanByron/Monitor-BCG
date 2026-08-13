# Evolución del Proyecto
## Monitor Inteligente de Descuentos para Biblioteca Clásica Gredos (Todocolección)

**Versión del documento:** 1.0  
**Estado del proyecto:** Arquitectura estable con integración continua de casos reales

---

# 1. Objetivo inicial

El proyecto nació con un objetivo muy concreto:

> Recibir una notificación inmediata en Windows cuando un libro de la colección Biblioteca Clásica Gredos almacenado en favoritos de Todocolección recibiera un descuento importante (por defecto ≥ 50%).

Desde el principio se tomó una decisión arquitectónica importante:

- **No utilizar Web Scraping.**
- Utilizar exclusivamente los correos electrónicos enviados oficialmente por Todocolección.

Esta decisión aporta numerosas ventajas:

- menor complejidad
- menor consumo de recursos
- independencia frente a cambios del sitio web
- utilización del canal oficial de notificaciones

---

# 2. Arquitectura inicial

Se diseñó una arquitectura modular dividida en responsabilidades independientes.

```
main.py
│
├── gui.py
├── imap_monitor.py
├── utils.py
├── notification.py
├── database.py
├── config.py
└── debug_panel.py
```

Cada módulo tiene una única responsabilidad, facilitando el mantenimiento y futuras ampliaciones.

---

# 3. Monitor IMAP en tiempo real

Inicialmente se planteó un sistema basado en sondeos periódicos.

Posteriormente evolucionó hacia una arquitectura basada en **IMAP IDLE**, permitiendo que el servidor de correo notificara automáticamente la llegada de nuevos mensajes.

Flujo:

```
Servidor IMAP
        │
        ▼
 IMAP IDLE
        │
        ▼
Nuevo correo
        │
        ▼
Parser
        │
        ▼
Notificación Windows
```

Ventajas:

- menor latencia
- menor consumo de red
- menor carga del servidor
- funcionamiento prácticamente en tiempo real

---

# 4. Evolución del parser

## Primera versión

La primera implementación de `utils.py` utilizaba principalmente:

- expresiones regulares
- heurísticas
- búsqueda de enlaces
- extracción simple de precios

Era funcional, pero dependía demasiado del formato del correo.

---

## Segunda versión

Se rediseñó completamente el parser utilizando una estrategia de extracción por niveles.

```
Parser HTML especializado
          │
          ▼
Parser semántico
          │
          ▼
Expresiones regulares
          │
          ▼
Heurísticas
```

Cada campo se obtiene utilizando la mejor estrategia disponible.

---

## Extractores especializados

Se dividió la lógica en pequeños componentes independientes:

- extract_title_from_html()
- extract_prices_from_html()
- extract_discount_from_html()
- extract_cta_link()
- extract_cover_image()

Esta separación redujo considerablemente el acoplamiento interno del parser.

---

# 5. Introducción de BeautifulSoup

Se incorporó BeautifulSoup como analizador HTML especializado.

Su incorporación permitió:

- localizar elementos mediante estructura HTML
- detectar enlaces reales del anuncio
- localizar imágenes de portada
- identificar precios tachados
- detectar texto destacado

La dependencia quedó implementada como opcional para mantener la compatibilidad del proyecto.

---

# 6. Sistema de confianza

Se añadió una puntuación de confianza (`confidence`) para evaluar la calidad del resultado obtenido.

La puntuación se calcula ponderando:

| Campo | Peso |
|--------|------|
| enlace | 0.25 |
| precio anterior | 0.20 |
| precio nuevo | 0.20 |
| descuento | 0.20 |
| título | 0.15 |

Si la confianza es inferior al umbral establecido, el sistema registra una advertencia en el log.

---

# 7. Trazabilidad del parser

Cada dato extraído incorpora ahora información sobre su procedencia.

Ejemplo:

```
Título:
HTML especializado

Precio:
HTML especializado

Descuento:
Parser semántico

Enlace:
Botón "Ver"
```

Esto facilita enormemente el diagnóstico cuando aparece un nuevo formato de correo.

---

# 8. Detección del primer fallo real

La primera prueba con un correo real de Todocolección reveló un error importante.

El parser obtenía:

```
Título:

Cancelar tu suscripción
```

en lugar del título del libro.

Este error nunca había aparecido con los ejemplos sintéticos.

---

# 9. Corrección del extractor de títulos

Se rediseñó completamente `extract_title_from_html()`.

Las mejoras incluyen:

- validación del enlace
- identificación del enlace real del anuncio
- descarte de enlaces de navegación
- lista negra de palabras prohibidas
- validación final del resultado
- utilización del asunto como último recurso

Tras la corrección:

```
DÉCIMO MAGNO AUSONIO
```

fue identificado correctamente.

---

# 10. Cobertura mediante casos reales

Se decidió abandonar la dependencia exclusiva de correos sintéticos.

Cada correo real exportado desde Gmail pasa a formar parte del conjunto de pruebas.

Esto convierte el comportamiento real de Todocolección en el principal mecanismo de validación.

---

# 11. Sistema de regresión

Se incorporó una batería automática de pruebas utilizando **pytest**.

Cada correo almacenado en:

```
tests/emails/
```

es procesado automáticamente.

El parser verifica:

- título
- precios
- descuento
- enlace
- portada
- confianza

Con ello se evita que futuras modificaciones rompan funcionalidades ya verificadas.

---

# 12. Archivos expected.json

Se introdujo el concepto de "caso esperado".

Cada correo puede disponer de un archivo:

```
correo.expected.json
```

que contiene los valores correctos esperados.

Esto permite comparar automáticamente:

```
Parser

VS

Resultado esperado
```

garantizando estabilidad en futuras versiones.

---

# 13. Generador automático de expected.json

Para evitar escribir manualmente los archivos JSON se desarrolló:

```
generate_expected.py
```

Su funcionamiento:

```
Correo
      │
      ▼
Parser
      │
      ▼
Usuario valida (S/N)
      │
      ▼
Generación automática del expected.json
```

Esto reduce considerablemente el tiempo necesario para ampliar la batería de pruebas.

---

# 14. Panel de depuración

Se desarrolló un panel independiente (`debug_panel.py`) para inspeccionar visualmente el comportamiento del parser.

Permite visualizar:

- asunto
- remitente
- título
- precios
- descuento
- portada
- confianza
- estrategia utilizada en cada campo

Además incorpora:

```
Reprocesar último correo
```

sin necesidad de reiniciar la aplicación.

---

# 15. Conservación automática del último correo

El monitor IMAP guarda automáticamente el último mensaje recibido mediante:

```
save_last_email(raw_bytes)
```

Esto permite que el panel de depuración trabaje siempre sobre el último correo procesado.

---

# 16. Integración continua del conocimiento

Cada nuevo correo real puede incorporarse al proyecto siguiendo el flujo:

```
Nuevo correo
        │
        ▼
Monitor IMAP
        │
        ▼
last_email.eml
        │
        ▼
Depuración
        │
        ▼
generate_expected.py
        │
        ▼
expected.json
        │
        ▼
pytest
```

Este mecanismo constituye un proceso continuo de fortalecimiento del parser.

---

# 17. Propuesta de aprendizaje continuo

Como evolución natural del proyecto se propuso incorporar un sistema de retroalimentación automática.

Objetivos:

- almacenar automáticamente nuevos correos
- evitar duplicados mediante Message-ID o SHA-256
- mantener un conjunto de pruebas actualizado
- eliminar automáticamente los casos más antiguos cuando se supere un umbral configurable (por ejemplo, conservar entre 30 y 50 correos recientes)
- generar estadísticas de cobertura del parser

Este enfoque no constituye Machine Learning, sino un sistema de **mejora continua basada en evidencia**, donde los casos reales alimentan de forma permanente la batería de pruebas.

---

# 18. Estado actual del proyecto

Actualmente el sistema dispone de:

- ✔ Arquitectura modular
- ✔ Monitor IMAP IDLE
- ✔ Notificaciones nativas de Windows
- ✔ Parser HTML especializado
- ✔ Parser semántico
- ✔ Expresiones regulares como respaldo
- ✔ Heurísticas finales
- ✔ Sistema de confianza
- ✔ Registro detallado de estrategias
- ✔ Panel de depuración
- ✔ Conservación automática del último correo
- ✔ Base de datos SQLite
- ✔ Historial de alertas
- ✔ Registro de eventos (`log.txt`)
- ✔ Generador automático de casos esperados
- ✔ Batería de pruebas automatizada con `pytest`
- ✔ Casos sintéticos y casos reales
- ✔ Arquitectura preparada para evolución futura

---

# 19. Conclusión

La evolución del proyecto transformó una herramienta inicialmente orientada a mostrar notificaciones de descuentos en una plataforma robusta para el procesamiento automático de correos electrónicos de Todocolección.

Las mejoras introducidas no se limitaron a incrementar funcionalidades, sino que fortalecieron aspectos fundamentales de la ingeniería del software: modularidad, trazabilidad, validación continua, pruebas de regresión y mantenibilidad.

La incorporación de correos reales como fuente permanente de validación, junto con la automatización de la generación de casos esperados y la propuesta de un ciclo de retroalimentación continua, sitúan el proyecto en un nivel de madurez propio de aplicaciones orientadas a la evolución a largo plazo, donde cada nuevo caso real contribuye a aumentar la fiabilidad del sistema sin comprometer su arquitectura.