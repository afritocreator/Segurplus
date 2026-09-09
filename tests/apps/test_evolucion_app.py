"""Test de la página de evolución contra AppTest de Streamlit, con una base
DuckDB temporal sembrada con facturas sintéticas -- docs/auditoria-2026-09.md,
hallazgo A-19: esta página (donde vivía el bug de A-1) tenía 0% de cobertura
antes de este bloque. `core.macro.ipc.leer_ipc` se monkeypatchea para no
pegarle a la red real de datos.gob.ar."""

from pathlib import Path

import polars as pl
import pytest
from streamlit.testing.v1 import AppTest

import core.almacenamiento as almacenamiento_mod
from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import Concepto, FacturaExtraida

_ENTRYPOINT = (
    Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "evolucion.py"
)

_IPC_SINTETICO = pl.DataFrame(
    {
        "fecha": [__import__("datetime").date(2026, m, 1) for m in (5, 6, 7)],
        "indice": [100.0, 103.0, 106.0],
    }
)


def _factura(hash_pdf: str, periodo_desde: str, periodo_hasta: str, cantidad: float, precio: float):
    importe = cantidad * precio
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio="telefonia",
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        fecha_emision=periodo_desde,
        fecha_vencimiento=None,
        numero_comprobante=hash_pdf,
        moneda="ARS",
        conceptos=[Concepto("Abono", cantidad, "línea", precio, importe)],
        subtotal=importe,
        total=importe,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


@pytest.fixture(autouse=True)
def _sin_red_real(monkeypatch):
    monkeypatch.setattr("core.macro.ipc.leer_ipc", lambda **k: _IPC_SINTETICO)


@pytest.fixture
def base_con_dos_periodos(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar()
    guardar_factura(con, _factura("h1", "2026-06-01", "2026-06-30", 4, 2500.0))
    guardar_factura(con, _factura("h2", "2026-07-01", "2026-07-31", 4, 2800.0))
    con.close()


def _app():
    return AppTest.from_file(str(_ENTRYPOINT))


def test_pagina_evolucion_renderiza_sin_errores(base_con_dos_periodos):
    at = _app()
    at.run()
    assert not at.exception


def test_pagina_muestra_totales_de_ambos_periodos(base_con_dos_periodos):
    at = _app()
    at.run()
    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Total período base"] == "$10,000.00"
    assert metricas["Total período comparado"] == "$11,200.00"


def test_sin_facturas_muestra_mensaje_informativo(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.run()
    assert not at.exception
    assert any("Todavía no hay facturas" in i.value for i in at.info)
