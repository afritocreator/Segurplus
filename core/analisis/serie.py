"""Serie temporal de un servicio a lo largo de TODOS los períodos cargados
-- no solo los dos que se eligen para comparar en `evolucion.py`.

Por qué existe: la comparación de dos períodos responde "¿cuánto cambió
entre marzo y abril?", pero no deja ver la tendencia (¿viene subiendo hace
seis meses o fue un salto puntual?). Esta serie muestra el total nominal
Y el total en pesos constantes (deflactado por IPC) de cada período, para
que la tendencia real -- no inflada por la inflación -- se vea de un
vistazo.

No reimplementa nada: reusa `core.deflactor.a_pesos_constantes`, ya
testeado, igual que `core.analisis.real`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from core.deflactor import a_pesos_constantes


@dataclass
class PuntoSerie:
    periodo: date
    total_nominal: float
    total_real: float  # en pesos constantes de `fecha_base`


def serie_nominal_y_real(
    totales: dict[date, float], *, fecha_base: date, df_ipc: pl.DataFrame | None = None
) -> list[PuntoSerie]:
    """Convierte `{periodo: total_nominal}` en una lista de `PuntoSerie`
    ordenada por `periodo`, agregando el total en pesos constantes de
    `fecha_base` (típicamente el período más reciente cargado -- "en
    plata de hoy", que es como se piensa habitualmente, no en pesos de
    hace un año).

    En el punto que coincide con `fecha_base`, `total_real == total_nominal`
    exactamente (el coeficiente de ajuste contra sí mismo es 1)."""
    return [
        PuntoSerie(
            periodo=periodo,
            total_nominal=total,
            total_real=a_pesos_constantes(total, periodo, fecha_base, df_ipc=df_ipc),
        )
        for periodo, total in sorted(totales.items())
    ]
