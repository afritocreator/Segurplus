"""Tests de core/extraccion/proveedores/render.py (ADR-004). Usa una
fixture SINTÉTICA (nunca una factura real, ver CLAUDE.md)."""

from pathlib import Path

import pytest

from core.extraccion.gemini import ExtraccionError
from core.extraccion.proveedores.render import renderizar_paginas_png

pytest.importorskip("pymupdf")

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "docs" / "fixtures" / "sintetico" / "energia_2026-07.pdf"
)


def test_renderiza_una_pagina_por_pdf():
    paginas = renderizar_paginas_png(_FIXTURE.read_bytes())
    assert len(paginas) == 1
    # Firma de un PNG válido (los primeros 8 bytes son fijos por especificación).
    assert paginas[0].startswith(b"\x89PNG\r\n\x1a\n")


def test_pdf_corrupto_lanza_extraccion_error():
    with pytest.raises(ExtraccionError):
        renderizar_paginas_png(b"esto no es un PDF")
