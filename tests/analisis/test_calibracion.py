"""Test de la regla de oro (CLAUDE.md): valor calculado a mano.

Mismo IPC sintético que tests/analisis/test_serie.py: 100.0 en mayo-2026,
103.0 en junio-2026, 106.0 en julio-2026.
"""

from datetime import date

import polars as pl
import pytest

from core.analisis.calibracion import (
    MetricasProveedor,
    metricas_por_proveedor,
    resumir_sin_clasificar,
    total_en_pesos_constantes,
)

IPC_SINTETICO = pl.DataFrame(
    {
        "fecha": [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)],
        "indice": [100.0, 103.0, 106.0],
    }
)


def test_resumir_agrupa_y_deflacta_calculado_a_mano():
    # Misma (servicio, descripción) en dos meses: $10.000 en mayo y $10.600
    # en julio (fecha_base) -- en plata de julio, mayo vale
    # 10.000 * (106/100) = 10.600,00 exacto (mismo cálculo que test_serie.py).
    filas = [
        ("telefonia", "Cargo raro", 0.30, 10000.0, date(2026, 5, 1)),
        ("telefonia", "Cargo raro", 0.30, 10600.0, date(2026, 7, 1)),
    ]
    resumen, total = resumir_sin_clasificar(
        filas, fecha_base=date(2026, 7, 1), df_ipc=IPC_SINTETICO
    )

    assert len(resumen) == 1
    fila = resumen[0]
    assert fila.servicio == "telefonia"
    assert fila.descripcion == "Cargo raro"
    assert fila.veces == 2
    assert fila.ultimo_periodo == date(2026, 7, 1)
    assert fila.importe_real == pytest.approx(10600.0 + 10600.0)
    assert total == pytest.approx(fila.importe_real)


def test_resumir_toma_el_score_maximo_no_el_primero():
    """El bug encontrado en la revisión del Bloque de Codex: `max((actual,
    nuevo), key=lambda x: x is not None)` no calcula el máximo real -- entre
    dos valores no nulos, ambas keys valen True, hay empate, y `max` devuelve
    el PRIMERO. Verificado con (0.30, 0.90): guardaba 0.30. Acá la primera
    fila trae 0.30 y la segunda 0.90 -- el score guardado tiene que ser 0.90,
    el máximo real, no el de la primera fila vista."""
    filas = [
        ("telefonia", "Cargo raro", 0.30, 1000.0, date(2026, 5, 1)),
        ("telefonia", "Cargo raro", 0.90, 1000.0, date(2026, 5, 1)),
    ]
    resumen, _total = resumir_sin_clasificar(
        filas, fecha_base=date(2026, 5, 1), df_ipc=IPC_SINTETICO
    )
    assert resumen[0].score == pytest.approx(0.90)


def test_resumir_toma_el_score_maximo_en_el_orden_inverso():
    """Mismo caso que arriba, pero con el score alto PRIMERO -- el bug
    original pasaba desapercibido en este orden porque `max` devuelve el
    primero de un empate, y acá el primero ya es el máximo. Confirma que la
    corrección no depende del orden de llegada de las filas."""
    filas = [
        ("telefonia", "Cargo raro", 0.90, 1000.0, date(2026, 5, 1)),
        ("telefonia", "Cargo raro", 0.30, 1000.0, date(2026, 5, 1)),
    ]
    resumen, _total = resumir_sin_clasificar(
        filas, fecha_base=date(2026, 5, 1), df_ipc=IPC_SINTETICO
    )
    assert resumen[0].score == pytest.approx(0.90)


def test_resumir_score_none_no_gana_ni_pierde_por_error():
    """Una fila sin score medido (None, filas guardadas antes de que existiera
    `score_homologacion`) no debe hacer que se pierda un score real medido
    en otra fila del mismo concepto, en ningún orden."""
    filas_none_primero = [
        ("telefonia", "Cargo raro", None, 1000.0, date(2026, 5, 1)),
        ("telefonia", "Cargo raro", 0.55, 1000.0, date(2026, 5, 1)),
    ]
    resumen, _ = resumir_sin_clasificar(
        filas_none_primero, fecha_base=date(2026, 5, 1), df_ipc=IPC_SINTETICO
    )
    assert resumen[0].score == pytest.approx(0.55)

    filas_none_segundo = [
        ("telefonia", "Cargo raro", 0.55, 1000.0, date(2026, 5, 1)),
        ("telefonia", "Cargo raro", None, 1000.0, date(2026, 5, 1)),
    ]
    resumen, _ = resumir_sin_clasificar(
        filas_none_segundo, fecha_base=date(2026, 5, 1), df_ipc=IPC_SINTETICO
    )
    assert resumen[0].score == pytest.approx(0.55)


def test_resumir_ordena_por_importe_real_descendente():
    filas = [
        ("telefonia", "Chico", 0.5, 100.0, date(2026, 5, 1)),
        ("telefonia", "Grande", 0.5, 5000.0, date(2026, 5, 1)),
    ]
    resumen, _total = resumir_sin_clasificar(
        filas, fecha_base=date(2026, 5, 1), df_ipc=IPC_SINTETICO
    )
    assert [f.descripcion for f in resumen] == ["Grande", "Chico"]


def test_total_en_pesos_constantes_calculado_a_mano():
    # Mismo cálculo que test_serie.py: 10.000 de mayo en pesos de julio = 10.600.
    filas = [(10000.0, date(2026, 5, 1)), (10600.0, date(2026, 7, 1))]
    total = total_en_pesos_constantes(filas, fecha_base=date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert total == pytest.approx(21200.0)


# --- MetricasProveedor: tasas calculadas a mano (Bloque 5) -----------------


def test_tasa_cuarentena_calculada_a_mano():
    # 3 de 4 PDFs vistos de este proveedor NO cerraron -> 75%.
    m = MetricasProveedor(
        emisor="Movistar",
        facturas_cargadas=1,
        facturas_en_cuarentena=3,
        conceptos_totales=2,
        conceptos_sin_homologar=1,
        importe_sin_homologar=500.0,
    )
    assert m.total_facturas_vistas == 4
    assert m.tasa_cuarentena == pytest.approx(0.75)
    assert m.tasa_sin_homologar == pytest.approx(0.5)


def test_tasa_cuarentena_none_sin_ningun_pdf_visto():
    m = MetricasProveedor(
        emisor="(sin emisor)",
        facturas_cargadas=0,
        facturas_en_cuarentena=0,
        conceptos_totales=0,
        conceptos_sin_homologar=0,
        importe_sin_homologar=0.0,
    )
    assert m.tasa_cuarentena is None
    assert m.tasa_sin_homologar is None


def test_tasa_sin_homologar_none_sin_conceptos_pero_con_cuarentena():
    # Un proveedor que solo tiene facturas en cuarentena todavía no tiene
    # NINGÚN concepto cargado -- tasa_sin_homologar no debe dar 0/0 -> None,
    # no 0.0 (0.0 significaría "homologó todo", que es distinto de "no hay
    # datos todavía").
    m = MetricasProveedor(
        emisor="Proveedor Nuevo",
        facturas_cargadas=0,
        facturas_en_cuarentena=2,
        conceptos_totales=0,
        conceptos_sin_homologar=0,
        importe_sin_homologar=0.0,
    )
    assert m.tasa_cuarentena == pytest.approx(1.0)
    assert m.tasa_sin_homologar is None


def test_metricas_por_proveedor_ordena_por_tasa_de_cuarentena_descendente():
    filas = [
        ("Bueno", 10, 0, 10, 0, 0.0),  # tasa_cuarentena = 0
        ("Malo", 1, 3, 1, 0, 0.0),  # tasa_cuarentena = 0.75
        ("Regular", 2, 1, 2, 0, 0.0),  # tasa_cuarentena = 0.333...
    ]
    ordenadas = metricas_por_proveedor(filas)
    assert [m.emisor for m in ordenadas] == ["Malo", "Regular", "Bueno"]
