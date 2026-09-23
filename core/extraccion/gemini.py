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
  tablero) podría agotar la cuota gratuita sin ningún freno. El número vive
  en `data/operacion.yaml` (`core.operacion.max_llamadas_gemini_por_hora`,
  CLAUDE.md: nunca hardcodeado), y se hace cumplir contando llamadas REALES
  en su propia tabla (`intentos_gemini`, ver `core/almacenamiento.py` y
  `core/pipeline.py`) -- SÍ una tabla aparte, a diferencia de lo que decía
  antes acá: un conteo sobre `facturas`/`cuarentena` subestimaba el uso
  real, porque una llamada que fallaba (`ExtraccionError`) no dejaba fila
  en ninguna de las dos (docs/auditoria-2026-09-facturas-reales.md, hallazgo B-4).

Esta llamada NUNCA es la última palabra sobre un número: todo lo que
devuelve pasa por `core/extraccion/validacion.py` antes de entrar al
análisis (CLAUDE.md, "la regla que separa esto de confiar en la IA").
"""

from __future__ import annotations

import json
import os

from core.extraccion.esquema import FacturaExtraida, esquema_json_para_modelo, factura_desde_json

MODELO = "gemini-3.6-flash"  # fijo, no "latest" -- ver docstring del módulo
# docs/auditoria-2026-09-facturas-reales.md, hallazgo B-3: el prompt sumó dos reglas
# (base imponible vs. importe en una línea de impuesto, y no mezclar
# columnas) y ahora también recibe el texto plano del PDF -- subir la
# versión documenta que una extracción vieja se hizo con reglas distintas.
VERSION_PROMPT = "2026-09-piloto-2"
# Bloque 3 del plan de rediseño de septiembre 2026 (docs/estado.md): el
# esquema JSON ganó el campo "concepto_sugerido" por línea de concepto --
# subir la versión documenta que una extracción vieja nunca tuvo esa
# oportunidad, para no confundir "el modelo no supo clasificar" con "el
# modelo nunca pudo".
VERSION_ESQUEMA = "2026-09-concepto-sugerido-1"

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
- Una línea de impuesto suele traer DOS montos: primero la BASE IMPONIBLE (el importe \
  sobre el que se calcula el impuesto, típicamente el subtotal o una parte de él) y \
  DESPUÉS el importe del impuesto en sí, que suele ser un número más chico (resultado \
  de aplicarle el porcentaje del impuesto a la base). El campo "importe" de cada \
  impuesto es SIEMPRE el segundo monto, el que va MÁS A LA DERECHA en la línea -- \
  nunca la base imponible. Ejemplo: la línea "I.V.A. (27,000%)  2.301,90  621,51" \
  tiene base imponible 2.301,90 e importe de IVA 621,51 (621,51 / 2.301,90 = 0,27, \
  el 27% que dice el nombre) -- "importe" es 621,51, no 2.301,90.
- Los cargos por mora, intereses o refacturación van en "recargos", NO como conceptos \
  normales -- son distintos de un consumo regular.
- Las bonificaciones, descuentos y notas de crédito van en "creditos", con importe positivo; \
  reducen el total y nunca se mezclan con consumo normal.
- Si la factura tiene el diseño en DOS COLUMNAS (dos bloques de datos uno al lado del \
  otro, en vez de una sola lista de arriba a abajo), prestá atención a no mezclar un \
  concepto de una columna con el monto de la otra -- guiate por la posición visual del \
  PDF, no por el orden en que puede aparecer el texto plano si se adjunta.
- Si un dato no está en la factura, usá null en vez de inventarlo.
- Los montos van en pesos argentinos, sin separador de miles, con punto decimal \
  (ej: 1234.50).
- Cada concepto tiene un campo "concepto_sugerido", con una lista fija de valores \
  permitidos: si la descripción corresponde CLARAMENTE a uno de esos conceptos \
  conocidos, elegilo; si no estás seguro, o es un tipo de cargo que no está en la \
  lista, usá null -- no elijas el más parecido "por las dudas".
"""


class ExtraccionError(Exception):
    """La llamada al modelo falló, no devolvió un JSON parseable, o el JSON
    que devolvió no tiene la forma que `factura_desde_json` necesita. La
    factura debe ir a cuarentena, nunca al análisis.

    `respuesta_cruda` (docs/auditoria-2026-09-facturas-reales.md, hallazgo
    C-2): el texto que Gemini devolvió, si llegó a devolver alguno -- para
    que `core/pipeline.py` lo pueda pasar a `registrar_intento_gemini` y
    quede disponible en el diagnóstico de B-5 incluso cuando el JSON era
    válido pero le faltaba un campo. `None` cuando el fallo fue ANTES de
    tener una respuesta (sin API key, error de red)."""

    def __init__(self, mensaje: str, *, respuesta_cruda: str | None = None) -> None:
        super().__init__(mensaje)
        self.respuesta_cruda = respuesta_cruda


_MARCADORES_ERROR_TRANSITORIO = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "TIMEOUT")


def es_error_transitorio(exc: ExtraccionError) -> bool:
    """True si `exc` viene de un problema pasajero del lado de Gemini
    (demanda alta -- 503 UNAVAILABLE --, cuota agotada por un momento --
    429 RESOURCE_EXHAUSTED --, o un timeout de red), donde reintentar
    tiene sentido. False para errores permanentes (sin API key, JSON mal
    formado, campo faltante) que van a fallar exactamente igual la
    próxima vez -- reintentarlos solo gasta cupo de la API sin ganar nada.

    No hay un tipo de excepción distinto para cada caso (`extraer_con_gemini`
    envuelve cualquier excepción de la librería en el mismo `ExtraccionError`
    con el mensaje original adentro, ver docstring de esa función) -- por
    eso la detección es textual, sobre el mismo mensaje que ya se le
    muestra a la persona (docs/auditoria-2026-09-web.md, E-6: en la única
    corrida real contra Gemini, 503 UNAVAILABLE fue justamente el error que
    apareció, varias veces, y el pipeline no reintentaba ninguna)."""
    texto = str(exc).upper()
    return any(marcador in texto for marcador in _MARCADORES_ERROR_TRANSITORIO)


def extraer_con_gemini(
    pdf_bytes: bytes, *, api_key: str | None = None, texto_extraido: str | None = None
) -> FacturaExtraida:
    """Manda el PDF (binario, nativo -- preserva el layout de las tablas)
    a Gemini y devuelve la factura en el esquema canónico, SIN validar
    aritméticamente (eso es `core/extraccion/validacion.py`, a propósito
    separado).

    `texto_extraido`: el texto plano que `core/ingesta/pdf_texto.py::extraer_texto`
    ya sacó del mismo PDF con `pdfplumber`, si se tiene a mano -- se lo pasa
    al modelo como contenido ADICIONAL, no en reemplazo del PDF nativo
    (docs/auditoria-2026-09-facturas-reales.md, hallazgo B-3): en una factura de
    diseño a dos columnas, `pdfplumber` puede entregar el texto entrelazado
    de un modo distinto a como el modelo lee el layout visual del PDF, así
    que darle las dos vistas le da más para contrastar. `core/pipeline.py`
    ya extrajo ese texto antes de llegar acá (para la doble lectura del
    total), así que pasarlo no cuesta una llamada extra. Opcional a
    propósito: sigue funcionando sin él (ej. `scripts/probar_extraccion.py`
    antes de tener el texto, o un test que no lo necesita).

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

    contenidos: list = [PROMPT_EXTRACCION]
    if texto_extraido:
        contenidos.append(
            "Texto plano extraído del mismo PDF con una herramienta de lectura de "
            "texto (puede tener el orden de columnas mezclado -- usalo como apoyo, "
            "pero para la posición de cada dato guiate por el layout visual del PDF "
            "adjunto, no por el orden de este texto):\n\n" + texto_extraido
        )
    contenidos.append({"inline_data": {"data": pdf_bytes, "mime_type": "application/pdf"}})

    cliente = genai.Client(api_key=api_key)
    try:
        respuesta = cliente.models.generate_content(
            model=MODELO,
            contents=contenidos,
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

    try:
        factura = factura_desde_json(datos)
    except (KeyError, TypeError, ValueError) as exc:
        # docs/auditoria-2026-09-facturas-reales.md, hallazgo C-2: un JSON
        # SINTÁCTICAMENTE válido puede tener un concepto/impuesto/recargo/
        # crédito al que le falta una clave requerida (factura_desde_json
        # indexa "descripcion", "precio_unitario", etc. directo). Antes esto
        # escapaba como KeyError crudo -- sin envolver en ExtraccionError, el
        # llamador (core/pipeline.py) no lo atrapaba, así que la llamada NO
        # se contaba para el tope (anulaba B-4) y no quedaba ningún rastro en
        # intentos_gemini (anulaba B-5), y el usuario veía un mensaje inútil
        # como "'descripcion'". Envolver acá, con el texto crudo adjunto,
        # devuelve las dos cosas.
        raise ExtraccionError(
            f"El JSON de Gemini no tiene la forma esperada (falta o está mal tipado: {exc})",
            respuesta_cruda=texto,
        ) from exc
    # Estos metadatos permiten reconstruir cómo se produjo cada extracción,
    # aun cuando se actualice el prompt o se rote el modelo en el futuro.
    factura.modelo_extraccion = MODELO
    factura.version_prompt = VERSION_PROMPT
    factura.version_esquema = VERSION_ESQUEMA
    factura.respuesta_extraida = texto
    return factura
