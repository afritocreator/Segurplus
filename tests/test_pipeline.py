"""Test del pipeline completo de punta a punta contra las fixtures
sintéticas, con la llamada a Gemini reemplazada por monkeypatch (no pega a
la red -- lo que Gemini devolvería ya se conoce de memoria, porque las
fixtures se generaron con esos valores exactos, ver
docs/fixtures/generar_fixtures.py)."""

from pathlib import Path

import core.pipeline as pipeline_mod
from core.almacenamiento import conectar
from core.extraccion.esquema import Concepto, FacturaExtraida, Impuesto
from core.pipeline import procesar_pdf

FIXTURES = Path(__file__).resolve().parent.parent / "docs" / "fixtures" / "sintetico"


def _factura_telefonia_julio() -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio="telefonia",
        periodo_desde="2026-07-01",
        periodo_hasta="2026-07-31",
        fecha_emision="2026-07-05",
        fecha_vencimiento=None,
        numero_comprobante="0001-00045501",
        moneda="ARS",
        conceptos=[
            Concepto("Abono 4 líneas móviles", 4, "línea", 2500.0, 10000.0),
            Concepto("Consumo de datos adicional", 8, "GB", 50.0, 400.0),
        ],
        impuestos=[Impuesto("IVA 21%", importe=2184.0)],
        subtotal=10400.0,
        total=12584.0,
    )


def _factura_rota() -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio="telefonia",
        periodo_desde="2026-09-01",
        periodo_hasta="2026-09-30",
        fecha_emision="2026-09-05",
        fecha_vencimiento=None,
        numero_comprobante="0001-00099999",
        moneda="ARS",
        conceptos=[Concepto("Abono 5 líneas móviles", 5, "línea", 100.0, 800.0)],  # debería ser 500
        impuestos=[Impuesto("IVA 21%", importe=168.0)],
        subtotal=800.0,
        total=968.0,
    )


def test_factura_valida_se_guarda(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "guardada"
    fila = con.execute(
        "SELECT emisor, total FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila == ("Comunicaciones Sur S.A.", 12584.0)
    con.close()


def test_factura_rota_va_a_cuarentena(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_rota())
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "rota_importe_no_cierra.pdf", con, api_key="fake")

    assert resultado.estado == "cuarentena"
    assert "línea" in resultado.detalle
    en_facturas = con.execute(
        "SELECT 1 FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert en_facturas is None
    con.close()


def test_reprocesar_el_mismo_pdf_no_duplica(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    r1 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    r2 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert r1.estado == "guardada"
    assert r2.estado == "ya_procesada"
    con.close()


def test_homologa_conceptos_al_guardar(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    normalizado = con.execute(
        "SELECT concepto_normalizado FROM conceptos WHERE hash_pdf = ? AND orden = 0",
        [resultado.hash_pdf],
    ).fetchone()[0]
    assert normalizado == "abono_movil"
    con.close()
