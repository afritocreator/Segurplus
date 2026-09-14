"""Tests de core/extraccion/gemini.py que NO pegan contra la red -- el
camino feliz con la API real se marca `red_real` y se skipea sin
GEMINI_API_KEY (ver tests/extraccion/conftest.py)."""

import pytest

from core.extraccion.esquema import esquema_json_para_modelo
from core.extraccion.gemini import PROMPT_EXTRACCION, ExtraccionError, extraer_con_gemini


def test_sin_api_key_lanza_extraccion_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ExtraccionError, match="GEMINI_API_KEY"):
        extraer_con_gemini(b"contenido-pdf-falso", api_key=None)


# --- B-3: el prompt explica la línea de impuesto con dos montos y avisa
# --- sobre layouts a dos columnas (docs/auditoria-2026-09-piloto.md) -------


def test_prompt_explica_base_imponible_vs_importe_del_impuesto():
    assert "base imponible" in PROMPT_EXTRACCION.lower()
    assert "más a la derecha" in PROMPT_EXTRACCION.lower()


def test_prompt_avisa_sobre_layouts_a_dos_columnas():
    assert "dos columnas" in PROMPT_EXTRACCION.lower()


class _RespuestaFake:
    text = '{"conceptos": [], "moneda": "ARS"}'


class _ModelsFake:
    def __init__(self):
        self.llamadas: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.llamadas.append({"model": model, "contents": contents, "config": config})
        return _RespuestaFake()


class _ClienteFake:
    instancias: list["_ClienteFake"] = []

    def __init__(self, api_key):
        self.api_key = api_key
        self.models = _ModelsFake()
        _ClienteFake.instancias.append(self)


def _parchear_cliente_fake(monkeypatch):
    import google.genai as genai_mod

    _ClienteFake.instancias = []
    monkeypatch.setattr(genai_mod, "Client", _ClienteFake)
    return _ClienteFake


def test_sin_texto_extraido_manda_solo_prompt_y_pdf(monkeypatch):
    _parchear_cliente_fake(monkeypatch)

    extraer_con_gemini(b"pdf-fake", api_key="fake")

    contents = _ClienteFake.instancias[0].models.llamadas[0]["contents"]
    assert contents[0] == PROMPT_EXTRACCION
    assert len(contents) == 2  # prompt + PDF, nada más


def test_con_texto_extraido_lo_manda_como_contenido_adicional(monkeypatch):
    """docs/auditoria-2026-09-piloto.md, hallazgo B-3: además del PDF
    nativo, se le pasa el texto plano que `pdfplumber` ya sacó -- una
    segunda vista para facturas con layout a dos columnas."""
    _parchear_cliente_fake(monkeypatch)

    extraer_con_gemini(b"pdf-fake", api_key="fake", texto_extraido="TOTAL A PAGAR $ 3.696,19")

    contents = _ClienteFake.instancias[0].models.llamadas[0]["contents"]
    assert len(contents) == 3  # prompt + texto extraído + PDF
    assert "TOTAL A PAGAR $ 3.696,19" in contents[1]
    assert contents[-1] == {"inline_data": {"data": b"pdf-fake", "mime_type": "application/pdf"}}


def test_texto_extraido_vacio_se_comporta_como_ausente(monkeypatch):
    _parchear_cliente_fake(monkeypatch)

    extraer_con_gemini(b"pdf-fake", api_key="fake", texto_extraido="")

    contents = _ClienteFake.instancias[0].models.llamadas[0]["contents"]
    assert len(contents) == 2


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
