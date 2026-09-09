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


def alertas_por_salto_de_cantidad(descomposiciones: list[DescomposicionVariacion]) -> list[Alerta]:
    """Salto de cantidad (líneas, chips, medidores) respecto del período
    anterior, por encima del umbral `salto_de_cantidad_ratio`."""
    umbral = _leer_umbrales()["salto_de_cantidad_ratio"]
    alertas = []
    for d in descomposiciones:
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
    porcentuales por encima de la inflación del período."""
    umbral_pp = _leer_umbrales()["precio_por_encima_del_ipc_pp"]
    alertas = []
    for d in descomposiciones:
        if d.precio_0 == 0:
            continue
        variacion_precio_pct = (d.precio_1 - d.precio_0) / d.precio_0
        exceso_pp = (variacion_precio_pct - ipc_periodo_pct) * 100
        if exceso_pp >= umbral_pp:
            alertas.append(
                Alerta(
                    tipo="precio_sobre_ipc",
                    severidad="alta",
                    mensaje=(
                        f'"{d.concepto}": precio unitario subió {variacion_precio_pct:.1%}, '
                        f"{exceso_pp:.1f} puntos por encima del IPC del período"
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
) -> list[Alerta]:
    """Corre todas las reglas de alerta y devuelve la lista combinada."""
    return [
        *alertas_por_recargos(factura),
        *alertas_por_item_duplicado(factura),
        *alertas_por_concepto_nuevo_o_desaparecido(descomposiciones),
        *alertas_por_salto_de_cantidad(descomposiciones),
        *alertas_por_precio_sobre_ipc(descomposiciones, ipc_periodo_pct=ipc_periodo_pct),
    ]
