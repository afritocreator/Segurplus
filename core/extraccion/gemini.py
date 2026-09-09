"""Llamada al modelo (Gemini, tier gratuito) que lee una factura en PDF y
devuelve el JSON estructurado del esquema canónico.

Port directo del circuito de `app/api/invoices/parse/route.ts` de Kleric-
(mismo modelo, mismo mecanismo de `responseJsonSchema`), con dos detalles
que costaron caro ahí y se copian tal cual:

- **Fijar la versión del modelo, no el alias "latest"**: en las pruebas de
  Kleric-, el alias más nuevo devolvía 503 por demanda alta. `gemini-3.6-flash`
  es lo que Google recomendaba en ese momento (ver
  docs/decisiones/ADR-001-lectura-de-facturas.md) -- ANTES de tocar esto,
  revisar la documentación vigente de Gemini, no confiar en esta constante
  a ciegas si pasó mucho tiempo.
- **Tope de llamadas por hora**: sin esto, un bucle (o un mal uso del
  tablero) podría agotar la cuota gratuita sin ningún freno. Acá se
  implementa como un contador simple sobre el propio DuckDB de facturas
  (ver `core/almacenamiento.py`), no una tabla aparte como en Kleric-
  (que usaba Supabase) -- incluye el criterio, no la infraestructura.

Esta llamada NUNCA es la última palabra sobre un número: todo lo que
devuelve pasa por `core/extraccion/validacion.py` antes de entrar al
análisis (CLAUDE.md, "la regla que separa esto de confiar en la IA").
"""

from __future__ import annotations

import json
import os

from core.extraccion.esquema import FacturaExtraida, esquema_json_para_modelo, factura_desde_json

MODELO = "gemini-3.6-flash"  # fijo, no "latest" -- ver docstring del módulo
MAX_LLAMADAS_POR_HORA = 30

PROMPT_EXTRACCION = """\
Sos un asistente que lee facturas de proveedores de servicios (telefonía, energía, \
gas, agua, seguros, alquileres) de una empresa argentina y extrae los datos en el \
formato pedido. Reglas importantes:

- "conceptos" debe incluir cada línea de la factura (abonos, consumos, cargos fijos), \
  con la descripción tal como aparece impresa.
- "cantidad" es la cantidad facturada (líneas, chips, kWh, m³, minutos, GB). Si el \
  concepto no tiene cantidad explícita (un cargo fijo, un alquiler), usá 1.
- "precio_unitario" es importe / cantidad, "importe" el total de esa línea tal como \
  figura o se puede calcular.
- Los impuestos (IVA, Ingresos Brutos, tasas municipales) van en "impuestos", NO como \
  conceptos.
- Los cargos por mora, intereses o refacturación van en "recargos", NO como conceptos \
  normales -- son distintos de un consumo regular.
- Si un dato no está en la factura, usá null en vez de inventarlo.
- Los montos van en pesos argentinos, sin separador de miles, con punto decimal \
  (ej: 1234.50).
"""


class ExtraccionError(Exception):
    """La llamada al modelo falló o no devolvió un JSON parseable contra el
    esquema esperado. La factura debe ir a cuarentena, nunca al análisis."""


def extraer_con_gemini(pdf_bytes: bytes, *, api_key: str | None = None) -> FacturaExtraida:
    """Manda el PDF (binario, nativo -- preserva el layout de las tablas)
    a Gemini y devuelve la factura en el esquema canónico, SIN validar
    aritméticamente (eso es `core/extraccion/validacion.py`, a propósito
    separado).

    Import de `google.genai` diferido adentro de la función: así el resto
    del pipeline (validación, análisis, alertas) no depende de tener la
    librería instalada ni una API key configurada para correr sus tests.
    """
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ExtraccionError(
            "No hay GEMINI_API_KEY configurada. Sin la nube activada, usá el motor "
            "por reglas (Fase 6) o cargá la factura a mano."
        )

    from google import genai  # noqa: PLC0415 (import diferido, ver docstring)

    cliente = genai.Client(api_key=api_key)
    try:
        respuesta = cliente.models.generate_content(
            model=MODELO,
            contents=[
                PROMPT_EXTRACCION,
                {"inline_data": {"data": pdf_bytes, "mime_type": "application/pdf"}},
            ],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": esquema_json_para_modelo(),
            },
        )
        texto = respuesta.text
    except Exception as exc:  # noqa: BLE001 -- cualquier error de red/API va a cuarentena
        raise ExtraccionError(f"Error llamando a Gemini: {exc}") from exc

    if not texto:
        raise ExtraccionError("Gemini no devolvió texto en la respuesta.")

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ExtraccionError(f"La respuesta de Gemini no es JSON válido: {exc}") from exc

    return factura_desde_json(datos)
