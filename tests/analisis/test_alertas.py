"""Tests de las reglas de alerta contra los umbrales reales de
data/alertas.yaml (vigencia_desde: 2026-09-01: precio_por_encima_del_ipc_pp=5.0,
salto_de_cantidad_ratio=0.30)."""

from datetime import date

from core.analisis.alertas import (
    Alerta,
    alertas_por_concepto_nuevo_o_desaparecido,
    alertas_por_item_duplicado,
    alertas_por_periodo_faltante,
    alertas_por_precio_sobre_ipc,
    alertas_por_recargos,
    alertas_por_salto_de_cantidad,
    generar_alertas,
    ordenar_por_severidad,
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
    # Precio sube de 100 a 120 (+20%). IPC del período: 10%.
    # Exceso real = (1.20/1.10 - 1) * 100 = 9.0909...pp >= umbral 5pp.
    d = descomponer_variacion("abono_movil", cantidad_0=4, precio_0=100, cantidad_1=4, precio_1=120)
    alertas = alertas_por_precio_sobre_ipc([d], ipc_periodo_pct=0.10)
    assert len(alertas) == 1
    assert alertas[0].severidad == "alta"


def test_precio_apenas_por_encima_del_ipc_no_dispara():
    # Precio sube 12%, IPC 10% -> exceso real = (1.12/1.10 - 1)*100 = 1.818...pp,
    # por debajo del umbral de 5pp.
    d = descomponer_variacion("abono_movil", cantidad_0=4, precio_0=100, cantidad_1=4, precio_1=112)
    assert alertas_por_precio_sobre_ipc([d], ipc_periodo_pct=0.10) == []


def test_deflacta_en_vez_de_restar_porcentajes():
    # docs/auditoria-2026-09.md, hallazgo A-22: con la vieja fórmula lineal
    # (variacion - ipc), precio +55% con IPC +50% daba exceso = 5.0pp exactos
    # y disparaba la alerta. La fórmula correcta (deflactada) da
    # (1.55/1.50 - 1)*100 = 3.33...pp, por debajo del umbral de 5pp: el
    # proveedor subió EN LÍNEA con la inflación, no por encima -- no debería
    # alertar. Este test fija el comportamiento correcto.
    d = descomponer_variacion("abono_movil", cantidad_0=4, precio_0=100, cantidad_1=4, precio_1=155)
    alertas_lineal_habria_disparado = (0.55 - 0.50) * 100 >= 5.0
    assert alertas_lineal_habria_disparado  # confirma que el caso es el que rompía antes
    assert alertas_por_precio_sobre_ipc([d], ipc_periodo_pct=0.50) == []


def test_generar_alertas_combina_todas_las_reglas():
    factura = _factura(recargos=[Recargo("Mora", importe=200.0)])
    d = descomponer_variacion(
        "abono_movil", cantidad_0=4, precio_0=2500, cantidad_1=6, precio_1=2500
    )
    alertas = generar_alertas(factura, [d], ipc_periodo_pct=0.10)
    tipos = {a.tipo for a in alertas}
    assert "recargo" in tipos
    assert "salto_de_cantidad" in tipos


def test_salto_de_cantidad_no_dispara_sobre_cantidad_sintetica():
    # Hallazgo detectado por el revisor-financiero al revisar A-20: el 1.0
    # "sin cantidad" que agregar_conceptos() inventa cuando la cantidad neta
    # de un período dio cero con importe distinto de cero (una nota de
    # crédito) NO es una cantidad real. Compararla contra una cantidad real
    # de otro período (1 -> 4, "+300%") dispara un salto de cantidad
    # espurio si no se excluye explícitamente.
    d = descomponer_variacion(
        "cargo_fijo", cantidad_0=1, precio_0=2000, cantidad_1=4, precio_1=2500
    )
    # Sin excluirlo, sí dispararía (sirve para confirmar que el caso es real):
    assert alertas_por_salto_de_cantidad([d]) != []
    # Excluyendo el concepto por ser cantidad sintética, no dispara:
    alertas = alertas_por_salto_de_cantidad(
        [d], conceptos_con_cantidad_sintetica=frozenset({"cargo_fijo"})
    )
    assert alertas == []


def test_generar_alertas_excluye_salto_de_cantidad_sintetica():
    factura = _factura()
    d = descomponer_variacion(
        "cargo_fijo", cantidad_0=1, precio_0=2000, cantidad_1=4, precio_1=2500
    )
    alertas = generar_alertas(
        factura, [d], conceptos_con_cantidad_sintetica=frozenset({"cargo_fijo"})
    )
    assert not any(a.tipo == "salto_de_cantidad" for a in alertas)


# --- A-13: alerta de período faltante -- umbral real: dias_tolerancia_periodo=10 ---
#
# `periodo_hasta=None` en todos estos tests a propósito: ejercitan el
# criterio VIEJO (fallback a "un mes después"), que sigue vigente cuando la
# factura no trae periodo_hasta. Los tests con periodo_hasta real están más
# abajo (docs/auditoria-2026-09-piloto.md, hallazgo B-6).


def test_periodos_consecutivos_no_alertan():
    periodos = [(date(2026, 7, 1), None), (date(2026, 8, 1), None), (date(2026, 9, 1), None)]
    assert alertas_por_periodo_faltante(periodos) == []


def test_mes_completo_faltante_alerta():
    # jul -> sep, sin ago: esperado ago-01, real sep-01 -> exceso 31 días > 10.
    periodos = [(date(2026, 7, 1), None), (date(2026, 9, 1), None)]
    alertas = alertas_por_periodo_faltante(periodos)
    assert len(alertas) == 1
    assert alertas[0].tipo == "periodo_faltante"
    assert "2026-07-01" in alertas[0].mensaje
    assert "2026-09-01" in alertas[0].mensaje


def test_factura_unos_dias_tarde_dentro_de_tolerancia_no_alerta():
    # ago-08 en vez de ago-01: exceso de 7 días, por debajo de la tolerancia (10).
    periodos = [(date(2026, 7, 1), None), (date(2026, 8, 8), None)]
    assert alertas_por_periodo_faltante(periodos) == []


def test_factura_bastante_tarde_fuera_de_tolerancia_alerta():
    # ago-15: exceso de 14 días, por encima de la tolerancia (10).
    periodos = [(date(2026, 7, 1), None), (date(2026, 8, 15), None)]
    alertas = alertas_por_periodo_faltante(periodos)
    assert len(alertas) == 1


def test_no_alerta_con_un_solo_periodo():
    assert alertas_por_periodo_faltante([(date(2026, 7, 1), None)]) == []


def test_periodos_desordenados_se_ordenan_solos():
    # sep antes que jul, a propósito -- se ordenan por periodo_desde.
    periodos = [(date(2026, 9, 1), None), (date(2026, 7, 1), None)]
    alertas = alertas_por_periodo_faltante(periodos)
    assert len(alertas) == 1  # detecta el hueco de agosto igual


# --- B-6: usar periodo_hasta evita el falso positivo en servicio bimestral,
# --- con evidencia real (facturas de gas, Camuzzi) ------------------------


def test_servicio_bimestral_con_periodo_hasta_no_alerta_nunca():
    """Caso real: gas factura cada dos meses, con periodo_hasta declarado
    ("Período de Lectura: 01/07/2022- 31/08/2022"). Antes de usar
    periodo_hasta, esto alertaba SIEMPRE (asumía cadencia mensual) aunque
    nunca faltara nada -- A-27, ahora resuelto con evidencia real."""
    periodos = [
        (date(2026, 7, 1), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 12, 31)),
    ]
    assert alertas_por_periodo_faltante(periodos) == []


def test_servicio_bimestral_con_hueco_real_si_alerta():
    # Falta el bimestre sep-oct: de jul-ago se salta directo a nov-dic.
    periodos = [
        (date(2026, 7, 1), date(2026, 8, 31)),
        (date(2026, 11, 1), date(2026, 12, 31)),
    ]
    alertas = alertas_por_periodo_faltante(periodos)
    assert len(alertas) == 1
    assert alertas[0].tipo == "periodo_faltante"


def test_periodo_hasta_ausente_en_una_factura_cae_al_criterio_viejo():
    # La factura de julio no trae periodo_hasta (el modelo no lo pudo leer)
    # -- esta comparación puntual usa el fallback de "un mes después", el
    # resto de la lista no se ve afectado.
    periodos = [(date(2026, 7, 1), None), (date(2026, 9, 1), date(2026, 9, 30))]
    alertas = alertas_por_periodo_faltante(periodos)
    assert len(alertas) == 1  # jul -> sep sin agosto: sigue detectando el hueco


# --- ordenar_por_severidad: orden determinístico (Bloque 8) --------------


def test_ordenar_por_severidad_alta_media_baja():
    baja = Alerta(tipo="x", severidad="baja", mensaje="b")
    alta = Alerta(tipo="x", severidad="alta", mensaje="a")
    media = Alerta(tipo="x", severidad="media", mensaje="m")
    ordenadas = ordenar_por_severidad([baja, alta, media])
    assert [a.severidad for a in ordenadas] == ["alta", "media", "baja"]


def test_ordenar_por_severidad_es_estable_dentro_de_la_misma_severidad():
    a1 = Alerta(tipo="uno", severidad="alta", mensaje="1")
    a2 = Alerta(tipo="dos", severidad="alta", mensaje="2")
    ordenadas = ordenar_por_severidad([a1, a2])
    assert [a.tipo for a in ordenadas] == ["uno", "dos"]


def test_ordenar_por_severidad_lista_vacia():
    assert ordenar_por_severidad([]) == []
