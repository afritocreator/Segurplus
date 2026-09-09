"""Test de la regla de oro (CLAUDE.md): valor calculado a mano.

IPC sintético: 100.0 en ene-2026, 112.0 en jul-2026 (12% de inflación
acumulada en el período). Una factura pasó de $10.000 (ene) a $11.500 (jul).

A mano:
  variación nominal = 11500/10000 - 1 = 0.15 (15%)
  10.000 en pesos de julio = 10.000 * (112/100) = 11.200
  variación real = 11500/11200 - 1 = 0.026785... (~2.68%)

Es decir: un aumento "del 15%" es en realidad un aumento real de ~2.7% una
vez descontada la inflación del período -- muy distinto de lo que parece a
simple vista.
"""

from datetime import date

import polars as pl
import pytest

from core.analisis.real import inflacion_del_periodo, variacion_real

IPC_SINTETICO = pl.DataFrame(
    {
        "fecha": [date(2026, 1, 1), date(2026, 7, 1)],
        "indice": [100.0, 112.0],
    }
)


def test_variacion_real_calculada_a_mano():
    r = variacion_real(10000.0, date(2026, 1, 1), 11500.0, date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert r.variacion_nominal_pct == pytest.approx(0.15)
    assert r.importe_0_en_pesos_de_1 == pytest.approx(11200.0)
    assert r.variacion_real_pct == pytest.approx(0.0267857142857, abs=1e-9)


def test_aumento_nominal_por_debajo_del_ipc_es_baja_real():
    # Nominal sube 8%, pero IPC del período fue 12% -> en términos reales bajó.
    r = variacion_real(10000.0, date(2026, 1, 1), 10800.0, date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert r.variacion_nominal_pct == pytest.approx(0.08)
    assert r.variacion_real_pct < 0


def test_sin_variacion_nominal_es_baja_real_pura():
    # Si el importe no cambió pero hubo inflación, en términos reales bajó exactamente
    # el 12% (la misma inflación del período).
    r = variacion_real(10000.0, date(2026, 1, 1), 10000.0, date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert r.variacion_nominal_pct == pytest.approx(0.0)
    assert r.variacion_real_pct == pytest.approx(10000.0 / 11200.0 - 1)


def test_importe_cero_lanza_error_explicito():
    with pytest.raises(ValueError, match="importe_0"):
        variacion_real(0.0, date(2026, 1, 1), 100.0, date(2026, 7, 1), df_ipc=IPC_SINTETICO)


def test_inflacion_del_periodo_calculada_a_mano():
    # IPC 100 -> 112: inflación acumulada = 112/100 - 1 = 0.12 (12%)
    inflacion = inflacion_del_periodo(date(2026, 1, 1), date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert inflacion == pytest.approx(0.12)


def test_inflacion_del_periodo_no_es_la_resta_de_nominal_menos_real():
    # docs/auditoria-2026-09.md, hallazgo A-1: antes del fix, evolucion.py calculaba
    # `variacion_real_pct - variacion_nominal_pct`, que da un número NEGATIVO (el
    # opuesto de la inflación), no la inflación en sí. Este test fija el comportamiento
    # correcto para que ese bug no pueda volver a colarse sin que un test lo note.
    r = variacion_real(10000.0, date(2026, 1, 1), 11500.0, date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    resta_incorrecta = r.variacion_real_pct - r.variacion_nominal_pct
    inflacion_correcta = inflacion_del_periodo(
        date(2026, 1, 1), date(2026, 7, 1), df_ipc=IPC_SINTETICO
    )

    assert resta_incorrecta < 0  # el bug daba esto -- negativo
    assert inflacion_correcta == pytest.approx(0.12)  # lo correcto es esto -- positivo
    assert resta_incorrecta != pytest.approx(inflacion_correcta)
