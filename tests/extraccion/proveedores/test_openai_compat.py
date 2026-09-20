"""Tests de core/extraccion/proveedores/openai_compat.py -- todos mockean
`requests.post`, ninguno pega contra una API real (no hay GROQ_API_KEY en
este entorno, ver docs/banco_extraccion.md)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from core.extraccion.gemini import ExtraccionError
from core.extraccion.proveedores.openai_compat import extraer_con_openai_compat

_FACTURA_JSON = {
    "emisor": "Proveedor de prueba",
    "cuit": "30-12345678-9",
    "servicio": "energia",
    "periodo_desde": "2026-07-01",
    "periodo_hasta": "2026-07-31",
    "fecha_emision": "2026-08-01",
    "fecha_vencimiento": "2026-08-15",
    "numero_comprobante": "A-1",
    "moneda": "ARS",
    "conceptos": [
        {
            "descripcion": "Cargo fijo",
            "cantidad": 1,
            "unidad": None,
            "precio_unitario": 100.0,
            "importe": 100.0,
        }
    ],
    "impuestos": [{"nombre": "IVA", "importe": 21.0}],
    "recargos": [],
    "creditos": [],
    "subtotal": 100.0,
    "total": 121.0,
}


def _respuesta_mock(contenido: str, status_ok: bool = True) -> MagicMock:
    respuesta = MagicMock()
    respuesta.raise_for_status = MagicMock()
    if not status_ok:
        respuesta.raise_for_status.side_effect = Exception("500 Server Error")
    respuesta.json.return_value = {"choices": [{"message": {"content": contenido}}]}
    return respuesta


def test_sin_clave_de_api_lanza_error_claro():
    with pytest.raises(ExtraccionError, match="Falta la clave de API"):
        extraer_con_openai_compat(
            b"%PDF-1.4",
            base_url="https://api.groq.com/openai/v1",
            modelo="algun-modelo",
            texto_extraido="texto",
        )


def test_lectura_exitosa_solo_con_texto():
    with patch(
        "requests.post", return_value=_respuesta_mock(json.dumps(_FACTURA_JSON))
    ) as mock_post:
        factura = extraer_con_openai_compat(
            b"%PDF-1.4",
            base_url="https://api.groq.com/openai/v1",
            modelo="algun-modelo",
            api_key="clave-de-prueba",
            texto_extraido="Cargo fijo 100",
            acepta_imagen=False,
        )
    assert factura.emisor == "Proveedor de prueba"
    assert factura.conceptos[0].importe == 100.0
    assert factura.modelo_extraccion == "algun-modelo"
    # El body mandado tiene que llevar la clave como Bearer y el modelo pedido.
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer clave-de-prueba"
    assert kwargs["json"]["model"] == "algun-modelo"


def test_respuesta_envuelta_en_cerca_de_markdown_se_parsea_igual():
    contenido = "```json\n" + json.dumps(_FACTURA_JSON) + "\n```"
    with patch("requests.post", return_value=_respuesta_mock(contenido)):
        factura = extraer_con_openai_compat(
            b"%PDF-1.4",
            base_url="https://api.groq.com/openai/v1",
            modelo="algun-modelo",
            api_key="clave-de-prueba",
            texto_extraido="Cargo fijo 100",
        )
    assert factura.total == 121.0


def test_json_invalido_lanza_extraccion_error_con_respuesta_cruda():
    with patch("requests.post", return_value=_respuesta_mock("esto no es JSON")):
        with pytest.raises(ExtraccionError) as excinfo:
            extraer_con_openai_compat(
                b"%PDF-1.4",
                base_url="https://api.groq.com/openai/v1",
                modelo="algun-modelo",
                api_key="clave-de-prueba",
                texto_extraido="algo",
            )
    assert excinfo.value.respuesta_cruda == "esto no es JSON"


def test_json_incompleto_lanza_extraccion_error():
    incompleto = {**_FACTURA_JSON, "conceptos": [{"descripcion": "Cargo fijo"}]}  # sin importe
    with patch("requests.post", return_value=_respuesta_mock(json.dumps(incompleto))):
        with pytest.raises(ExtraccionError):
            extraer_con_openai_compat(
                b"%PDF-1.4",
                base_url="https://api.groq.com/openai/v1",
                modelo="algun-modelo",
                api_key="clave-de-prueba",
                texto_extraido="algo",
            )


def test_error_de_red_se_envuelve():
    with patch("requests.post", side_effect=ConnectionError("no hay red")):
        with pytest.raises(ExtraccionError, match="no hay red"):
            extraer_con_openai_compat(
                b"%PDF-1.4",
                base_url="https://api.groq.com/openai/v1",
                modelo="algun-modelo",
                api_key="clave-de-prueba",
                texto_extraido="algo",
            )


def test_status_http_de_error_se_envuelve():
    with patch("requests.post", return_value=_respuesta_mock("", status_ok=False)):
        with pytest.raises(ExtraccionError, match="500 Server Error"):
            extraer_con_openai_compat(
                b"%PDF-1.4",
                base_url="https://api.groq.com/openai/v1",
                modelo="algun-modelo",
                api_key="clave-de-prueba",
                texto_extraido="algo",
            )


def test_acepta_imagen_manda_el_pdf_renderizado_como_data_uri():
    with (
        patch(
            "core.extraccion.proveedores.openai_compat.renderizar_paginas_png",
            return_value=[b"PNGFALSO"],
        ),
        patch(
            "requests.post", return_value=_respuesta_mock(json.dumps(_FACTURA_JSON))
        ) as mock_post,
    ):
        extraer_con_openai_compat(
            b"%PDF-1.4",
            base_url="https://api.groq.com/openai/v1",
            modelo="algun-modelo-vision",
            api_key="clave-de-prueba",
            acepta_imagen=True,
        )
    _, kwargs = mock_post.call_args
    contenido = kwargs["json"]["messages"][1]["content"]
    tipos = [parte["type"] for parte in contenido]
    assert "image_url" in tipos
    imagen = next(p for p in contenido if p["type"] == "image_url")
    assert imagen["image_url"]["url"].startswith("data:image/png;base64,")


def test_sin_imagen_ni_texto_lanza_error():
    with pytest.raises(ExtraccionError, match="No hay imagen ni texto"):
        extraer_con_openai_compat(
            b"%PDF-1.4",
            base_url="https://api.groq.com/openai/v1",
            modelo="algun-modelo",
            api_key="clave-de-prueba",
        )


def test_clave_desde_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "desde-el-entorno")
    with patch(
        "requests.post", return_value=_respuesta_mock(json.dumps(_FACTURA_JSON))
    ) as mock_post:
        extraer_con_openai_compat(
            b"%PDF-1.4",
            base_url="https://api.groq.com/openai/v1",
            modelo="algun-modelo",
            variable_entorno_clave="GROQ_API_KEY",
            texto_extraido="algo",
        )
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer desde-el-entorno"
