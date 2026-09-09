"""Test de la regla de oro (CLAUDE.md): valor calculado a mano.

Caso sintético controlado, sin red: IPC pasa de 100.0 (ene-2025) a 250.0
(jul-2026), es decir la inflación acumulada en el período es del 150%.

A mano: $1.000.000 nominales de enero 2025 equivalen a
1.000.000 * (250.0 / 100.0) = $2.500.000 en pesos de julio 2026.
El coeficiente de ajuste tiene que dar exactamente 2.5.
"""

from datetime import date

import polars as pl
import pytest

from core.deflactor import a_pesos_constantes, coeficiente_ajuste

IPC_SINTETICO = pl.DataFrame(
    {
        "fecha": [date(2025, 1, 1), date(2025, 6, 1), date(2026, 7, 1)],
        "indice": [100.0, 150.0, 250.0],
    }
)


def test_coeficiente_ajuste_valor_conocido():
    coef = coeficiente_ajuste(date(2025, 1, 1), date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert coef == pytest.approx(2.5)


def test_a_pesos_constantes_valor_conocido():
    resultado = a_pesos_constantes(
        1_000_000, date(2025, 1, 1), date(2026, 7, 1), df_ipc=IPC_SINTETICO
    )
    assert resultado == pytest.approx(2_500_000)


def test_coeficiente_identidad_misma_fecha():
    coef = coeficiente_ajuste(date(2025, 6, 1), date(2025, 6, 1), df_ipc=IPC_SINTETICO)
    assert coef == pytest.approx(1.0)


def test_coeficiente_hacia_atras_es_inverso():
    adelante = coeficiente_ajuste(date(2025, 1, 1), date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    atras = coeficiente_ajuste(date(2026, 7, 1), date(2025, 1, 1), df_ipc=IPC_SINTETICO)
    assert adelante * atras == pytest.approx(1.0)


def test_fecha_sin_dato_lanza_error_claro():
    with pytest.raises(ValueError, match="No hay IPC"):
        coeficiente_ajuste(date(2030, 1, 1), date(2025, 1, 1), df_ipc=IPC_SINTETICO)


def test_normaliza_dia_del_mes():
    # El 15 de enero 2025 tiene que resolver al mismo índice que el día 1
    coef_dia_1 = coeficiente_ajuste(date(2025, 1, 1), date(2025, 6, 1), df_ipc=IPC_SINTETICO)
    coef_dia_15 = coeficiente_ajuste(date(2025, 1, 15), date(2025, 6, 1), df_ipc=IPC_SINTETICO)
    assert coef_dia_1 == pytest.approx(coef_dia_15)
