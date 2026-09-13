"""Test de la página "Conceptos sin clasificar" contra AppTest de
Streamlit -- el circuito de calibración de docs/auditoria-2026-09.md
(Bloque 9 del plan de correcciones)."""

from pathlib import Path

import pytest

import core.almacenamiento as almacenamiento_mod
from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import Concepto, FacturaExtraida

_ENTRYPOINT = (
    Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "sin_clasificar.py"
)


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(_ENTRYPOINT))


def _factura_sin_homologar(hash_pdf: str, descripcion: str, importe: float) -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit="30-1",
        servicio="telefonia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision="2026-08-05",
        fecha_vencimiento=None,
        numero_comprobante=hash_pdf,
        moneda="ARS",
        conceptos=[Concepto(descripcion, 1, None, importe, importe)],
        subtotal=importe,
        total=importe,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


def test_sin_conceptos_sin_clasificar_muestra_exito(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.run()
    assert not at.exception
    assert any("No hay conceptos sin clasificar" in s.value for s in at.success)


@pytest.fixture
def base_con_conceptos_sin_clasificar(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar()
    guardar_factura(
        con,
        _factura_sin_homologar("h1", "Servicio de telefonía Agosto 2026", 30000.0),
        scores_homologacion={0: 0.588},
    )
    con.close()


def test_pagina_renderiza_sin_errores(base_con_conceptos_sin_clasificar):
    at = _app()
    at.run()
    assert not at.exception


def test_pagina_muestra_el_importe_sin_clasificar(base_con_conceptos_sin_clasificar):
    at = _app()
    at.run()
    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Importe sin clasificar"] == "$30.000,00"


def test_boton_rehomologar_actualiza_concepto(base_con_conceptos_sin_clasificar, monkeypatch):
    diccionario_con_alias = {"servicio_telefonia": ["servicio de telefonia"]}
    monkeypatch.setattr(
        "apps.segurplus.paginas.sin_clasificar.cargar_diccionario",
        lambda servicio: diccionario_con_alias,
    )

    at = _app()
    at.run()
    at.button[0].click().run()  # previsualizar
    at.checkbox[0].check().run()
    at.button[1].click().run()  # aplicar confirmado

    con = conectar()
    concepto = con.execute("SELECT concepto_normalizado FROM conceptos").fetchone()[0]
    assert concepto == "servicio_telefonia"
    con.close()
