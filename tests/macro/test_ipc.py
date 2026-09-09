"""Smoke test de red (test_api_datos_gob_responde): confirma que la API
pública de datos.gob.ar sigue respondiendo y que la serie sigue siendo la
esperada (IPC Nacional Nivel General, base dic-2016). Si ese test falla,
la serie cambió o se cayó la API — no es un bug de nuestro código, hay
que revisar la fuente.

El resto de los tests de este archivo (docs/backlog.md B-09) NO usan la
red: verifican la lógica de cacheo/lectura con datos sintéticos y
`requests.get` mockeado, para que no dependan de que la API esté arriba
ni de que devuelva siempre lo mismo."""

from datetime import date

import polars as pl
import pytest
import requests

import core.macro.ipc as ipc_modulo
from core.macro.ipc import (
    SERIE_ID,
    actualizar_cache,
    descargar_ipc,
    leer_ipc,
    ultima_fecha_disponible,
)
from tests.macro.conftest import FakeResponse, saltar_si_no_hay_red


@pytest.mark.red_real
def test_api_datos_gob_responde():
    try:
        df = descargar_ipc(timeout=15)
    except requests.RequestException as exc:
        saltar_si_no_hay_red(exc, "datos.gob.ar / IPC Nacional")

    assert df.height > 100  # la serie histórica tiene cientos de meses
    assert set(df.columns) == {"fecha", "indice"}
    assert df["indice"].min() > 0
    # la serie tiene que venir ordenada ascendente por fecha
    assert df["fecha"].is_sorted()


def test_serie_id_es_la_esperada():
    assert SERIE_ID == "148.3_INIVELNAL_DICI_M_26"


@pytest.fixture
def payload_ipc_sintetico():
    return {"data": [["2024-01-01", 1000.0], ["2024-02-01", 1050.0], ["2024-03-01", 1100.0]]}


def test_actualizar_cache_y_leer_ipc_round_trip_sin_red(
    monkeypatch, tmp_path, payload_ipc_sintetico
):
    monkeypatch.setattr(ipc_modulo, "CACHE_PATH", tmp_path / "ipc.parquet")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload_ipc_sintetico))

    df_actualizado = actualizar_cache()
    assert df_actualizado.height == 3

    # leer_ipc() con cache ya existente NO tiene que llamar a requests.get.
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no debería llamar a la red")),
    )
    df_leido = leer_ipc()
    assert df_leido.equals(df_actualizado)


def test_leer_ipc_sin_cache_descarga(monkeypatch, tmp_path, payload_ipc_sintetico):
    monkeypatch.setattr(ipc_modulo, "CACHE_PATH", tmp_path / "no_existe_todavia.parquet")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload_ipc_sintetico))

    df = leer_ipc()
    assert df.height == 3
    assert (tmp_path / "no_existe_todavia.parquet").exists()


def test_ultima_fecha_disponible_con_df_dado():
    df = pl.DataFrame(
        {
            "fecha": [date(2024, 1, 1), date(2024, 3, 1), date(2024, 2, 1)],
            "indice": [100.0, 120.0, 110.0],
        }
    )
    assert ultima_fecha_disponible(df) == date(2024, 3, 1)
