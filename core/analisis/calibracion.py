"""Cálculos comparables para la calibración de conceptos no homologados."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from core.deflactor import a_pesos_constantes


@dataclass(frozen=True)
class ConceptoSinClasificarResumen:
    servicio: str | None
    descripcion: str
    score: float | None
    importe_real: float
    veces: int
    ultimo_periodo: date


def resumir_sin_clasificar(
    filas: list[tuple[str | None, str, float | None, float, date]],
    *,
    fecha_base: date,
    df_ipc: pl.DataFrame,
) -> tuple[list[ConceptoSinClasificarResumen], float]:
    """Agrupa importes en pesos constantes y devuelve filas y total sin clasificar.

    Cada fila contiene ``(servicio, descripción, score, importe, período)``.
    """
    acumulado: dict[tuple[str | None, str], list[object]] = {}
    for servicio, descripcion, score, importe, periodo in filas:
        clave = (servicio, descripcion)
        real = a_pesos_constantes(importe, periodo, fecha_base, df_ipc=df_ipc)
        if clave not in acumulado:
            acumulado[clave] = [score, 0.0, 0, periodo]
        actual = acumulado[clave]
        actual[0] = max((actual[0], score), key=lambda x: x is not None)
        actual[1] = float(actual[1]) + real
        actual[2] = int(actual[2]) + 1
        actual[3] = max(actual[3], periodo)
    resumen = [
        ConceptoSinClasificarResumen(s, d, v[0], float(v[1]), int(v[2]), v[3])
        for (s, d), v in acumulado.items()
    ]
    resumen.sort(key=lambda r: r.importe_real, reverse=True)
    return resumen, sum(r.importe_real for r in resumen)


def total_en_pesos_constantes(
    filas: list[tuple[float, date]], *, fecha_base: date, df_ipc: pl.DataFrame
) -> float:
    """Suma importes de períodos distintos en la misma moneda de referencia."""
    return sum(
        a_pesos_constantes(importe, periodo, fecha_base, df_ipc=df_ipc)
        for importe, periodo in filas
    )
