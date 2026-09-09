"""Tests de las reglas de alerta contra los umbrales reales de
data/alertas.yaml (vigencia_desde: 2026-09-01: precio_por_encima_del_ipc_pp=5.0,
salto_de_cantidad_ratio=0.30)."""

from core.analisis.alertas import (
    alertas_por_concepto_nuevo_o_desaparecido,
    alertas_por_item_duplicado,
    alertas_por_precio_sobre_ipc,
    alertas_por_recargos,
    alertas_por_salto_de_cantidad,
    generar_alertas,
)
from core.analisis.variacion import descomponer_variacion
from core.extraccion.esquema import Concepto, FacturaExtraida, Recargo


def _factura(conceptos=None, recargos=None) -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit=None,
        servicio="telefonia",
        periodo_desde=None,
        periodo_hasta=None,
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        conceptos=conceptos or [],
        recargos=recargos or [],
    )


def test_cualquier_recargo_dispara_alerta():
    factura = _factura(recargos=[Recargo("Interés por mora", importe=500.0)])
    alertas = alertas_por_recargos(factura)
    assert len(alertas) == 1
    assert alertas[0].severidad == "alta"


def test_sin_recargos_no_hay_alerta():
    assert alertas_por_recargos(_factura()) == []


def test_item_duplicado_se_detecta():
    factura = _factura(
        conceptos=[
            Concepto("Abono", 1, None, 1000.0, 1000.0),
            Concepto("Abono", 1, None, 1000.0, 1000.0),
        ]
    )
    alertas = alertas_por_item_duplicado(factura)
    assert len(alertas) == 1
    assert "2 veces" in alertas[0].mensaje


def test_concepto_nuevo_dispara_alerta():
    d = descomponer_variacion(
        "consumo_datos", cantidad_0=0, precio_0=50, cantidad_1=10, precio_1=50
    )
    alertas = alertas_por_concepto_nuevo_o_desaparecido([d])
    assert len(alertas) == 1
    assert alertas[0].tipo == "concepto_nuevo"


def test_concepto_desaparecido_dispara_alerta():
    d = descomponer_variacion("roaming", cantidad_0=5, precio_0=100, cantidad_1=0, precio_1=100)
    alertas = alertas_por_concepto_nuevo_o_desaparecido([d])
    assert len(alertas) == 1
    assert alertas[0].tipo == "concepto_desaparecido"


def test_salto_de_cantidad_por_encima_del_umbral():
    # umbral real: 0.30. De 4 a 6 líneas es +50%, por encima del umbral.
    d = descomponer_variacion(
        "abono_movil", cantidad_0=4, precio_0=2500, cantidad_1=6, precio_1=2500
    )
    alertas = alertas_por_salto_de_cantidad([d])
    assert len(alertas) == 1
    assert "+50%" in alertas[0].mensaje


def test_salto_de_cantidad_por_debajo_del_umbral_no_alerta():
    # De 10 a 11 es +10%, por debajo de 0.30.
    d = descomponer_variacion(
        "abono_movil", cantidad_0=10, precio_0=100, cantidad_1=11, precio_1=100
    )
    assert alertas_por_salto_de_cantidad([d]) == []


def test_precio_por_encima_del_ipc_dispara_alerta():
    # Precio sube de 100 a 120 (+20%). IPC del período: 10%. Exceso: 10pp >= umbral 5pp.
    d = descomponer_variacion("abono_movil", cantidad_0=4, precio_0=100, cantidad_1=4, precio_1=120)
    alertas = alertas_por_precio_sobre_ipc([d], ipc_periodo_pct=0.10)
    assert len(alertas) == 1
    assert alertas[0].severidad == "alta"


def test_precio_apenas_por_encima_del_ipc_no_dispara():
    # Precio sube 12%, IPC 10% -> exceso 2pp, por debajo del umbral de 5pp.
    d = descomponer_variacion("abono_movil", cantidad_0=4, precio_0=100, cantidad_1=4, precio_1=112)
    assert alertas_por_precio_sobre_ipc([d], ipc_periodo_pct=0.10) == []


def test_generar_alertas_combina_todas_las_reglas():
    factura = _factura(recargos=[Recargo("Mora", importe=200.0)])
    d = descomponer_variacion(
        "abono_movil", cantidad_0=4, precio_0=2500, cantidad_1=6, precio_1=2500
    )
    alertas = generar_alertas(factura, [d], ipc_periodo_pct=0.10)
    tipos = {a.tipo for a in alertas}
    assert "recargo" in tipos
    assert "salto_de_cantidad" in tipos
