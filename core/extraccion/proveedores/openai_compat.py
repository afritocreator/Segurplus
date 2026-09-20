"""Adaptador genérico para cualquier proveedor de inferencia compatible con
la especificación de API de OpenAI (`/chat/completions`) -- Groq, Cerebras,
SambaNova, OpenRouter, todos según el `Informe Técnico Semanal: APIs
Gratuitas de Modelos de Lenguaje` (18/09/2026) que motivó este bloque: los
cuatro implementan la misma forma, solo cambian `base_url`, `modelo` y la
variable de entorno de la clave.

No usa el paquete `openai` (no está en el stack cerrado, y agregarlo solo
para esto sería una dependencia nueva sin necesidad real): arma la
solicitud HTTP a mano con `requests`, que ya es una dependencia del
proyecto.

Reutiliza `core.extraccion.gemini.PROMPT_EXTRACCION` (las reglas de
extracción no dependen del proveedor) y
`core.extraccion.esquema.esquema_json_para_modelo` -- acá van dentro del
prompt como texto en vez de como `response_json_schema` nativo (Gemini
tiene ese mecanismo; no todos los proveedores compatibles con OpenAI lo
soportan de forma confiable), pidiendo `response_format: json_object` como
red de contención adicional.

IMPORTANTE: este adaptador todavía no se probó contra ninguna API real
(este entorno de desarrollo no tiene GROQ_API_KEY ni ninguna clave
equivalente) -- ver docs/banco_extraccion.md. Antes de usarlo para leer
facturas de verdad, correr `scripts/banco_extraccion.py --proveedor
groq_scout` con una clave real y mirar la tabla resultante.
"""

from __future__ import annotations

import base64
import json
import os

import requests

from core.extraccion.esquema import FacturaExtraida, esquema_json_para_modelo, factura_desde_json
from core.extraccion.gemini import PROMPT_EXTRACCION, ExtraccionError
from core.extraccion.proveedores.render import renderizar_paginas_png


def _extraer_json(texto: str) -> dict:
    """Algunos modelos, aun con `response_format: json_object`, envuelven
    la respuesta en una cerca de código Markdown (```json ... ```) -- se
    saca antes de parsear, en vez de fallar por un detalle de formato que
    no cambia el contenido."""
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = limpio.strip("`")
        if limpio.startswith("json"):
            limpio = limpio[len("json") :]
        limpio = limpio.strip()
    return json.loads(limpio)


def extraer_con_openai_compat(
    pdf_bytes: bytes,
    *,
    base_url: str,
    modelo: str,
    api_key: str | None = None,
    variable_entorno_clave: str | None = None,
    texto_extraido: str | None = None,
    acepta_imagen: bool = False,
    timeout_segundos: float = 30.0,
) -> FacturaExtraida:
    """Lee una factura con un proveedor compatible con la API de OpenAI.
    Mismo contrato que `core.extraccion.gemini.extraer_con_gemini`: nunca
    valida aritméticamente (eso es `core/extraccion/validacion.py`), y
    cualquier fallo -- de red, de cuota, de forma del JSON -- se envuelve
    en `ExtraccionError`."""
    api_key = api_key or (
        os.environ.get(variable_entorno_clave) if variable_entorno_clave else None
    )
    if not api_key:
        nombre_variable = variable_entorno_clave or "(sin variable configurada)"
        raise ExtraccionError(
            f"Falta la clave de API para {base_url} (variable {nombre_variable})."
        )

    instrucciones = (
        PROMPT_EXTRACCION
        + "\n\nDevolvé SOLO un objeto JSON que cumpla exactamente este JSON Schema, "
        "sin texto ni explicación antes ni después, sin cercas de código Markdown:\n\n"
        + json.dumps(esquema_json_para_modelo(), ensure_ascii=False)
    )

    contenido_usuario: list[dict] = []
    if acepta_imagen:
        paginas_png = renderizar_paginas_png(pdf_bytes)  # ya lanza ExtraccionError si falla
        for png in paginas_png:
            b64 = base64.b64encode(png).decode("ascii")
            contenido_usuario.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            )
    if texto_extraido:
        contenido_usuario.append(
            {
                "type": "text",
                "text": (
                    "Texto plano extraído del mismo PDF con una herramienta de lectura de "
                    "texto (puede tener el orden de columnas mezclado -- usalo como apoyo, "
                    "priorizá lo que ves en la imagen adjunta si la hay):\n\n" + texto_extraido
                ),
            }
        )
    if not contenido_usuario:
        raise ExtraccionError(
            "No hay imagen ni texto extraído para mandarle al modelo "
            "(acepta_imagen=False y texto_extraido vacío)."
        )

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": instrucciones},
            {"role": "user", "content": contenido_usuario},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        respuesta = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout_segundos,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        texto = cuerpo["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 -- cualquier error de red/API/forma se envuelve
        raise ExtraccionError(f"Error llamando a {base_url} ({modelo}): {exc}") from exc

    if not texto:
        raise ExtraccionError(f"{base_url} ({modelo}) no devolvió texto en la respuesta.")

    try:
        datos = _extraer_json(texto)
    except json.JSONDecodeError as exc:
        raise ExtraccionError(
            f"La respuesta de {base_url} ({modelo}) no es JSON válido: {exc}",
            respuesta_cruda=texto,
        ) from exc

    try:
        factura = factura_desde_json(datos)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExtraccionError(
            f"El JSON de {base_url} ({modelo}) no tiene la forma esperada "
            f"(falta o está mal tipado: {exc})",
            respuesta_cruda=texto,
        ) from exc

    factura.modelo_extraccion = modelo
    factura.respuesta_extraida = texto
    return factura
