"""Test de la página de Cuarentena contra AppTest de Streamlit --
docs/auditoria-2026-09.md, hallazgo A-19 (0% de cobertura antes de este
bloque) y A-17 (botón "Reintentar")."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.almacenamiento as almacenamiento_mod
from core.almacenamiento import conectar, factura_ya_procesada, guardar_en_cuarentena
from core.extraccion.esquema import Concepto, FacturaExtraida
from core.extraccion.validacion import validar_factura

_ENTRYPOINT = (
    Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "cuarentena.py"
)


def _factura_rota(hash_pdf: str) -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio="telefonia",
        periodo_desde="2026-07-01",
        periodo_hasta="2026-07-31",
        fecha_emision="2026-07-05",
        fecha_vencimiento=None,
        numero_comprobante=hash_pdf,
        moneda="ARS",
        conceptos=[Concepto("Abono 5 líneas", 5, "línea", 100.0, 800.0)],  # no cierra
        subtotal=800.0,
        total=968.0,
        hash_pdf=hash_pdf,
        ruta_pdf=f"/tmp/{hash_pdf}.pdf",
    )


def _app():
    return AppTest.from_file(str(_ENTRYPOINT))


def test_sin_cuarentena_muestra_mensaje_de_exito(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.run()
    assert not at.exception
    assert any("No hay facturas en cuarentena" in s.value for s in at.success)


@pytest.fixture
def base_con_una_factura_en_cuarentena(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar()
    factura = _factura_rota("rota1")
    resultado = validar_factura(factura)
    guardar_en_cuarentena(
        con, hash_pdf=factura.hash_pdf, ruta_pdf=factura.ruta_pdf, resultado=resultado
    )
    con.close()
    return factura.hash_pdf


def test_muestra_la_factura_en_cuarentena(base_con_una_factura_en_cuarentena):
    at = _app()
    at.run()
    assert not at.exception
    assert any("rota1" in md.value for md in at.markdown)


def test_boton_reintentar_saca_la_factura_de_cuarentena(base_con_una_factura_en_cuarentena):
    hash_pdf = base_con_una_factura_en_cuarentena
    at = _app()
    at.run()
    at.button[0].click().run()

    con = conectar()
    assert not factura_ya_procesada(con, hash_pdf)
    con.close()
