"""Tests de la lógica de comparación del banco de medición (Bloque 1 del
plan de rediseño de septiembre 2026) -- ver docstring de
scripts/banco_extraccion.py para el porqué. Estos tests NO tocan
data/reales/ (zona restringida): arman a mano un `FacturaExtraida` y una
verdad de referencia chica, con los tres casos que el script tiene que
distinguir bien: una lectura perfecta, una con un impuesto tomado de la
columna equivocada (el patrón real de docs/auditoria-2026-09-facturas-
reales.md, B-3), y una con una línea de menos."""

from core.extraccion.esquema import Concepto, FacturaExtraida, Impuesto
from scripts.banco_extraccion import comparar_factura


def _verdad(**overrides) -> dict:
    base = {
        "emisor": "Usina Popular y Municipal de Tandil S.E.M.",
        "cuit": "30-54570225-4",
        "servicio": "energia",
        "periodo_desde": "2022-07-01",
        "periodo_hasta": "2022-07-31",
        "fecha_emision": "2022-08-08",
        "fecha_vencimiento": "2022-08-14",
        "numero_comprobante": "A101-00295837",
        "moneda": "ARS",
        "conceptos": [
            {"descripcion": "Cargo Fijo", "importe": 108.71, "concepto_correcto": "cargo_fijo"},
            {"descripcion": "Energía", "importe": 420.85, "concepto_correcto": "consumo_energia"},
        ],
        "impuestos": [
            {"nombre": "I.V.A. (27,000%)", "importe": 621.51},
        ],
        "subtotal": 529.56,
        "total": 1151.07,
    }
    base.update(overrides)
    return base


def _factura(**overrides) -> FacturaExtraida:
    base = dict(
        emisor="Usina Popular y Municipal de Tandil S.E.M.",
        cuit="30-54570225-4",
        servicio="energia",
        periodo_desde="2022-07-01",
        periodo_hasta="2022-07-31",
        fecha_emision="2022-08-08",
        fecha_vencimiento="2022-08-14",
        numero_comprobante="A101-00295837",
        moneda="ARS",
        conceptos=[
            Concepto("Cargo Fijo", 1, None, 108.71, 108.71, concepto_sugerido="cargo_fijo"),
            Concepto("Energía", 56, "kWh", 7.5151, 420.85, concepto_sugerido="consumo_energia"),
        ],
        impuestos=[Impuesto("I.V.A. (27,000%)", 621.51)],
        subtotal=529.56,
        total=1151.07,
    )
    base.update(overrides)
    return FacturaExtraida(**base)


def test_lectura_perfecta_da_100_por_ciento_en_todo():
    resultado = comparar_factura(_factura(), _verdad())
    (
        cab_ok,
        cab_total,
        con_ok,
        con_total,
        imp_ok,
        imp_total,
        subtotal_ok,
        total_ok,
        cierra,
        sugerido_ok,
        sugerido_total,
    ) = resultado

    assert cab_ok == cab_total  # los 9 campos de cabecera coinciden
    assert con_ok == con_total == 2
    assert imp_ok == imp_total == 1
    assert subtotal_ok
    assert total_ok
    assert cierra
    assert sugerido_ok == sugerido_total == 2


def test_impuesto_con_la_base_imponible_en_vez_del_importe_no_matchea():
    """El patrón exacto de B-3: el modelo toma el primer monto de la línea
    (la base imponible, 2301.90) en vez del segundo (el importe real,
    621.51) -- el banco tiene que marcarlo como impuesto MAL leído, no
    como un simple redondeo."""
    factura = _factura(impuestos=[Impuesto("I.V.A. (27,000%)", 2301.90)])
    _, _, _, _, imp_ok, imp_total, _, _, _, _, _ = comparar_factura(factura, _verdad())
    assert imp_total == 1
    assert imp_ok == 0


def test_una_linea_de_concepto_de_menos_no_cuenta_como_correcta():
    factura = _factura(
        conceptos=[Concepto("Cargo Fijo", 1, None, 108.71, 108.71, concepto_sugerido="cargo_fijo")]
    )
    _, _, con_ok, con_total, _, _, _, _, _, sugerido_ok, sugerido_total = comparar_factura(
        factura, _verdad()
    )
    assert con_total == 2
    assert con_ok == 1
    # La línea que falta también deja de contar para concepto_sugerido.
    assert sugerido_total == 2
    assert sugerido_ok == 1


def test_descripcion_distinta_pero_importe_igual_igual_matchea():
    """Un proveedor distinto puede redactar la descripción distinto -- el
    banco no debe exigir texto literal idéntico, solo que el importe
    coincida y la descripción sea razonablemente parecida."""
    factura = _factura(
        conceptos=[
            Concepto("Cargo fijo mensual", 1, None, 108.71, 108.71, concepto_sugerido="cargo_fijo"),
            Concepto(
                "Consumo de energía eléctrica",
                56,
                "kWh",
                7.5151,
                420.85,
                concepto_sugerido="consumo_energia",
            ),
        ]
    )
    _, _, con_ok, con_total, _, _, _, _, _, _, _ = comparar_factura(factura, _verdad())
    assert con_ok == con_total == 2


def test_campo_de_cabecera_null_en_ambos_lados_cuenta_como_correcto():
    factura = _factura(fecha_vencimiento=None)
    verdad = _verdad(fecha_vencimiento=None)
    cab_ok, cab_total, *_ = comparar_factura(factura, verdad)
    assert cab_ok == cab_total


def test_campo_de_cabecera_null_de_un_solo_lado_cuenta_como_incorrecto():
    factura = _factura(fecha_vencimiento=None)
    cab_ok, cab_total, *_ = comparar_factura(factura, _verdad())
    assert cab_ok == cab_total - 1


def test_cabecera_tolera_mayusculas_y_acentos_pero_no_un_valor_distinto():
    factura = _factura(emisor="USINA POPULAR Y MUNICIPAL DE TANDIL S.E.M.")
    cab_ok, cab_total, *_ = comparar_factura(factura, _verdad())
    assert cab_ok == cab_total  # mismo valor, distinta capitalización -> ok

    factura_mal = _factura(emisor="Edesur S.A.")
    cab_ok_mal, cab_total_mal, *_ = comparar_factura(factura_mal, _verdad())
    assert cab_ok_mal == cab_total_mal - 1


def test_subtotal_y_total_fuera_de_tolerancia_no_pasan():
    factura = _factura(subtotal=1000.00, total=1500.00)
    _, _, _, _, _, _, subtotal_ok, total_ok, _, _, _ = comparar_factura(factura, _verdad())
    assert not subtotal_ok
    assert not total_ok


def test_factura_sin_conceptos_de_verdad_no_divide_por_cero():
    """Una verdad sin conceptos (no debería pasar en la práctica, pero el
    banco no debe romperse) da 0/0 -- lo resuelve `_porcentaje` en el
    dataclass, esta función solo tiene que devolver (0, 0) sin lanzar."""
    verdad = _verdad(conceptos=[])
    _, _, con_ok, con_total, *_ = comparar_factura(_factura(), verdad)
    assert con_total == 0
    assert con_ok == 0


# --- concepto_sugerido (Bloque 3 del plan de rediseño de septiembre 2026) ---


def test_concepto_sugerido_incorrecto_no_cuenta():
    factura = _factura(
        conceptos=[
            Concepto("Cargo Fijo", 1, None, 108.71, 108.71, concepto_sugerido="consumo_energia"),
            Concepto("Energía", 56, "kWh", 7.5151, 420.85, concepto_sugerido="consumo_energia"),
        ]
    )
    *_, sugerido_ok, sugerido_total = comparar_factura(factura, _verdad())
    assert sugerido_total == 2
    assert sugerido_ok == 1  # solo "Energía" acertó


def test_concepto_sugerido_none_no_cuenta_como_correcto():
    factura = _factura(
        conceptos=[
            Concepto("Cargo Fijo", 1, None, 108.71, 108.71, concepto_sugerido=None),
            Concepto("Energía", 56, "kWh", 7.5151, 420.85, concepto_sugerido=None),
        ]
    )
    *_, sugerido_ok, sugerido_total = comparar_factura(factura, _verdad())
    assert sugerido_total == 2
    assert sugerido_ok == 0


def test_lineas_de_verdad_sin_concepto_correcto_no_cuentan_para_el_total():
    """El caso real de data/reales/banco/gas_1.yaml: una línea ambigua se
    deja sin `concepto_correcto` a propósito -- no debe penalizar ni
    premiar a ningún proveedor."""
    verdad = _verdad(
        conceptos=[
            {"descripcion": "Cargo Fijo", "importe": 108.71, "concepto_correcto": "cargo_fijo"},
            {"descripcion": "Algo ambiguo", "importe": 420.85},  # sin concepto_correcto
        ]
    )
    factura = _factura(
        conceptos=[
            Concepto("Cargo Fijo", 1, None, 108.71, 108.71, concepto_sugerido="cargo_fijo"),
            Concepto("Algo ambiguo", 1, None, 420.85, 420.85, concepto_sugerido=None),
        ]
    )
    *_, sugerido_ok, sugerido_total = comparar_factura(factura, verdad)
    assert sugerido_total == 1  # solo la línea con concepto_correcto cuenta
    assert sugerido_ok == 1
