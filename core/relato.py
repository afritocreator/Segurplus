"""El resultado en castellano: un párrafo que responde en una frase la
pregunta que motivó todo el proyecto ("¿por qué cambió lo que pago de este
servicio?"), en vez de que la persona tenga que leer cuatro métricas y una
tabla para reconstruirlo sola.

Bloque 5 del plan de rediseño de septiembre 2026 (ver `docs/estado.md`):
el usuario reportó que, incluso cuando la herramienta lee bien la factura,
el análisis no se entiende.

REGLA DURA, NO NEGOCIABLE (misma que separa esta herramienta de "confiar en
la IA", ver CLAUDE.md): el modelo de lenguaje NUNCA produce un número. Por
eso este módulo arma el párrafo entero con una plantilla de Python, sin IA
y sin red -- nunca falla, y nunca puede decir un número que no venga de
`core/analisis/*` (la misma fuente que ya usa `web/app.py` para las
métricas). Antes existía `redactar_con_modelo`, que le pedía a un modelo de
texto (Groq) que reescribiera el párrafo; se sacó en el arreglo de
`docs/auditoria-2026-09-web.md` (E-1): la "verificación" que el docstring
prometía nunca se implementó, así que en producción el modelo podía decir
cualquier cosa sin que nada lo controlara. Si en el futuro se quiere volver
a probar, tiene que venir con una verificación real (comparar los números
del párrafo original contra los de la respuesta) antes de mostrarse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from core.analisis.agregacion import etiqueta_legible
from core.analisis.variacion import DescomposicionVariacion
from core.formato import pesos_ars

RUTA_ALERTAS = Path(__file__).resolve().parents[1] / "data" / "alertas.yaml"

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


def _umbral_composicion_relato() -> float:
    """Sin cache y sin lectura a nivel de módulo, a propósito -- mismo
    criterio que `core/analisis/alertas.py::_leer_umbrales`: un YAML
    corrupto no debe tumbar el import. Nunca hardcodeado en el código
    (CLAUDE.md), ver `data/alertas.yaml::umbral_composicion_relato`."""
    datos = yaml.safe_load(RUTA_ALERTAS.read_text(encoding="utf-8"))
    return float(datos["umbral_composicion_relato"])


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


def _porcentaje(valor: float, *, con_signo: bool = True) -> str:
    """`con_signo=True` (default): `+15,0%` / `-15,0%`, para cuando el
    signo es la única forma de saber la dirección (ej. la variación
    nominal). `con_signo=False`: `15,0%` sin signo, para cuando la
    dirección YA la dice una palabra ("subió", "bajó") -- antes esta
    función siempre forzaba el signo, así que "bajó un ..." terminaba en
    "bajó un +23%" (docs/auditoria-2026-09-web.md, E-2): un signo de más
    delante de una baja."""
    if con_signo:
        texto = f"{valor * 100:+.1f}%"
    else:
        texto = f"{abs(valor) * 100:.1f}%"
    texto = texto.replace(".", ",").replace(",0%", "%")
    # Un valor que redondea a cero no tiene dirección -- "+0%" sugeriría
    # una suba mínima en vez de "sin cambio".
    return "0%" if texto == "+0%" else texto


@dataclass(frozen=True)
class DatosRelato:
    """Todo lo que necesita el relato, ya calculado por `core/analisis/*`
    -- ver `web/app.py::_analisis` para de dónde sale cada campo. Ninguno
    de estos valores se recalcula acá: si algo está mal, el bug está en
    quien llama, no en este módulo.

    `total_0`/`total_1` son el TOTAL PAGABLE (consumos + impuestos +
    recargos - créditos, ver Bloque 6 del plan de rediseño), no solo los
    consumos -- son los mismos números que se muestran como cifra
    principal. `consumos_0/1`, `impuestos_0/1`, `recargos_0/1` y
    `creditos_0/1` son la composición de ese total (de
    `core.almacenamiento.componentes_financieros_periodo`), para poder
    explicar cuánto del cambio es de cada parte
    (docs/auditoria-2026-09-web.md, E-3).

    `tipo_dominante`/`proporcion_dominante` y
    `efecto_precio_total`/`efecto_cantidad_total` describen el cambio
    DENTRO de los consumos únicamente (de
    `core.analisis.variacion.efecto_dominante` y la suma de
    `DescomposicionVariacion.efecto_precio`/`efecto_cantidad` de todos los
    conceptos) -- nunca del total pagable, que puede moverse por motivos
    ajenos al consumo (impuestos, recargos)."""

    servicio: str
    periodo_0: str  # ISO, ej. "2026-07-01"
    periodo_1: str
    total_0: float  # total pagable
    total_1: float
    consumos_0: float
    consumos_1: float
    impuestos_0: float
    impuestos_1: float
    recargos_0: float
    recargos_1: float
    creditos_0: float
    creditos_1: float
    tipo_dominante: str  # "precio" | "cantidad" | "mixto" | "sin_variacion", de los CONSUMOS
    proporcion_dominante: float  # 0..1, irrelevante si tipo_dominante == "sin_variacion"
    efecto_precio_total: float  # signo real: positivo = el precio subió
    efecto_cantidad_total: float  # signo real: positivo = se consumió más
    variacion_real_pct: float | None  # None si no se pudo calcular (ver core/analisis/real.py)
    inflacion_pct: float | None  # None si no se pudo descargar/calcular el IPC del período
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

    # Cuánto del cambio es consumo y cuánto es impuestos/recargos/créditos
    # (docs/auditoria-2026-09-web.md, E-3) -- solo se menciona si la parte
    # que no es consumo pesa lo suficiente como para importar.
    delta_consumo = datos.consumos_1 - datos.consumos_0
    no_consumo_0 = datos.impuestos_0 + datos.recargos_0 - datos.creditos_0
    no_consumo_1 = datos.impuestos_1 + datos.recargos_1 - datos.creditos_1
    delta_no_consumo = no_consumo_1 - no_consumo_0

    frase_composicion = ""
    if (
        variacion_pesos != 0
        and abs(delta_no_consumo) / abs(variacion_pesos) >= _umbral_composicion_relato()
    ):
        frase_composicion = (
            f" De esa diferencia, {pesos_ars(abs(delta_no_consumo))} son impuestos, "
            f"recargos y créditos, y {pesos_ars(abs(delta_consumo))} son consumos."
        )

    if datos.tipo_dominante == "sin_variacion":
        if variacion_pesos == 0:
            frase_causa = "El gasto no cambió entre los dos meses."
        else:
            frase_causa = (
                "Los consumos no cambiaron: toda la diferencia es de impuestos, "
                "recargos o créditos."
            )
    elif datos.tipo_dominante == "precio":
        direccion = "subió" if datos.efecto_precio_total >= 0 else "bajó"
        frase_causa = (
            f"Dentro de los consumos, el cambio fue mayormente por PRECIO "
            f"({datos.proporcion_dominante:.0%} del movimiento): el precio unitario "
            f"{direccion}."
        )
    elif datos.tipo_dominante == "cantidad":
        direccion = "aumentó" if datos.efecto_cantidad_total >= 0 else "disminuyó"
        frase_causa = (
            f"Dentro de los consumos, el cambio fue mayormente por CANTIDAD "
            f"({datos.proporcion_dominante:.0%} del movimiento): lo que consumiste "
            f"{direccion}."
        )
    else:  # "mixto"
        frase_causa = (
            "Dentro de los consumos, fue una mezcla de cantidad y precio -- ningún "
            "efecto explica la mayor parte por sí solo."
        )

    if datos.variacion_real_pct is not None and datos.inflacion_pct is not None:
        if abs(datos.variacion_real_pct) < 0.005:
            frase_real = (
                f"Descontada la inflación del período ({_porcentaje(datos.inflacion_pct)}), "
                "el gasto real fue prácticamente el mismo."
            )
        else:
            frase_real = (
                f"Descontada la inflación del período ({_porcentaje(datos.inflacion_pct)}), "
                f"tu gasto real {'subió' if datos.variacion_real_pct >= 0 else 'bajó'} un "
                f"{_porcentaje(abs(datos.variacion_real_pct), con_signo=False)}."
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

    return f"{frase_monto}{frase_composicion} {frase_causa} {frase_real}{frase_concepto}"
