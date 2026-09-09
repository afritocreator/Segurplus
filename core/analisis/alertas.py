"""Reglas de alerta sobre facturas y sus descomposiciones de variación.
Umbrales SIEMPRE en `data/alertas.yaml` (CLAUDE.md: nunca hardcodear
parámetros de este tipo en el código).

Cada función devuelve una lista de `Alerta` (posiblemente vacía) -- se
pensaron para poder correr independientemente y componerse en
`generar_alertas()`, que es la que usa el tablero y el Excel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from core.analisis.variacion import DescomposicionVariacion
from core.extraccion.esquema import FacturaExtraida

RUTA_ALERTAS = Path(__file__).resolve().parents[2] / "data" / "alertas.yaml"


@dataclass
class Alerta:
    tipo: str
    severidad: str  # "alta", "media", "baja"
    mensaje: str
    concepto: str | None = None


def _leer_umbrales() -> dict:
    """Sin cache y sin lectura a nivel de módulo, a propósito -- un YAML
    corrupto no debe tumbar el import ni la app Streamlit (mismo patrón que
    `core/beneficios/economia_conocimiento.py` de Consultora)."""
    datos = yaml.safe_load(RUTA_ALERTAS.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        raise ValueError(f"{RUTA_ALERTAS} no tiene la forma esperada (debe ser un mapeo)")
    return datos


def alertas_por_recargos(factura: FacturaExtraida) -> list[Alerta]:
    """Cualquier recargo (mora, interés, refacturación) es una alerta en sí
    misma -- no debería existir uno en una factura sana."""
    umbrales = _leer_umbrales()
    if not umbrales.get("alertar_cualquier_recargo", True):
        return []
    return [
        Alerta(
            tipo="recargo",
            severidad="alta",
            mensaje=f'Recargo "{r.nombre}" por ${r.importe:,.2f}',
        )
        for r in factura.recargos
    ]


def alertas_por_concepto_nuevo_o_desaparecido(
    descomposiciones: list[DescomposicionVariacion],
) -> list[Alerta]:
    """Un concepto con total_0 == 0 apareció; con total_1 == 0 desapareció."""
    alertas = []
    for d in descomposiciones:
        if d.total_0 == 0.0 and d.total_1 != 0.0:
            alertas.append(
                Alerta(
                    tipo="concepto_nuevo",
                    severidad="media",
                    mensaje=f'Concepto nuevo: "{d.concepto}" (${d.total_1:,.2f})',
                    concepto=d.concepto,
                )
            )
        elif d.total_1 == 0.0 and d.total_0 != 0.0:
            alertas.append(
                Alerta(
                    tipo="concepto_desaparecido",
                    severidad="baja",
                    mensaje=f'Concepto que dejó de facturarse: "{d.concepto}"',
                    concepto=d.concepto,
                )
            )
    return alertas


def alertas_por_salto_de_cantidad(
    descomposiciones: list[DescomposicionVariacion],
    *,
    conceptos_con_cantidad_sintetica: frozenset[str] = frozenset(),
) -> list[Alerta]:
    """Salto de cantidad (líneas, chips, medidores) respecto del período
    anterior, por encima del umbral `salto_de_cantidad_ratio`.

    `conceptos_con_cantidad_sintetica`: conceptos donde `cantidad_0` o
    `cantidad_1` NO es una cantidad real, sino el `1.0` sintético que
    `core.analisis.agregacion.agregar_conceptos` genera cuando la cantidad
    neta de un período dio cero con importe distinto de cero (una nota de
    crédito, ver hallazgo A-20). Comparar esa cantidad inventada contra una
    cantidad real dispara un "salto de cantidad" que no es tal -- son
    conceptos_con_cantidad_neta_cero() del período correspondiente, y se
    excluyen acá en vez de generar una alerta que después hay que descartar
    a mano."""
    umbral = _leer_umbrales()["salto_de_cantidad_ratio"]
    alertas = []
    for d in descomposiciones:
        if d.concepto in conceptos_con_cantidad_sintetica:
            continue
        if d.cantidad_0 == 0:
            continue
        variacion_cantidad = (d.cantidad_1 - d.cantidad_0) / d.cantidad_0
        if abs(variacion_cantidad) >= umbral:
            alertas.append(
                Alerta(
                    tipo="salto_de_cantidad",
                    severidad="media",
                    mensaje=(
                        f'"{d.concepto}": cantidad pasó de {d.cantidad_0:g} a {d.cantidad_1:g} '
                        f"({variacion_cantidad:+.0%})"
                    ),
                    concepto=d.concepto,
                )
            )
    return alertas


def alertas_por_precio_sobre_ipc(
    descomposiciones: list[DescomposicionVariacion], *, ipc_periodo_pct: float
) -> list[Alerta]:
    """Precio unitario que sube más de `precio_por_encima_del_ipc_pp` puntos
    porcentuales POR ENCIMA, EN TÉRMINOS REALES, de la inflación del período
    -- es decir, `exceso_pp` es la variación de precio ya deflactada por el
    IPC, no una resta lineal de dos porcentajes.

    exceso = (1 + variacion_precio_pct) / (1 + ipc_periodo_pct) - 1

    Por qué no `variacion_precio_pct - ipc_periodo_pct` (la aproximación
    lineal que tenía este módulo antes -- ver docs/auditoria-2026-09.md,
    hallazgo A-22): con inflación mensual real (no el 1-2% de una economía
    estable), la diferencia entre ambas fórmulas deja de ser despreciable.
    Ejemplo con el umbral real de data/alertas.yaml (5,0 pp): precio +55%,
    inflación +50% -> la resta lineal da exactamente 5,0 pp y dispara la
    alerta; la fórmula correcta da (1.55/1.50 - 1) = +3,33%, por debajo del
    umbral -- el proveedor subió en línea con la inflación, no por encima."""
    umbral_pp = _leer_umbrales()["precio_por_encima_del_ipc_pp"]
    alertas = []
    for d in descomposiciones:
        if d.precio_0 == 0:
            continue
        variacion_precio_pct = (d.precio_1 - d.precio_0) / d.precio_0
        exceso_pp = ((1 + variacion_precio_pct) / (1 + ipc_periodo_pct) - 1) * 100
        if exceso_pp >= umbral_pp:
            alertas.append(
                Alerta(
                    tipo="precio_sobre_ipc",
                    severidad="alta",
                    mensaje=(
                        f'"{d.concepto}": precio unitario subió {variacion_precio_pct:.1%}, '
                        f"{exceso_pp:.1f} puntos reales por encima de la inflación del período"
                    ),
                    concepto=d.concepto,
                )
            )
    return alertas


def alertas_por_item_duplicado(factura: FacturaExtraida) -> list[Alerta]:
    """Dos conceptos con la misma descripción exacta dentro de la misma
    factura -- típico indicio de refacturación duplicada."""
    vistos: dict[str, int] = {}
    for c in factura.conceptos:
        vistos[c.descripcion] = vistos.get(c.descripcion, 0) + 1
    return [
        Alerta(
            tipo="item_duplicado",
            severidad="media",
            mensaje=f'"{descripcion}" aparece {cantidad} veces en la misma factura',
            concepto=descripcion,
        )
        for descripcion, cantidad in vistos.items()
        if cantidad > 1
    ]


def generar_alertas(
    factura: FacturaExtraida,
    descomposiciones: list[DescomposicionVariacion],
    *,
    ipc_periodo_pct: float = 0.0,
    conceptos_con_cantidad_sintetica: frozenset[str] = frozenset(),
) -> list[Alerta]:
    """Corre todas las reglas de alerta y devuelve la lista combinada.

    `conceptos_con_cantidad_sintetica`: ver docstring de
    `alertas_por_salto_de_cantidad` -- pasar acá el resultado de
    `core.analisis.agregacion.conceptos_con_cantidad_neta_cero()` de AMBOS
    períodos comparados, para no generar un "salto de cantidad" espurio
    sobre el 1.0 sintético del hallazgo A-20."""
    return [
        *alertas_por_recargos(factura),
        *alertas_por_item_duplicado(factura),
        *alertas_por_concepto_nuevo_o_desaparecido(descomposiciones),
        *alertas_por_salto_de_cantidad(
            descomposiciones, conceptos_con_cantidad_sintetica=conceptos_con_cantidad_sintetica
        ),
        *alertas_por_precio_sobre_ipc(descomposiciones, ipc_periodo_pct=ipc_periodo_pct),
    ]
