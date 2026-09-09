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


def agregar_conceptos(filas: list[FilaConcepto]) -> dict[str, tuple[float, float]]:
    """Suma cantidad e importe de todas las filas con el mismo concepto
    normalizado (puede haber más de una factura del mismo servicio en un
    período, ej. dos líneas separadas), y calcula el precio unitario
    PROMEDIO PONDERADO (importe total / cantidad total) -- no el promedio
    simple de precios, que distorsionaría si las cantidades difieren mucho
    entre facturas.

    Las filas sin homologar (`concepto_normalizado is None`) se agrupan bajo
    su propia descripción tal cual, para no perderlas del análisis -- van a
    aparecer como "concepto nuevo" en las alertas, que es la señal correcta.
    """
    acumulado: dict[str, list[float]] = {}  # concepto -> [cantidad_total, importe_total]
    for fila in filas:
        clave = fila.concepto_normalizado or f"(sin_homologar) {fila.descripcion}"
        if clave not in acumulado:
            acumulado[clave] = [0.0, 0.0]
        acumulado[clave][0] += fila.cantidad
        acumulado[clave][1] += fila.importe

    resultado = {}
    for concepto, (cantidad_total, importe_total) in acumulado.items():
        precio_unitario = importe_total / cantidad_total if cantidad_total != 0 else 0.0
        resultado[concepto] = (cantidad_total, precio_unitario)
    return resultado
