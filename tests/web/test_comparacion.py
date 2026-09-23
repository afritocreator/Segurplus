"""Tests de web/comparacion.py -- Bloque 2 del arreglo de
docs/auditoria-2026-09-web.md (E-5, E-11). No dependen de la red: el IPC
se mockea a nivel de módulo, con valores calculados a mano."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import core.almacenamiento as almacenamiento_mod
import web.comparacion as comparacion_mod
from core.extraccion.esquema import Concepto, FacturaExtraida
from core.pipeline import confirmar_factura
from web.comparacion import calcular_comparacion


@dataclass
class _VariacionRealFalsa:
    variacion_real_pct: float


@pytest.fixture
def con(tmp_path):
    conexion = almacenamiento_mod.conectar(tmp_path / "test.duckdb")
    yield conexion
    conexion.close()


def _factura(
    *, hash_pdf: str, periodo_desde: str, periodo_hasta: str, precio: float
) -> FacturaExtraida:
    return FacturaExtraida(
        emisor="P",
        cuit="30-1",
        servicio="energia",
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        conceptos=[Concepto("Cargo fijo", 4, None, precio, precio * 4)],
        subtotal=precio * 4,
        total=precio * 4,
        hash_pdf=hash_pdf,
    )


def test_precio_en_linea_con_la_inflacion_no_genera_alerta(monkeypatch, con):
    """docs/auditoria-2026-09-web.md, E-5: precio +8%, IPC del período +8%
    (calculado a mano, deflactado: (1.08/1.08 - 1) = 0% de exceso, muy por
    debajo del umbral) -- no tiene que aparecer 'precio_sobre_ipc'. Antes,
    con el IPC desconocido tratado como 0%, esta misma suba SÍ alertaba."""
    monkeypatch.setattr(comparacion_mod, "leer_ipc", lambda: object())
    monkeypatch.setattr(comparacion_mod, "inflacion_del_periodo", lambda *a, **k: 0.08)
    monkeypatch.setattr(comparacion_mod, "variacion_real", lambda *a, **k: _VariacionRealFalsa(0.0))

    f0 = _factura(
        hash_pdf="c0", periodo_desde="2026-07-01", periodo_hasta="2026-07-31", precio=100.0
    )
    f1 = _factura(
        hash_pdf="c1", periodo_desde="2026-08-01", periodo_hasta="2026-08-31", precio=108.0
    )
    almacenamiento_mod.guardar_factura(con, f0, estado="borrador")
    confirmar_factura(con, f0, actor="test")
    almacenamiento_mod.guardar_factura(con, f1, estado="borrador")
    confirmar_factura(con, f1, actor="test")

    resultado = calcular_comparacion(
        con,
        servicio="energia",
        periodo_0="2026-07-01",
        periodo_1="2026-08-01",
        periodos_del_servicio=[("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31")],
    )
    assert resultado.ipc_periodo_pct == 0.08
    assert not any(a.tipo == "precio_sobre_ipc" for a in resultado.alertas)


def test_sin_ipc_calculable_tampoco_genera_alerta_de_precio(monkeypatch, con):
    """Mismo caso, pero el IPC no se pudo descargar -- antes se usaba 0.0
    como reemplazo y CUALQUIER aumento alertaba como "sobre la inflación"."""

    def _sin_ipc(*a, **k):
        raise ValueError("no hay IPC para ese período")

    monkeypatch.setattr(comparacion_mod, "leer_ipc", lambda: object())
    monkeypatch.setattr(comparacion_mod, "inflacion_del_periodo", _sin_ipc)

    f0 = _factura(
        hash_pdf="s0", periodo_desde="2026-07-01", periodo_hasta="2026-07-31", precio=100.0
    )
    f1 = _factura(
        hash_pdf="s1", periodo_desde="2026-08-01", periodo_hasta="2026-08-31", precio=108.0
    )
    almacenamiento_mod.guardar_factura(con, f0, estado="borrador")
    confirmar_factura(con, f0, actor="test")
    almacenamiento_mod.guardar_factura(con, f1, estado="borrador")
    confirmar_factura(con, f1, actor="test")

    resultado = calcular_comparacion(
        con,
        servicio="energia",
        periodo_0="2026-07-01",
        periodo_1="2026-08-01",
        periodos_del_servicio=[("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31")],
    )
    assert resultado.ipc_periodo_pct is None
    assert not any(a.tipo == "precio_sobre_ipc" for a in resultado.alertas)
    assert any("No se pudo calcular la variación real" in a for a in resultado.avisos_calculo)
