"""Tests de ingesta de PDF contra las fixtures sintéticas generadas por
docs/fixtures/generar_fixtures.py (nunca facturas reales, ver CLAUDE.md)."""

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from core.ingesta.pdf_texto import (
    PdfSinTextoError,
    extraer_texto,
    hash_archivo,
    total_impreso,
)

FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "fixtures" / "sintetico"


def test_extrae_texto_de_factura_de_telefonia():
    doc = extraer_texto(FIXTURES / "telefonia_2026-07.pdf")
    assert "Comunicaciones Sur" in doc.texto
    assert "Abono 4 líneas" in doc.texto


def test_total_impreso_calculado_a_mano():
    # subtotal 10.400,00 + IVA 21% (2.184,00) = 12.584,00 -- ver
    # docs/fixtures/generar_fixtures.py
    doc = extraer_texto(FIXTURES / "telefonia_2026-07.pdf")
    assert total_impreso(doc.texto) == pytest.approx(12584.00)


def test_total_impreso_de_factura_con_recargo():
    # subtotal 17.200,00 + IVA 3.612,00 + recargo 350,00 = 21.162,00
    doc = extraer_texto(FIXTURES / "telefonia_2026-08.pdf")
    assert total_impreso(doc.texto) == pytest.approx(21162.00)


def test_total_impreso_sin_coincidencia_devuelve_none():
    assert total_impreso("un texto cualquiera sin ningún total") is None


def test_hash_es_estable_y_determinista():
    ruta = FIXTURES / "telefonia_2026-07.pdf"
    assert hash_archivo(ruta) == hash_archivo(ruta)
    assert len(hash_archivo(ruta)) == 64  # sha256 hexdigest


def test_hash_distingue_archivos_distintos():
    h1 = hash_archivo(FIXTURES / "telefonia_2026-07.pdf")
    h2 = hash_archivo(FIXTURES / "energia_2026-07.pdf")
    assert h1 != h2


def test_pdf_sin_capa_de_texto_lanza_error_explicito(tmp_path):
    # PDF válido pero sin ningún texto dibujado -- simula una foto escaneada
    # sin OCR (no lo hacemos, ver docstring del módulo).
    ruta = tmp_path / "sin_texto.pdf"
    c = canvas.Canvas(str(ruta), pagesize=A4)
    c.showPage()
    c.save()

    with pytest.raises(PdfSinTextoError, match="no tiene texto extraíble"):
        extraer_texto(ruta)


# --- A-5: formatos de total que un ERP o proveedor real puede imprimir -----
# Antes del fix, el formato estadounidense (coma de miles, punto decimal)
# no devolvía None -- devolvía un valor INCORRECTO en silencio (la regex
# vieja matcheaba solo "12,58" de "12,584.00"). Estos tests fijan que los
# cinco formatos den el valor correcto, o None si genuinamente no hay match.


def test_total_impreso_formato_argentino_con_separador_de_miles():
    assert total_impreso("TOTAL A PAGAR: $ 12.584,00") == pytest.approx(12584.00)


def test_total_impreso_sin_decimales():
    assert total_impreso("TOTAL A PAGAR: $12584") == pytest.approx(12584.0)


def test_total_impreso_con_punto_decimal_sin_miles():
    assert total_impreso("TOTAL A PAGAR: 12584.00") == pytest.approx(12584.00)


def test_total_impreso_formato_estadounidense():
    # docs/auditoria-2026-09.md, hallazgo A-5: este es el caso que antes
    # devolvía 12.58 en silencio -- un error de más de cuatro órdenes de
    # magnitud, sin ninguna excepción.
    assert total_impreso("Total a Pagar $ 12,584.00") == pytest.approx(12584.00)


def test_total_impreso_sin_separador_de_miles_con_coma_decimal():
    assert total_impreso("TOTAL: 12584,00 ARS") == pytest.approx(12584.00)


def test_total_impreso_nunca_construye_un_valor_de_una_coincidencia_parcial():
    # El total real es 12.584,00 -- si alguna vez la regex volviera a
    # matchear solo un prefijo del número, este test lo detectaría porque
    # el valor esperado completo no coincidiría con un prefijo truncado.
    resultado = total_impreso("Total a Pagar $ 12,584.00")
    assert resultado != pytest.approx(12.58)
    assert resultado == pytest.approx(12584.00)
