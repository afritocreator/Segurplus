"""Test de la regla de oro (CLAUDE.md): valor calculado a mano.

IPC sintético: 100.0 en mayo-2026, 103.0 en junio-2026, 106.0 en
julio-2026 (mismo que usan los tests de la página de evolución,
tests/apps/test_evolucion_app.py). Un servicio facturó $10.000 en mayo y
$10.600 en julio (fecha_base) -- en plata de julio, mayo vale
10.000 * (106/100) = 10.600,00 exacto: la variación NOMINAL parece 6%,
pero descontada la inflación del período (6%) la variación REAL es 0%.
"""

from datetime import date

import polars as pl
import pytest

from core.analisis.serie import PuntoSerie, serie_nominal_y_real

IPC_SINTETICO = pl.DataFrame(
    {
        "fecha": [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)],
        "indice": [100.0, 103.0, 106.0],
    }
)


def test_serie_calculada_a_mano():
    totales = {date(2026, 5, 1): 10000.0, date(2026, 7, 1): 10600.0}
    serie = serie_nominal_y_real(totales, fecha_base=date(2026, 7, 1), df_ipc=IPC_SINTETICO)

    assert len(serie) == 2
    mayo, julio = serie
    assert mayo.total_nominal == pytest.approx(10000.0)
    # 10.000 en pesos de julio = 10.000 * (106/100) = 10.600,00
    assert mayo.total_real == pytest.approx(10600.0)
    assert julio.total_nominal == pytest.approx(10600.0)
    assert julio.total_real == pytest.approx(10600.0)


def test_en_fecha_base_total_real_igual_a_nominal():
    fecha_base = date(2026, 7, 1)
    totales = {date(2026, 5, 1): 5000.0, fecha_base: 8000.0}
    serie = serie_nominal_y_real(totales, fecha_base=fecha_base, df_ipc=IPC_SINTETICO)
    punto_base = next(p for p in serie if p.periodo == fecha_base)
    assert punto_base.total_real == pytest.approx(punto_base.total_nominal)


def test_serie_queda_ordenada_por_periodo():
    totales = {
        date(2026, 7, 1): 1.0,
        date(2026, 5, 1): 1.0,
        date(2026, 6, 1): 1.0,
    }
    serie = serie_nominal_y_real(totales, fecha_base=date(2026, 7, 1), df_ipc=IPC_SINTETICO)
    assert [p.periodo for p in serie] == [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)]


def test_punto_serie_es_un_dataclass_simple():
    p = PuntoSerie(periodo=date(2026, 7, 1), total_nominal=100.0, total_real=100.0)
    assert p.periodo == date(2026, 7, 1)
