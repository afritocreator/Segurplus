"""Regresiones críticas del cierre de auditoría del piloto web."""

from dataclasses import replace

import pytest

from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import Concepto, FacturaExtraida
from core.pipeline import confirmar_factura
from web.comparacion import sincronizar_casos_pendientes, snapshot_comparacion


def _factura(hash_pdf: str) -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Proveedor de prueba",
        cuit="30-12345678-1",
        servicio="energia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante="0001-00000042",
        moneda="ARS",
        conceptos=[Concepto("Cargo fijo", 1, None, 1000, 1000)],
        subtotal=1000,
        total=1000,
        hash_pdf=hash_pdf,
    )


def test_pdf_distinto_mismo_comprobante_bloquea_hasta_excepcion(tmp_path):
    con = conectar(tmp_path / "facturas.duckdb")
    primera = _factura("hash-uno")
    segunda = replace(_factura("hash-dos"), cuit="30123456781", numero_comprobante="000100000042")
    guardar_factura(con, primera, estado="borrador")
    confirmar_factura(con, primera, actor="operador")
    guardar_factura(con, segunda, estado="borrador")

    with pytest.raises(ValueError, match="igual CUIT"):
        confirmar_factura(con, segunda, actor="operador")
    assert con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = 'hash-dos'"
    ).fetchone()[0] == "borrador"

    confirmar_factura(
        con, segunda, actor="operador", permitir_duplicado=True,
        motivo_duplicado="Son dos suministros diferentes en una factura consolidada",
    )
    assert con.execute(
        "SELECT motivo FROM excepciones_duplicado WHERE hash_pdf = 'hash-dos'"
    ).fetchone()[0].startswith("Son dos suministros")
    con.close()


def test_periodo_invertido_no_sale_del_borrador(tmp_path):
    con = conectar(tmp_path / "facturas.duckdb")
    factura = replace(_factura("hash-invertido"), periodo_hasta="2026-07-31")
    guardar_factura(con, factura, estado="borrador")
    with pytest.raises(ValueError, match="anterior"):
        confirmar_factura(con, factura, actor="operador")
    assert con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = 'hash-invertido'"
    ).fetchone()[0] == "borrador"
    con.close()


def test_alertas_comparativas_se_materializan_fuera_del_get(tmp_path, monkeypatch):
    import requests

    import web.comparacion as comparacion

    def _sin_ipc():
        raise requests.exceptions.ConnectionError("IPC no disponible")

    monkeypatch.setattr(comparacion, "leer_ipc", _sin_ipc)
    con = conectar(tmp_path / "facturas.duckdb")
    anterior = replace(_factura("julio"), periodo_desde="2026-07-01", periodo_hasta="2026-07-31")
    actual = replace(
        _factura("agosto"), numero_comprobante="0001-00000043",
        conceptos=[Concepto("Cargo fijo", 100, None, 1000, 100000)],
        subtotal=100000, total=100000,
    )
    for factura in (anterior, actual):
        guardar_factura(con, factura, estado="borrador")
        confirmar_factura(con, factura, actor="operador")
    assert con.execute("SELECT count(*) FROM casos_alerta").fetchone()[0] == 0
    sincronizar_casos_pendientes(con, servicio="energia")
    referencias = con.execute(
        "SELECT DISTINCT hash_pdf FROM casos_alerta WHERE hash_pdf LIKE 'comparacion:%'"
    ).fetchall()
    assert referencias == [("comparacion:energia:2026-07-01:2026-08-01",)]
    con.close()


def test_snapshot_cambia_si_se_corrige_una_factura_aprobada(tmp_path):
    con = conectar(tmp_path / "facturas.duckdb")
    factura = _factura("snapshot")
    guardar_factura(con, factura, estado="borrador")
    confirmar_factura(con, factura, actor="operador")
    args = {"servicio": "energia", "periodo_0": "2026-07-01", "periodo_1": "2026-08-01"}
    primero = snapshot_comparacion(con, **args)
    con.execute("UPDATE facturas SET total = 999 WHERE hash_pdf = 'snapshot'")
    assert snapshot_comparacion(con, **args) != primero
    con.close()


def test_importe_argentino_y_error_especifico():
    from web.app import _num_desde_texto

    assert _num_desde_texto("1.234,56") == 1234.56
    assert _num_desde_texto("$ 1.234,56") == 1234.56
    with pytest.raises(ValueError, match="importe inválido"):
        _num_desde_texto("1.234.56")
