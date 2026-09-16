"""Test de "Confirmar carga" contra AppTest de Streamlit.

AppTest NO puede simular una edición de `st.data_editor` (no aparece en su
árbol de elementos interactuables -- verificado: no hay `at.data_editor`).
Lo que se puede probar acá es la CÁSCARA: que la página renderiza, que el
botón "Confirmar factura" queda habilitado o bloqueado según los datos del
borrador (usando el contenido SIN editar de la tabla, que ya viene del
borrador guardado), y el circuito de descartar. Editar la tabla y volver a
homologar/validar con los valores corregidos ya está cubierto en
`tests/test_pipeline.py` (`confirmar_factura` en sí) y en
`tests/extraccion/test_esquema.py` (`conceptos_desde_filas`/
`montos_desde_filas`, las funciones puras que arman la factura editada a
partir de las filas del editor)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

import core.almacenamiento as almacenamiento_mod
from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import Concepto, FacturaExtraida, Impuesto

_ENTRYPOINT = (
    Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "confirmar.py"
)


def _app():
    return AppTest.from_file(str(_ENTRYPOINT))


def _factura(hash_pdf: str = "b1", *, servicio: str | None = "telefonia") -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio=servicio,
        periodo_desde="2026-07-01",
        periodo_hasta="2026-07-31",
        fecha_emision="2026-07-05",
        fecha_vencimiento=None,
        numero_comprobante="0001-00045501",
        moneda="ARS",
        conceptos=[Concepto("Abono 4 líneas móviles", 4, "línea", 2500.0, 10000.0)],
        impuestos=[Impuesto("IVA 21%", importe=2100.0)],
        subtotal=10000.0,
        total=12100.0,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


def test_sin_borradores_muestra_exito(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    at = _app()
    at.run()
    assert not at.exception
    assert any("No hay facturas esperando confirmación" in s.value for s in at.success)


def test_pagina_renderiza_con_un_borrador_completo(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(), estado="borrador", texto_extraido="TOTAL A PAGAR $ 12.100,00")
    con.close()

    at = _app()
    at.run()

    assert not at.exception


def test_boton_confirmar_habilitado_cuando_todo_cierra(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(), estado="borrador", texto_extraido="TOTAL A PAGAR $ 12.100,00")
    con.close()

    at = _app()
    at.run()

    boton = at.get_by_key("confirmar_b1")
    assert boton.disabled is False


def test_boton_confirmar_deshabilitado_sin_servicio(tmp_path, monkeypatch):
    """Un borrador vacío (Gemini no pudo leerlo) o al que le falta el
    servicio no se puede confirmar -- el selectbox arranca en "(elegir)"."""
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(servicio=None), estado="borrador")
    con.close()

    at = _app()
    at.run()

    boton = at.get_by_key("confirmar_b1")
    assert boton.disabled is True
    assert any("no se puede confirmar" in w.value.lower() for w in at.warning)


def test_boton_confirmar_deshabilitado_sin_ningun_concepto(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    factura_vacia = FacturaExtraida(
        emisor=None,
        cuit=None,
        servicio=None,
        periodo_desde=None,
        periodo_hasta=None,
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        hash_pdf="b1",
        ruta_pdf="/tmp/x.pdf",
    )
    guardar_factura(
        con, factura_vacia, estado="borrador", motivo_carga="No se pudo leer con Gemini: timeout"
    )
    con.close()

    at = _app()
    at.run()

    assert not at.exception
    boton = at.get_by_key("confirmar_b1")
    assert boton.disabled is True
    assert any("No se pudo leer automáticamente" in w.value for w in at.warning)


def test_pdf_no_disponible_muestra_texto_extraido(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    # Sin ruta_evidencia (ej. EVIDENCIA_DIR no configurada) -- cae al texto.
    factura = _factura()
    factura.ruta_evidencia = None
    guardar_factura(con, factura, estado="borrador", texto_extraido="Comunicaciones Sur S.A.")
    con.close()

    at = _app()
    at.run()

    assert not at.exception
    textos = " ".join(t.value for t in at.text)
    assert "Comunicaciones Sur S.A." in textos


def test_descartar_borrador_lo_saca_de_la_lista(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(), estado="borrador")
    con.close()

    at = _app()
    at.run()
    at.get_by_key("descartar_b1").click().run()

    assert not at.exception
    con = conectar(tmp_path / "test.duckdb")
    fila = con.execute("SELECT 1 FROM facturas WHERE hash_pdf = 'b1'").fetchone()
    con.close()
    assert fila is None


def test_confirmar_una_factura_que_cierra_la_saca_de_borradores(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(), estado="borrador", texto_extraido="TOTAL A PAGAR $ 12.100,00")
    con.close()

    at = _app()
    at.run()
    at.get_by_key("confirmar_b1").click().run()

    assert not at.exception
    con = conectar(tmp_path / "test.duckdb")
    estado = con.execute("SELECT estado FROM facturas WHERE hash_pdf = 'b1'").fetchone()[0]
    con.close()
    assert estado == "aprobada"
