"""Ajuste de valores nominales en pesos a moneda constante usando el IPC.

Por qué existe este módulo (ver docs/PLAN.md §4.2): un balance argentino leído
en pesos nominales miente. Una empresa puede "crecer 90% en ventas" y estar
cayendo en términos reales. Toda comparación entre dos fechas en `core/` tiene
que pasar por acá antes de mostrarse a un cliente.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from core.macro.ipc import leer_ipc


def _indice_en(df_ipc: pl.DataFrame, fecha: date) -> float:
    """Índice IPC del mes de `fecha` (se normaliza al día 1 del mes)."""
    fecha_mes = fecha.replace(day=1)
    fila = df_ipc.filter(pl.col("fecha") == fecha_mes)
    if fila.is_empty():
        disponibles = df_ipc["fecha"].min(), df_ipc["fecha"].max()
        raise ValueError(
            f"No hay IPC para {fecha_mes}. Rango disponible: {disponibles[0]} a {disponibles[1]}."
        )
    return fila["indice"].item()


def coeficiente_ajuste(
    fecha_origen: date, fecha_destino: date, *, df_ipc: pl.DataFrame | None = None
) -> float:
    """Coeficiente que multiplica un valor nominal de `fecha_origen` para
    expresarlo en pesos de `fecha_destino` (moneda constante).

    coeficiente = IPC(fecha_destino) / IPC(fecha_origen)
    """
    df_ipc = df_ipc if df_ipc is not None else leer_ipc()
    indice_origen = _indice_en(df_ipc, fecha_origen)
    indice_destino = _indice_en(df_ipc, fecha_destino)
    return indice_destino / indice_origen


def a_pesos_constantes(
    valor_nominal: float,
    fecha_origen: date,
    fecha_destino: date,
    *,
    df_ipc: pl.DataFrame | None = None,
) -> float:
    """Convierte un valor nominal en pesos de `fecha_origen` a pesos
    constantes de `fecha_destino` (poder adquisitivo de esa fecha).

    Ejemplo: ventas de enero 2025 en pesos constantes de julio 2026, para
    compararlas de verdad contra ventas nominales de julio 2026.
    """
    coef = coeficiente_ajuste(fecha_origen, fecha_destino, df_ipc=df_ipc)
    return valor_nominal * coef
