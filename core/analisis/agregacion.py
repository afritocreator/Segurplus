"""Agrega los conceptos homologados de todas las facturas de un servicio
dentro de un período en un único {concepto: (cantidad, precio_unitario)} --
lo que pide `core.analisis.variacion.descomponer_conceptos`.

Función pura sobre filas ya leídas de DuckDB (no abre conexión acá), así se
puede testear sin base de datos real.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FilaConcepto:
    concepto_normalizado: str | None
    descripcion: str
    cantidad: float
    importe: float
    unidad: str | None = None


def _clave(
    concepto_normalizado: str | None, descripcion: str, unidad: str | None
) -> tuple[str, str | None]:
    """(concepto, unidad) -- NUNCA se agrupa solo por concepto: dos facturas
    del mismo servicio pueden homologar al mismo `concepto_normalizado` con
    unidades distintas (kWh vs. GB, por ejemplo, si el umbral de similitud
    de `core.analisis.homologacion` las confunde -- ver
    docs/auditoria-2026-09.md, hallazgo A-16). Sumarlas sin distinguir la
    unidad produce una "cantidad total" y un "precio unitario promedio" que
    no significan nada -- el equivalente de sumar litros con kilos."""
    concepto = concepto_normalizado or f"(sin_homologar) {descripcion}"
    return concepto, unidad


def _etiqueta(concepto: str, unidad: str | None) -> str:
    return concepto if unidad is None else f"{concepto} [{unidad}]"


def _acumular(filas: list[FilaConcepto]) -> dict[tuple[str, str | None], list[float]]:
    acumulado: dict[tuple[str, str | None], list[float]] = {}
    for fila in filas:
        clave = _clave(fila.concepto_normalizado, fila.descripcion, fila.unidad)
        if clave not in acumulado:
            acumulado[clave] = [0.0, 0.0]
        acumulado[clave][0] += fila.cantidad
        acumulado[clave][1] += fila.importe
    return acumulado


def agregar_conceptos(filas: list[FilaConcepto]) -> dict[str, tuple[float, float]]:
    """Suma cantidad e importe de todas las filas con el mismo concepto
    normalizado Y LA MISMA UNIDAD (puede haber más de una factura del mismo
    servicio en un período, ej. dos líneas separadas), y calcula el precio
    unitario PROMEDIO PONDERADO (importe total / cantidad total) -- no el
    promedio simple de precios, que distorsionaría si las cantidades
    difieren mucho entre facturas. Ese promedio ponderado solo tiene sentido
    si todas las filas agregadas comparten unidad -- por eso se agrupa por
    (concepto, unidad), no solo por concepto (ver `_clave`).

    Las filas sin homologar (`concepto_normalizado is None`) se agrupan bajo
    su propia descripción tal cual, para no perderlas del análisis -- van a
    aparecer como "concepto nuevo" en las alertas, que es la señal correcta.

    Caso borde -- cantidad neta cero con importe distinto de cero (una nota
    de crédito o un ajuste que cancela la cantidad de un concepto dentro del
    mismo período, ej. +4 líneas en una factura y -4 en una nota de
    crédito): ANTES este caso hacía que el precio se forzara a 0.0 y el
    importe correspondiente se perdiera del total agregado, sin aviso (ver
    docs/auditoria-2026-09.md, hallazgo A-20). Ahora se trata como un
    concepto "sin cantidad" (cantidad=1, precio=importe_total) -- igual que
    un cargo fijo -- para que la identidad `cantidad × precio == importe`
    se mantenga y el importe no se evapore del total. Ese caso además se
    puede detectar con `conceptos_con_cantidad_neta_cero` para alertarlo
    explícitamente, porque sigue siendo una anomalía que vale la pena mirar.
    El caso genuino de cantidad Y importe cero (`0, 0.0`) sigue devolviendo
    `(0.0, 0.0)`.
    """
    resultado: dict[str, tuple[float, float]] = {}
    for (concepto, unidad), (cantidad_total, importe_total) in _acumular(filas).items():
        if cantidad_total == 0 and importe_total != 0:
            cantidad_total, precio_unitario = 1.0, importe_total
        elif cantidad_total == 0:
            precio_unitario = 0.0
        else:
            precio_unitario = importe_total / cantidad_total
        resultado[_etiqueta(concepto, unidad)] = (cantidad_total, precio_unitario)
    return resultado


def conceptos_con_cantidad_neta_cero(filas: list[FilaConcepto]) -> list[str]:
    """Etiquetas de los conceptos (ver `_etiqueta`) cuya cantidad total dio
    cero pero cuyo importe total NO es cero -- típico de una nota de
    crédito o un ajuste dentro del mismo período. Es una anomalía real que
    vale la pena que alguien mire, no algo para tapar en silencio (ver
    docstring de `agregar_conceptos`, hallazgo A-20)."""
    return [
        _etiqueta(concepto, unidad)
        for (concepto, unidad), (cantidad_total, importe_total) in _acumular(filas).items()
        if cantidad_total == 0 and importe_total != 0
    ]
