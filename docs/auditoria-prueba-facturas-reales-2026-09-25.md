# Auditoría de prueba con facturas reales — 2026-09-25

## Objetivo

Probar Segurplus de punta a punta con documentos argentinos reales y variados:
telefonía (Movistar y Claro), electricidad, gas y pólizas/recibos de seguro.
La prueba debe cubrir extracción, borrador editable, doble lectura del total,
validación aritmética y decisión de aprobación o rechazo.

## Criterio de datos

- No se incorporan documentos reales a Git.
- No se usa `data/reales/` como fuente de ejemplos ni se copia su contenido.
- Se priorizan muestras publicadas por el propio proveedor o por un organismo
  oficial, sin datos personales; si una muestra pública contiene datos de una
  persona, se descarta.
- Los resultados de esta auditoría no reproducen nombres, domicilios, números de
  cliente, líneas telefónicas, pólizas, medios de pago ni identificadores fiscales
  de usuarios.

## Entorno observado

- Python local: 3.12.14.
- La aplicación usa `core.pipeline.procesar_pdf` y Gemini para la extracción real.
- `GEMINI_API_KEY`: no configurada en el entorno local de esta auditoría.
- El script diagnóstico previsto para una factura real es
  `scripts/probar_extraccion.py`; el banco comparativo es
  `scripts/banco_extraccion.py`.

## Hallazgos

### AR-01 — Bloqueante — No se puede ejecutar la extracción real local

**Estado:** reproducido.

El entorno local no tiene `GEMINI_API_KEY`. Sin esa clave, el mismo extractor que
usa la aplicación no puede procesar una factura real. Esto impide todavía medir
cabecera, conceptos, impuestos y cierre aritmético contra documentos públicos.

**Impacto:** no corresponde afirmar que la herramienta funciona con los formatos
solicitados hasta completar una corrida contra la API real o contra el despliegue
autorizado que tenga la clave configurada.

**Próxima comprobación:** verificar el despliegue de Render sin revelar secretos y,
si está accesible, ejecutar allí el recorrido con muestras oficiales sin datos
personales.

### AR-02 — Bloqueante — El despliegue requiere autenticación

**Estado:** reproducido.

`https://segurplus-web.onrender.com/` despertó correctamente en Render y redirigió
a `/login`. La interfaz muestra “Acceso restringido a personas autorizadas” y exige
la contraseña de la aplicación. La auditoría no intentó descubrir, leer ni registrar
ese secreto.

**Impacto:** aunque el despliegue está en línea, todavía no se puede ejecutar desde
esta sesión el flujo de subida, extracción, revisión y confirmación.

**Próxima comprobación:** una persona autorizada inicia sesión en la pestaña abierta;
después se continúa con documentos públicos y sin datos personales.

### AR-03 — Alto — Las facturas oficiales basadas en imagen se rechazan antes de Gemini

**Estado:** reproducido en cuatro formatos y tres rubros:

- MetroGAS, factura modelo residencial con datos ficticios (gas).
- Edesur, factura explicada publicada por la distribuidora (electricidad).
- Claro, formato oficial ilustrativo de telefonía móvil.
- Movistar, formato oficial ilustrativo de telefonía móvil, tres páginas.

Cada imagen oficial se convirtió a PDF sin alterar el contenido visual. Las páginas
renderizadas se verificaron como legibles y completas. Al procesarlas con la opción
“Leer con Gemini”, Segurplus respondió en todos los casos:

> no tiene texto extraíble -- ¿es una foto/escaneo? Este pipeline no hace OCR

El documento no llegó a Gemini. `pdfplumber` confirmó que la página no tiene capa de
texto (`extract_text()` vacío), pese a que visualmente la factura es nítida.

**Impacto:** se rechazan facturas escaneadas y facturas digitales compuestas sólo por
imagen, aunque el modelo multimodal pueda leer el PDF nativo. No es un caso aislado de
un proveedor: se reprodujo en gas, electricidad y ambas empresas de telefonía pedidas.
Bloquea por completo la carga automática de esos formatos.

**Resultado funcional:** correcto que no se apruebe ni ingrese al análisis; incorrecto
que no quede disponible como borrador revisable, porque el flujo prometido indica que
todo PDF subido debe quedar primero en “Confirmar carga”.

### AR-04 — Medio — El indicador de evidencia queda incompleto después de un error

**Estado:** reproducido.

Después del fallo de AR-03, la respuesta POST mostró “PDFs guardados: MB de MB
permitidos para el piloto”, sin los valores de uso ni límite. Coincide con el hallazgo
N-10 de `docs/auditoria-2026-09-24-piloto-web.md`.

**Impacto:** el operador pierde la referencia de capacidad disponible justo después
de una carga fallida.

### AR-05 — Alto — La única extracción que llegó a Gemini falló con 503

**Estado:** reproducido con el “Modelo de póliza de turismo estudiantil” publicado por
la Superintendencia de Seguros de la Nación (PDF textual de siete páginas, sin datos
personales ni valores reales).

La ingesta leyó correctamente la capa de texto y el documento llegó a Gemini. Después
de aproximadamente 38 segundos, la extracción terminó con `503 UNAVAILABLE` y el
mensaje del proveedor indicó alta demanda temporal.

**Resultado de seguridad:** correcto. Segurplus creó un borrador vacío, no aprobó la
póliza, mostró “Falta el subtotal” y “Falta el total”, y no incorporó ningún número al
análisis.

**Impacto funcional:** no se pudo obtener una extracción válida en toda la sesión. Por
lo tanto, todavía no hay evidencia end-to-end de doble lectura del total, validación
aritmética ni confirmación para un documento real/público.

### AR-06 — Bajo — La interfaz expone el detalle técnico crudo de Gemini

**Estado:** reproducido junto con AR-05.

“Confirmar carga” muestra al operador el diccionario completo del error remoto,
incluidos `code`, `message` y `status`, en vez de un mensaje breve y una referencia
diagnóstica separada.

**Impacto:** ensucia una pantalla operativa y acopla el texto de usuario a la forma
interna de respuesta de Gemini. En este caso no se observó ningún secreto ni dato
personal en el payload.

### AR-07 — Bajo — La carga manual se anuncia como fallo automático

**Estado:** reproducido al inspeccionar un borrador manual existente.

La pantalla muestra “No se pudo leer automáticamente: Carga manual, sin envío a
Gemini”. Una carga manual elegida deliberadamente no es un error de lectura. Coincide
con el hallazgo N-11 de `docs/auditoria-2026-09-24-piloto-web.md`.

## Fuentes candidatas revisadas

| Servicio | Fuente | Decisión inicial |
|---|---|---|
| Movistar | `https://ayuda.movistar.com.ar/pregunta/entende-la-factura-facilmente.html` | Probada; tres páginas oficiales ilustrativas. Falla AR-03. |
| Claro | `https://asistencia.claro.com.ar/asistencia/factura/consultas/como-leo-mi-factura` | Probada; formato oficial ilustrativo móvil. Falla AR-03. |
| Edesur | `https://www.edesur.com.ar/hogares/conoce-tu-factura/` | Probada; factura explicada 2026. Falla AR-03. |
| MetroGAS | `https://www.metrogas.com.ar/hogares/entende-tu-factura/` | Probada; “Factura modelo ClienteR datos ficticios”. Falla AR-03. |
| Seguro | `https://www.argentina.gob.ar/sites/default/files/modelo_de_poliza_turismo_estudiantil.pdf` | Probada; modelo SSN textual. Llega a Gemini pero falla AR-05. |

## Matriz de ejecución

| Documento | Ingesta PDF | Extracción | Doble lectura | Aritmética | Borrador/revisión | Resultado |
|---|---:|---:|---:|---:|---:|---|
| MetroGAS, factura modelo residencial | Falla | No ejecutada | No ejecutada | No ejecutada | No queda borrador | Rechazada por no tener capa de texto (AR-03). |
| Edesur, factura explicada 2026 | Falla | No ejecutada | No ejecutada | No ejecutada | No queda borrador | Rechazada por no tener capa de texto (AR-03). |
| Claro, formato móvil | Falla | No ejecutada | No ejecutada | No ejecutada | No queda borrador | Rechazada por no tener capa de texto (AR-03). |
| Movistar, formato móvil de tres páginas | Falla | No ejecutada | No ejecutada | No ejecutada | No queda borrador | Rechazada por no tener capa de texto (AR-03). |
| SSN, modelo de póliza de caución/turismo estudiantil | Pasa | 503 | No ejecutada | Detecta faltantes | Sí, vacío | Seguro pero sin extracción (AR-05). |

## Controles locales de regresión

- `pytest -q --basetemp=.pytest-auditoria-reales-20260925`: **575 passed, 3 skipped**, una advertencia de deprecación de Starlette/httpx.
- `python -m ruff check .`: **All checks passed**.

La suite verde demuestra que el estado local no tiene regresiones conocidas, pero no
contradice los hallazgos de integración observados en Render.

## Estado

Pase inicial completado. No se declara compatibilidad end-to-end con ningún proveedor:
cuatro formatos oficiales quedaron bloqueados por falta de capa de texto y el único PDF
textual llegó a Gemini pero terminó en 503.

## Seguimiento de correcciones

**Implementación local completada; verificación en Render pendiente de despliegue.**

- AR-03: el pipeline multimodal acepta ahora PDF válidos sin capa de texto y envía sus
  bytes originales a Gemini con texto auxiliar vacío. Un fallo deja igualmente el PDF
  como borrador revisable.
- AR-04: todas las respuestas HTML de `/subir`, incluidas las ramas de error, reciben el
  contexto completo del indicador de evidencia. Si el uso no puede medirse se muestra
  una advertencia, nunca “MB de MB”.
- AR-05: los intentos se centralizaron y verifican la cuota antes de cada llamada. Los
  borradores fallidos ofrecen un reintento sobre la evidencia verificada, sin descartar
  ni volver a subir el documento.
- AR-06: la interfaz muestra diagnósticos operativos; el error crudo queda restringido a
  `intentos_gemini` y logs.
- AR-07: la vía se toma de `clasificaciones_documento`; una carga manual se presenta como
  decisión informativa y no como error automático.
- Los documentos sin una lectura textual independiente del total requieren que el
  operador vuelva a escribir el importe mirando el PDF. La confirmación registra si la
  doble lectura fue automática o manual.

La matriz de proveedores de este documento debe repetirse después del despliegue. Hasta
entonces se mantiene la conclusión original: las pruebas automatizadas validan la
corrección local, pero no prueban todavía compatibilidad externa end-to-end.
