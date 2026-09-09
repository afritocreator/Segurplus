"""Índice de Precios al Consumidor (IPC) Nacional, Nivel General.

Fuente: INDEC, vía la API pública de series de tiempo de datos.gob.ar
(Subsecretaría de Programación Macroeconómica). Base diciembre 2016 = 100.

Serie verificada manualmente el 2026-08-23: id "148.3_INIVELNAL_DICI_M_26",
"IPC. Nivel General Nacional. Base dic 2016. Mensual.", con datos hasta
2026-07-01. Documentación de la API: https://apis.datos.gob.ar/series/
"""

from __future__ import annotations

from datetime import date

import polars as pl
import requests

from core.macro.rutas import ruta_cache

SERIE_ID = "148.3_INIVELNAL_DICI_M_26"
API_URL = "https://apis.datos.gob.ar/series/api/series/"
CACHE_PATH = ruta_cache("ipc_nacional.parquet")


def descargar_ipc(*, timeout: int = 30) -> pl.DataFrame:
    """Baja la serie completa del IPC Nacional Nivel General desde datos.gob.ar.

    Devuelve un DataFrame con columnas `fecha` (date, primer día del mes) e
    `indice` (float, base dic-2016=100), ordenado ascendente por fecha.
    """
    params = {
        "ids": SERIE_ID,
        "format": "json",
        "limit": 5000,
        "sort": "asc",
    }
    resp = requests.get(API_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()["data"]
    if not data:
        raise ValueError(f"La API de datos.gob.ar devolvió una serie vacía para {SERIE_ID}")

    df = pl.DataFrame(data, schema=["fecha", "indice"], orient="row")
    df = df.with_columns(pl.col("fecha").str.to_date("%Y-%m-%d"))
    return df.sort("fecha")


def actualizar_cache(*, timeout: int = 30) -> pl.DataFrame:
    """Descarga el IPC y lo guarda en `data/macro/ipc_nacional.parquet`."""
    df = descargar_ipc(timeout=timeout)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(CACHE_PATH)
    return df


def leer_ipc(*, forzar_descarga: bool = False) -> pl.DataFrame:
    """Lee el IPC desde el cache local; si no existe, lo descarga primero."""
    if forzar_descarga or not CACHE_PATH.exists():
        return actualizar_cache()
    return pl.read_parquet(CACHE_PATH)


def ultima_fecha_disponible(df: pl.DataFrame | None = None) -> date:
    """Fecha del último dato de IPC disponible en la serie (cache o remota)."""
    df = df if df is not None else leer_ipc()
    return df["fecha"].max()
