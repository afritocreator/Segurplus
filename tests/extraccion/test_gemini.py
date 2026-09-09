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


def test_servicio_tiene_enum_con_los_valores_conocidos():
    # docs/auditoria-2026-09.md, hallazgo A-3: antes "servicio" era texto
    # libre (solo una descripción), así que un valor fuera de los nombres de
    # data/conceptos/*.yaml (ej. "internet" en vez de "telefonia") hacía que
    # cargar_diccionario(servicio) no encontrara ningún archivo propio y la
    # factura perdiera toda la homologación específica salvo comunes.yaml.
    from core.extraccion.esquema import SERVICIOS_CONOCIDOS

    esquema = esquema_json_para_modelo()
    assert esquema["properties"]["servicio"]["enum"] == [*SERVICIOS_CONOCIDOS, None]


def test_todo_servicio_conocido_tiene_yaml_propio_salvo_el_catch_all():
    # "otro" es el único valor del enum sin archivo propio a propósito (una
    # factura de un servicio no contemplado todavía homologa solo contra
    # comunes.yaml, en vez de fallar) -- todos los demás tienen que tener su
    # data/conceptos/<servicio>.yaml, si no, volvemos a A-3 por otra vía.
    from pathlib import Path

    from core.extraccion.esquema import SERVICIOS_CONOCIDOS

    directorio = Path(__file__).resolve().parent.parent.parent / "data" / "conceptos"
    for servicio in SERVICIOS_CONOCIDOS:
        if servicio == "otro":
            continue
        assert (directorio / f"{servicio}.yaml").exists(), f"falta data/conceptos/{servicio}.yaml"
