"""Tests de core/extraccion/gemini.py que NO pegan contra la red -- el
camino feliz con la API real se marca `red_real` y se skipea sin
GEMINI_API_KEY (ver tests/extraccion/conftest.py)."""

import pytest

from core.extraccion.esquema import esquema_json_para_modelo
from core.extraccion.gemini import ExtraccionError, extraer_con_gemini


def test_sin_api_key_lanza_extraccion_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ExtraccionError, match="GEMINI_API_KEY"):
        extraer_con_gemini(b"contenido-pdf-falso", api_key=None)


def test_esquema_json_tiene_los_campos_clave():
    esquema = esquema_json_para_modelo()
    propiedades = esquema["properties"]
    for campo in ("emisor", "servicio", "conceptos", "impuestos", "recargos", "total"):
        assert campo in propiedades
    assert esquema["properties"]["conceptos"]["type"] == "array"
