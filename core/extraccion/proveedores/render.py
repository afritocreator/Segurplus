"""Renderiza un PDF a imágenes PNG, una por página -- para los proveedores
de `core/extraccion/proveedores/openai_compat.py` que solo aceptan imagen
(no reciben el PDF nativo como Gemini): Groq, por ejemplo, sirve modelos
multimodales (Llama 4 Scout, Qwen 3.6) que leen imágenes, no PDF.

Usa PyMuPDF (`pymupdf`) en vez de `pdf2image`/Poppler: es una dependencia
Python autocontenida (no necesita un binario del sistema operativo
instalado aparte), más simple de tener en un deploy gratuito -- ver
`docs/decisiones/ADR-004-render-pdf-a-imagen.md`. Import diferido, mismo
criterio que `core/extraccion/gemini.py` con `google.genai`: el resto del
pipeline no depende de tener esta librería instalada si nunca se usa un
proveedor que la necesite.
"""

from __future__ import annotations

from core.extraccion.gemini import ExtraccionError

# Mismo criterio que Gemini le da al PDF nativo: suficiente resolución para
# leer una tabla de impuestos chica sin inflar el tamaño de la imagen más
# de lo necesario para un proveedor gratuito con límites de tokens.
DPI_DEFAULT = 200


def renderizar_paginas_png(pdf_bytes: bytes, *, dpi: int = DPI_DEFAULT) -> list[bytes]:
    """Devuelve el contenido PNG de cada página del PDF, en orden. Lanza
    `ExtraccionError` (no una excepción de PyMuPDF sin envolver) si el PDF
    no se puede abrir o renderizar -- mismo tratamiento que cualquier otro
    fallo de lectura en esta capa."""
    try:
        import pymupdf  # noqa: PLC0415 -- import diferido, ver docstring del módulo
    except ImportError as exc:  # pragma: no cover -- depende de si está instalado
        raise ExtraccionError(
            "Falta la dependencia 'pymupdf' para renderizar el PDF a imagen "
            "(pip install pymupdf, ver ADR-004)."
        ) from exc

    try:
        documento = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        paginas_png = [pagina.get_pixmap(dpi=dpi).tobytes("png") for pagina in documento]
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo de PyMuPDF se envuelve
        raise ExtraccionError(f"No se pudo renderizar el PDF a imagen: {exc}") from exc

    if not paginas_png:
        raise ExtraccionError("El PDF no tiene páginas para renderizar.")
    return paginas_png
