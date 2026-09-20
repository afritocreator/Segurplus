# ADR-001: lectura de facturas con Gemini (tier gratuito), en vez de OCR local o Anthropic

**Estado**: aceptado. **Fecha**: 2026-09-09.

## Contexto

Necesitamos leer facturas de servicios en PDF (telefonía, energía, gas, agua, seguros,
alquileres) y convertirlas a datos estructurados, sin costo mientras se prueba la
herramienta.

## Decisión

Se reutiliza la investigación y el circuito ya construidos en `afritocreator/Kleric-`
(repo hermano, mismo dueño), documentados ahí en `docs/01-lectura-facturas-gratis.md`:
usar **Gemini Flash en tier gratuito** (`gemini-3.6-flash`, fijo, no el alias `latest`)
vía `google-genai`, pidiendo salida JSON contra un esquema (`response_json_schema`).

Se descartan, con el mismo razonamiento que en Kleric-:
- **Anthropic pago**: funciona, pero es la opción más cara de las evaluadas.
- **Mistral OCR**: perdió su tier gratis permanente en junio de 2026.
- **Tesseract / OCR local**: gratis y privado, pero es OCR plano — no entiende la
  estructura de una factura, y falla ante cualquier cambio de diseño del comprobante.

## Contrapartida aceptada

En el tier gratuito, Google puede usar el contenido para mejorar sus productos (política
pública, textual: *"Content used to improve our products"* vs. *"not used"* en el tier
pago). Para Segurplus esto queda sujeto a la decisión explícita de la dueña del negocio
(ver plan del proyecto) — la salida de emergencia es pasar al tier pago, que cuesta
centavos por mes con este volumen y no requiere cambiar código.

## Por qué esto es seguro igual: el modelo nunca es la última palabra

Ningún número que devuelve el modelo se muestra sin pasar antes por
`core/extraccion/validacion.py` (control aritmético determinístico, port de
`lib/invoice/validate.ts` de Kleric-) más la doble lectura del total contra el texto del
PDF (`core/ingesta/pdf_texto.py::total_impreso`). Si algo no cierra, la factura va a
cuarentena y no entra al análisis.

## Addendum (2026-09-20): banco de medición, primer resultado real (solo Gemini)

El plan de rediseño de septiembre 2026 (ver `docs/estado.md`) agregó
`scripts/banco_extraccion.py` y `core/extraccion/proveedores/` para medir, con datos y
no por opinión, si Gemini sigue siendo la mejor opción o si conviene reordenar la
cascada de `data/extraccion.yaml` (candidato evaluado: Groq, único proveedor gratis del
`Informe Técnico Semanal de APIs Gratuitas de Modelos de Lenguaje` del 18/09/2026 con
modelos multimodales de verdad). El mismo día se consiguió una `GEMINI_API_KEY` real y el
banco corrió por primera vez: sobre las 4 facturas reales (2 luz, 2 gas), 3 se leyeron sin
error -- promedio cabecera 93%, conceptos 83%, impuestos 92%, `concepto_sugerido` 100%. Las
dos facturas de luz cerraron perfecto; la de gas leída no cerró, coincidiendo con la
ambigüedad de layout ya documentada a mano en `data/reales/banco/gas_1.yaml` (detalle en
`docs/estado.md`). **Todavía no alcanza para decidir la cascada**: falta `GROQ_API_KEY`
para tener el otro lado de la comparación, y la muestra (3 facturas leídas, 1 con error de
servidor transitorio) es chica para separar "gas es difícil para cualquier proveedor" de
"esta corrida tuvo mala suerte". Gemini sigue siendo el único proveedor en producción
(`core/pipeline.py::procesar_pdf` no cambió). Cuando el banco corra también con Groq, el
resultado se publica en `docs/estado.md` y, si cambia la cascada, se actualiza acá.

## Consecuencias

- Cero costo de desarrollo y cero costo de producción con el volumen esperado.
- La calidad de extracción depende de un servicio externo fuera de nuestro control — si
  Google cambia límites o deprecia el modelo, hay que revisar la documentación vigente de
  Gemini antes de tocar `core/extraccion/gemini.py` (no confiar en la memoria del modelo
  que escribe el código, la propia API cambió de forma varias veces).
- Si en algún momento no se puede usar la nube, la Fase 6 del plan (plantillas por
  reglas, 100% locales) cubre los proveedores de mayor volumen sin depender de Gemini.
