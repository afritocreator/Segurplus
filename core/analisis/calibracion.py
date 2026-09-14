"""Cálculos comparables para la calibración de conceptos no homologados."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from core.deflactor import a_pesos_constantes


@dataclass(frozen=True)
class ConceptoSinClasificarResumen:
    servicio: str | None
    descripcion: str
    score: float | None
    importe_real: float
    veces: int
    ultimo_periodo: date


def resumir_sin_clasificar(
    filas: list[tuple[str | None, str, float | None, float, date]],
    *,
    fecha_base: date,
    df_ipc: pl.DataFrame,
) -> tuple[list[ConceptoSinClasificarResumen], float]:
    """Agrupa importes en pesos constantes y devuelve filas y total sin clasificar.

    Cada fila contiene ``(servicio, descripción, score, importe, período)``.
    """
    acumulado: dict[tuple[str | None, str], list[object]] = {}
    for servicio, descripcion, score, importe, periodo in filas:
        clave = (servicio, descripcion)
        real = a_pesos_constantes(importe, periodo, fecha_base, df_ipc=df_ipc)
        if clave not in acumulado:
            acumulado[clave] = [score, 0.0, 0, periodo]
        actual = acumulado[clave]
        # max() con key=lambda x: x is not None NO calcula el máximo real: entre
        # dos valores no nulos, ambas keys valen True y max() devuelve el PRIMERO
        # (docs/auditoria-2026-09-rediseno.md, hallazgo de la revisión del Bloque
        # de Codex -- verificado con (0.30, 0.90): guardaba 0.30). El máximo
        # ignorando None hay que calcularlo filtrando los None antes.
        candidatos = [v for v in (actual[0], score) if v is not None]
        actual[0] = max(candidatos) if candidatos else None
        actual[1] = float(actual[1]) + real
        actual[2] = int(actual[2]) + 1
        actual[3] = max(actual[3], periodo)
    resumen = [
        ConceptoSinClasificarResumen(s, d, v[0], float(v[1]), int(v[2]), v[3])
        for (s, d), v in acumulado.items()
    ]
    resumen.sort(key=lambda r: r.importe_real, reverse=True)
    return resumen, sum(r.importe_real for r in resumen)


def total_en_pesos_constantes(
    filas: list[tuple[float, date]], *, fecha_base: date, df_ipc: pl.DataFrame
) -> float:
    """Suma importes de períodos distintos en la misma moneda de referencia."""
    return sum(
        a_pesos_constantes(importe, periodo, fecha_base, df_ipc=df_ipc)
        for importe, periodo in filas
    )


@dataclass(frozen=True)
class MetricasProveedor:
    """Cifras de calidad de lectura de UN proveedor -- responde "¿la
    herramienta está leyendo bien a este emisor?" en vez de mirar el
    agregado de todos mezclados. Las cifras crudas vienen de
    `core.almacenamiento.metricas_por_proveedor`; las proporciones (cálculo,
    no consulta) se calculan acá, con test de valor a mano."""

    emisor: str
    facturas_cargadas: int
    facturas_en_cuarentena: int
    facturas_rechazadas: int
    conceptos_totales: int
    conceptos_sin_homologar: int
    importe_sin_homologar: float

    @property
    def total_facturas_vistas(self) -> int:
        """Cargadas + cuarentena -- el universo real de PDFs de este
        proveedor que pasaron por el pipeline, para poder calcular una tasa.
        No suma `facturas_rechazadas` -- una rechazada ya está contada
        dentro de `facturas_cargadas` (llegó a validar aritméticamente y
        pasar a `facturas`, después alguien la rechazó; ver
        `core.almacenamiento.decision_factura`), sumarla de nuevo la
        contaría dos veces."""
        return self.facturas_cargadas + self.facturas_en_cuarentena

    @property
    def tasa_cuarentena(self) -> float | None:
        """Proporción de PDFs de este proveedor que NO cerraron
        aritméticamente. `None` si nunca se vio ningún PDF de este emisor
        (evita una división por cero sin sentido)."""
        if self.total_facturas_vistas == 0:
            return None
        return self.facturas_en_cuarentena / self.total_facturas_vistas

    @property
    def tasa_sin_homologar(self) -> float | None:
        """Proporción de conceptos de las facturas ya cargadas y APROBADAS
        de este proveedor que no homologaron a ningún concepto normalizado
        (`conceptos_totales`/`conceptos_sin_homologar` ya vienen filtrados
        por `estado = 'aprobada'` desde `core.almacenamiento.
        metricas_por_proveedor`, docs/auditoria-2026-09-piloto.md, A-55).
        `None` si el proveedor no tiene ningún concepto aprobado todavía
        (solo cuarentena, solo rechazadas, o nada)."""
        if self.conceptos_totales == 0:
            return None
        return self.conceptos_sin_homologar / self.conceptos_totales


def metricas_por_proveedor(
    filas: list[tuple[str, int, int, int, int, int, float]],
) -> list[MetricasProveedor]:
    """Envuelve las filas crudas de `core.almacenamiento.metricas_por_proveedor`
    en `MetricasProveedor`, ordenadas por tasa de cuarentena descendente (el
    proveedor que más falla primero -- la prioridad de dónde mirar)."""
    metricas = [MetricasProveedor(*fila) for fila in filas]
    return sorted(metricas, key=lambda m: m.tasa_cuarentena or 0.0, reverse=True)
