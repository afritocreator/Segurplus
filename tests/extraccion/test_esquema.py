"""Test de core/extraccion/esquema.py::factura_desde_json y la
normalización de fechas (docs/auditoria-2026-09.md, hallazgo A-11): el
prompt le pide al modelo formato ISO, pero un LLM puede devolver el formato
argentino que ve impreso en la factura."""

from core.extraccion.esquema import _normalizar_fecha, factura_desde_json


def _datos_minimos(**overrides) -> dict:
    base = {
        "conceptos": [
            {"descripcion": "Abono", "cantidad": 1, "precio_unitario": 100.0, "importe": 100.0}
        ],
        "moneda": "ARS",
    }
    base.update(overrides)
    return base


def test_fecha_iso_pasa_intacta():
    assert _normalizar_fecha("2026-07-05") == "2026-07-05"


def test_fecha_argentina_se_normaliza_a_iso():
    assert _normalizar_fecha("05/07/2026") == "2026-07-05"


def test_fecha_argentina_con_dia_y_mes_sin_cero_a_la_izquierda():
    assert _normalizar_fecha("5/7/2026") == "2026-07-05"


def test_fecha_invalida_devuelve_none():
    assert _normalizar_fecha("31/02/2026") is None  # febrero no tiene 31 días


def test_fecha_sin_ningun_formato_reconocible_devuelve_none():
    assert _normalizar_fecha("hace un mes") is None


def test_none_devuelve_none():
    assert _normalizar_fecha(None) is None


def test_factura_desde_json_normaliza_fechas_argentinas():
    datos = _datos_minimos(
        periodo_desde="01/07/2026",
        periodo_hasta="31/07/2026",
        fecha_emision="05/07/2026",
        fecha_vencimiento="20/07/2026",
    )
    factura = factura_desde_json(datos)
    assert factura.periodo_desde == "2026-07-01"
    assert factura.periodo_hasta == "2026-07-31"
    assert factura.fecha_emision == "2026-07-05"
    assert factura.fecha_vencimiento == "2026-07-20"


def test_factura_desde_json_deja_none_una_fecha_no_interpretable():
    datos = _datos_minimos(fecha_emision="fecha ilegible")
    factura = factura_desde_json(datos)
    assert factura.fecha_emision is None
