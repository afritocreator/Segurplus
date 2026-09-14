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


def test_fecha_dia_mes_anio_con_guiones_se_normaliza():
    assert _normalizar_fecha("05-07-2026") == "2026-07-05"


def test_mes_anio_se_normaliza_al_primer_dia_del_mes():
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo B-1: caso real --
    una factura de luz de la Usina Popular de Tandil solo imprime
    "Período: 07/2022", sin día. Antes de esta corrección, `_normalizar_fecha`
    devolvía `None`, la factura quedaba `periodo_desde=None`, VALIDABA BIEN
    y se guardaba -- pero todas las consultas del análisis filtran
    `periodo_desde IS NOT NULL`, así que quedaba invisible sin ningún aviso.
    Sin `fin_de_mes` (el default), sigue yendo al PRIMER día -- lo que usa
    `periodo_desde`."""
    assert _normalizar_fecha("07/2022") == "2022-07-01"


def test_anio_guion_mes_se_normaliza_al_primer_dia_del_mes():
    assert _normalizar_fecha("2022-07") == "2022-07-01"


def test_mes_anio_con_fin_de_mes_se_normaliza_al_ultimo_dia_del_mes():
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-1: julio tiene
    31 días -- `periodo_hasta` con `fin_de_mes=True` tiene que cubrir el
    mes ENTERO, no su primer día (si no, `alertas_por_periodo_faltante`
    espera el próximo período al día siguiente del primero de julio)."""
    assert _normalizar_fecha("07/2022", fin_de_mes=True) == "2022-07-31"


def test_anio_guion_mes_con_fin_de_mes_se_normaliza_al_ultimo_dia_del_mes():
    # Febrero de 2024 es bisiesto -- 29 días, no 28.
    assert _normalizar_fecha("2024-02", fin_de_mes=True) == "2024-02-29"


def test_mes_anio_con_mes_invalido_devuelve_none():
    assert _normalizar_fecha("13/2022") is None
    assert _normalizar_fecha("13/2022", fin_de_mes=True) is None


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


def test_factura_desde_json_periodo_hasta_mes_anio_va_a_fin_de_mes():
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-1: la factura
    real de luz (Usina Popular de Tandil) solo imprime "Período: 07/2022".
    `periodo_desde` va al primer día (1/7), `periodo_hasta` al ÚLTIMO
    (31/7) -- si los dos fueran el primer día, quedarían iguales y
    `alertas_por_periodo_faltante` esperaría el próximo período al día
    siguiente del primero de julio, disparando una alerta falsa con
    cualquier factura consecutiva normal."""
    datos = _datos_minimos(periodo_desde="07/2022", periodo_hasta="07/2022")
    factura = factura_desde_json(datos)
    assert factura.periodo_desde == "2022-07-01"
    assert factura.periodo_hasta == "2022-07-31"
