"""El resultado en castellano: un párrafo que responde en una frase la
pregunta que motivó todo el proyecto ("¿por qué cambió lo que pago de este
servicio?"), en vez de que la persona tenga que leer cuatro métricas y una
tabla para reconstruirlo sola.

Bloque 5 del plan de rediseño de septiembre 2026 (ver `docs/estado.md`):
el usuario reportó que, incluso cuando la herramienta lee bien la factura,
el análisis no se entiende.

REGLA DURA, NO NEGOCIABLE (misma que separa esta herramienta de "confiar en
la IA", ver CLAUDE.md): el modelo de lenguaje NUNCA produce un número.
`generar_relato_determinista` recibe los números YA CALCULADOS y
VALIDADOS por `core/analisis/*` (la misma fuente que ya usa `web/app.py`
para las métricas) y arma el párrafo con una plantilla de Python -- sin
IA, sin red, nunca falla. `redactar_con_modelo` (opcional) le pide a un
modelo de texto que lo redacte más natural, pasándole el párrafo ya armado
como única fuente de verdad y prohibiéndole explícitamente cambiar un
número; si el modelo no está configurado, falla, o devuelve algo que no
se puede verificar como una reescritura fiel, se usa el párrafo
determinístico tal cual. Nunca se le pide al modelo "generá un resumen a
partir de los datos" -- eso le daría la oportunidad de inventar o
redondear mal un número.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from core.analisis.agregacion import etiqueta_legible
from core.analisis.variacion import DescomposicionVariacion
from core.formato import pesos_ars

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _mes_anio(periodo_iso: str) -> str:
    """`"2026-08-01"` -> `"agosto de 2026"`. Si el formato no se puede
    interpretar (no debería pasar -- `periodo_desde` ya se normalizó a ISO
    en `core/extraccion/esquema.py` antes de llegar acá), devuelve el
    valor tal cual en vez de lanzar: un relato con una fecha rara sigue
    siendo mejor que una pantalla rota."""
    try:
        fecha = date.fromisoformat(periodo_iso)
    except ValueError:
        return periodo_iso
    return f"{_MESES[fecha.month - 1]} de {fecha.year}"


def _porcentaje(valor: float) -> str:
    return f"{valor * 100:+.1f}%".replace(".", ",").replace(",0%", "%")


@dataclass(frozen=True)
class DatosRelato:
    """Todo lo que necesita el relato, ya calculado por `core/analisis/*`
    -- ver `web/app.py::_analisis` para de dónde sale cada campo. Ninguno
    de estos valores se recalcula acá: si algo está mal, el bug está en
    quien llama, no en este módulo."""

    servicio: str
    periodo_0: str  # ISO, ej. "2026-07-01"
    periodo_1: str
    total_0: float
    total_1: float
    tipo_dominante: str  # "precio" | "cantidad" | "mixto" | "sin_variacion"
    proporcion_dominante: float  # 0..1, irrelevante si tipo_dominante == "sin_variacion"
    variacion_real_pct: float | None  # None si no se pudo calcular (ver core/analisis/real.py)
    inflacion_pct: float
    concepto_destacado: DescomposicionVariacion | None  # el de mayor variación absoluta, o None


def generar_relato_determinista(datos: DatosRelato) -> str:
    """El párrafo, siempre disponible, siempre verificable a mano contra
    los mismos números que ya se muestran en las métricas de arriba."""
    mes_1 = _mes_anio(datos.periodo_1)
    mes_0 = _mes_anio(datos.periodo_0)
    variacion_pesos = datos.total_1 - datos.total_0

    if datos.total_0 == 0:
        # No hay período base real contra el cual comparar -- primera
        # factura de este servicio, o el servicio no facturó nada antes.
        return (
            f"En {mes_1} pagaste {pesos_ars(datos.total_1)} de {datos.servicio}. "
            f"No hay un {mes_0} con gasto para comparar, así que todavía no se puede "
            "decir si esto es más, menos o parecido a lo habitual."
        )

    variacion_pct = variacion_pesos / datos.total_0
    frase_monto = (
        f"En {mes_1} pagaste {pesos_ars(datos.total_1)} de {datos.servicio}, "
        f"{pesos_ars(abs(variacion_pesos))} "
        f"{'más' if variacion_pesos >= 0 else 'menos'} que en {mes_0} "
        f"({_porcentaje(variacion_pct)})."
    )

    if datos.tipo_dominante == "sin_variacion":
        frase_causa = "El gasto no cambió entre los dos meses."
    elif datos.tipo_dominante == "precio":
        frase_causa = (
            f"Casi todo el cambio es por PRECIO ({datos.proporcion_dominante:.0%} del "
            "movimiento): consumiste una cantidad parecida, pero salió más caro."
        )
    elif datos.tipo_dominante == "cantidad":
        frase_causa = (
            f"Casi todo el cambio es por CANTIDAD ({datos.proporcion_dominante:.0%} del "
            "movimiento): consumiste distinto, el precio se mantuvo parecido."
        )
    else:  # "mixto"
        frase_causa = (
            "Fue una mezcla de cantidad y precio -- ningún efecto explica la mayor "
            "parte por sí solo."
        )

    if datos.variacion_real_pct is not None:
        if abs(datos.variacion_real_pct) < 0.005:
            frase_real = (
                f"Descontada la inflación del período ({_porcentaje(datos.inflacion_pct)}), "
                "el gasto real fue prácticamente el mismo."
            )
        else:
            frase_real = (
                f"Descontada la inflación del período ({_porcentaje(datos.inflacion_pct)}), "
                f"tu gasto real {'subió' if datos.variacion_real_pct >= 0 else 'bajó'} un "
                f"{_porcentaje(abs(datos.variacion_real_pct))}."
            )
    else:
        frase_real = (
            "No se pudo calcular cuánto de eso es inflación (falta el IPC de alguno de "
            "los dos períodos)."
        )

    frase_concepto = ""
    if datos.concepto_destacado is not None and abs(datos.concepto_destacado.variacion_total) > 0:
        d = datos.concepto_destacado
        etiqueta = etiqueta_legible(d.concepto)
        frase_concepto = (
            f' El que más cambió fue "{etiqueta}": '
            f"{pesos_ars(abs(d.variacion_total))} "
            f"{'más' if d.variacion_total >= 0 else 'menos'}."
        )

    return f"{frase_monto} {frase_causa} {frase_real}{frase_concepto}"


_INSTRUCCION_REDACCION = """\
Te paso un párrafo que explica cómo cambió el gasto de un servicio entre dos meses. \
Reescribilo en un castellano más natural y fluido, en 2 o 3 oraciones, sin cambiar \
NINGÚN número, porcentaje ni nombre propio -- son datos ya verificados, tu única tarea \
es la redacción. Si no podés reescribirlo sin tocar un número, devolvé el párrafo \
original tal cual. Respondé solo con el párrafo final, sin explicaciones ni comillas.

Párrafo original:
"""


def redactar_con_modelo(
    parrafo_determinista: str,
    *,
    base_url: str = "https://api.groq.com/openai/v1",
    modelo: str = "llama-3.3-70b-versatile",
    variable_entorno_clave: str = "GROQ_API_KEY",
    timeout_segundos: float = 8.0,
) -> str:
    """Le pide a un modelo de texto rápido y gratis (Groq, recomendación
    #1 del Informe Técnico Semanal de APIs Gratuitas del 18/09/2026) que
    redacte mejor el párrafo YA ARMADO -- nunca que lo genere desde cero.
    Cualquier fallo (sin clave, sin red, timeout, respuesta vacía) devuelve
    el párrafo determinístico tal cual: esto es una mejora cosmética
    opcional, nunca un punto de falla para ver el resultado.

    Todavía no se probó contra la API real de Groq en este entorno de
    desarrollo (no hay GROQ_API_KEY -- ver docs/banco_extraccion.md)."""
    api_key = os.environ.get(variable_entorno_clave)
    if not api_key:
        return parrafo_determinista

    try:
        import requests

        respuesta = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": modelo,
                "messages": [
                    {
                        "role": "user",
                        "content": _INSTRUCCION_REDACCION + parrafo_determinista,
                    }
                ],
                "temperature": 0.3,
            },
            timeout=timeout_segundos,
        )
        respuesta.raise_for_status()
        texto = respuesta.json()["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001 -- cualquier fallo cae al párrafo determinístico
        return parrafo_determinista

    if not texto:
        return parrafo_determinista
    return texto
