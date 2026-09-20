# ADR-004: PyMuPDF para renderizar PDF a imagen, dependencia opcional

**Estado**: aceptado. **Fecha**: 2026-09-20.

## Contexto

El plan de rediseño de septiembre 2026 (ver `docs/banco_extraccion.md`) agrega una capa
de proveedores de extracción intercambiable (`core/extraccion/proveedores/`) para poder
medir, con el banco, si otro proveedor gratis lee mejor una factura que Gemini. Groq --
el único proveedor gratis del `Informe Técnico Semanal de APIs Gratuitas de Modelos de
Lenguaje` (18/09/2026) con modelos multimodales de verdad (Llama 4 Scout, Qwen 3.6) --
recibe **imágenes**, no un PDF nativo como Gemini. Hace falta renderizar cada página del
PDF a PNG antes de mandarlo.

## Decisión

Se usa **PyMuPDF** (`pymupdf`, importado como `fitz` internamente pero se usa el nombre
nuevo del paquete) para el render, como dependencia **opcional** (`pip install -e
".[vision]"`), no agregada a `requirements.txt` ni a las dependencias obligatorias del
proyecto.

## Por qué PyMuPDF y no `pdf2image`/Poppler

`pdf2image` es la opción más común en tutoriales, pero depende de tener el binario
`poppler-utils` instalado en el sistema operativo -- una dependencia externa a Python
que no se resuelve con `pip install`. En un deploy gratuito (Render, Hugging Face
Spaces) eso es una imagen de contenedor más compleja de armar y mantener, o directamente
no disponible. PyMuPDF es un paquete de Python autocontenido (los binarios de MuPDF
vienen empaquetados en el wheel), sin nada que instalar aparte -- mismo criterio que ya
se usa en el proyecto para preferir dependencias autocontenidas cuando existen.

## Por qué opcional y no obligatoria

El pipeline real (`core/pipeline.py::procesar_pdf`) **no la necesita**: sigue llamando
solo a Gemini, que recibe el PDF nativo (ver `core/extraccion/gemini.py`). Esta
dependencia solo hace falta para:

- `scripts/banco_extraccion.py` cuando se mide un proveedor con `acepta_imagen: true`
  (ver `data/extraccion.yaml`), y
- `core/extraccion/proveedores/openai_compat.py`, si algún día `procesar_pdf` migra a
  usar la cascada de proveedores (todavía no -- ver el comentario en
  `core/extraccion/proveedores/__init__.py`).

Agregarla a las dependencias obligatorias forzaría a instalarla en todos lados (incluido
el deploy real, que no la usa) solo para poder correr un script de medición manual.
`core/extraccion/proveedores/render.py` envuelve el `ImportError` en `ExtraccionError`
con un mensaje claro (`pip install pymupdf`) en vez de dejarlo escapar crudo.

## Consecuencia

`pymupdf` se agrega como extra `vision` en `pyproject.toml`, y también dentro de `dev`
(para que sus tests corran en la suite normal). No se agrega a `requirements.txt`
(la copia manual para Streamlit Community Cloud, que de todos modos se va a dejar de
usar -- ver Bloque 4 del plan de rediseño). Import diferido dentro de
`renderizar_paginas_png`, mismo criterio que `google.genai` en
`core/extraccion/gemini.py`.
