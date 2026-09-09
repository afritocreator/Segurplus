"""Test de la regla de oro (CLAUDE.md): valor calculado a mano.

Factura sintética de telefonía con dos conceptos:
- Abono 4 líneas a $2.500 c/u -> importe 10.000
- Consumo 120 minutos a $15 c/u -> importe 1.800

A mano:
  suma_conceptos = 10.000 + 1.800 = 11.800
  IVA 21% de 11.800 = 2.478
  total = 11.800 + 2.478 = 14.278
"""

from core.extraccion.esquema import Concepto, FacturaExtraida, Impuesto
from core.extraccion.validacion import validar_factura


def _factura_ok() -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit="30-12345678-1",
        servicio="telefonia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision="2026-08-05",
        fecha_vencimiento="2026-08-20",
        numero_comprobante="0001-00012345",
        moneda="ARS",
        conceptos=[
            Concepto(
                "Abono 4 líneas",
                cantidad=4,
                unidad="línea",
                precio_unitario=2500.0,
                importe=10000.0,
            ),
            Concepto(
                "Consumo minutos",
                cantidad=120,
                unidad="minuto",
                precio_unitario=15.0,
                importe=1800.0,
            ),
        ],
        impuestos=[Impuesto("IVA 21%", importe=2478.0)],
        subtotal=11800.0,
        total=14278.0,
    )


def test_factura_que_cierra_es_valida():
    resultado = validar_factura(_factura_ok())
    assert resultado.suma_conceptos == 11800.0
    assert resultado.subtotal_ok
    assert resultado.total_ok
    assert resultado.todas_las_lineas_ok
    assert resultado.factura_valida
    assert resultado.motivos_de_falla() == []


def test_linea_que_no_cierra_va_a_cuarentena():
    factura = _factura_ok()
    # Se "corrompe" el importe de la primera línea: 4 * 2500 = 10000, no 9000
    factura.conceptos[0].importe = 9000.0
    resultado = validar_factura(factura)
    assert not resultado.items[0].ok
    assert resultado.items[0].importe_esperado == 10000.0
    assert not resultado.todas_las_lineas_ok
    assert not resultado.factura_valida
    assert "línea" in resultado.motivos_de_falla()[0]


def test_subtotal_que_no_cierra_va_a_cuarentena():
    factura = _factura_ok()
    factura.subtotal = 9000.0  # debería ser 11800.0
    resultado = validar_factura(factura)
    assert not resultado.subtotal_ok
    assert not resultado.factura_valida


def test_total_que_no_cierra_contra_subtotal_mas_impuestos():
    factura = _factura_ok()
    factura.total = 20000.0  # debería ser 14278.0
    resultado = validar_factura(factura)
    assert not resultado.total_ok
    assert not resultado.factura_valida


def test_recargo_se_suma_al_total_esperado():
    from core.extraccion.esquema import Recargo

    factura = _factura_ok()
    factura.recargos = [Recargo("Interés por mora", importe=500.0)]
    factura.total = 14778.0  # 14278 + 500
    resultado = validar_factura(factura)
    assert resultado.suma_recargos == 500.0
    assert resultado.total_ok


def test_doble_lectura_del_total_discrepante_va_a_cuarentena():
    resultado = validar_factura(_factura_ok(), total_impreso=99999.0)
    assert not resultado.total_impreso_ok
    assert not resultado.factura_valida
    assert "impreso" in resultado.motivos_de_falla()[-1]


def test_doble_lectura_del_total_coincidente_pasa():
    resultado = validar_factura(_factura_ok(), total_impreso=14278.0)
    assert resultado.total_impreso_ok
    assert resultado.factura_valida


def test_tolerancia_de_un_peso_por_redondeo_no_bloquea():
    factura = _factura_ok()
    factura.conceptos[0].importe = 10000.99  # $0.99 de diferencia, dentro de tolerancia
    resultado = validar_factura(factura)
    assert resultado.items[0].ok
