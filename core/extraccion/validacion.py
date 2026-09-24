"""Validación aritmética determinística de una factura extraída.

Port de `lib/invoice/validate.ts` de Kleric-, con cierre contable exacto
en centavos. Un porcentaje de tolerancia ocultaría diferencias materiales;
los redondeos impresos deben capturarse como líneas identificables.
Agrega controles propios de facturas de servicios:

- impuestos + recargos también tienen que cerrar contra el total, no solo
  el subtotal (una factura de servicios discrimina IVA e Ingresos Brutos
  aparte, a diferencia de una factura de mercadería).
- doble lectura del total: se compara el total que dijo el modelo contra
  el total leído con una regex directamente del texto del PDF
  (`core/ingesta/pdf_texto.py::total_impreso`). Dos lecturas independientes
  del mismo dato — si no coinciden, algo está mal y no hay que confiar en
  ninguna de las dos.
- `subtotal`/`total` AUSENTES (el modelo devolvió `null`) son un motivo de
  falla en sí mismo, no algo que se tapa con un fallback. Antes, si
  faltaban, se usaba el propio cálculo (`suma_conceptos`,
  `subtotal + impuestos + recargos`) como referencia -- y esa referencia se
  comparaba contra sí misma, así que CUALQUIER número pasaba la validación
  con esos campos en `null` (ver docs/auditoria-2026-09.md, hallazgo A-4).

Si CUALQUIERA de estos controles falla, la factura NO entra al análisis --
CLAUDE.md: "nunca mostrarle a un usuario un número no verificado". Antes
esto se hacía cumplir mandando la factura a la tabla `cuarentena`; con el
plan de confirmación de carga (docs/estado.md),
`core.pipeline.confirmar_factura` es quien llama a `validar_factura` y
rechaza confirmar si `factura_valida` da `False` -- la factura sigue
siendo un borrador editable, no un callejón sin salida."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from core.extraccion.esquema import FacturaExtraida

TOLERANCIA_LINEA = 0.01  # Un centavo para el redondeo de cantidad × precio.
TOLERANCIA_TOTAL_RATIO = 0.0  # Compatibilidad de firma; no se admite tolerancia porcentual.


def _centavos(valor: float | Decimal) -> int:
    return int((Decimal(str(valor)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class ItemValidado:
    indice: int
    ok: bool
    importe_esperado: float
    diferencia: float = 0.0


@dataclass
class ResultadoValidacion:
    items: list[ItemValidado] = field(default_factory=list)
    suma_conceptos: float = 0.0
    suma_impuestos: float = 0.0
    suma_recargos: float = 0.0
    suma_creditos: float = 0.0
    subtotal_ok: bool = True
    total_ok: bool = True
    total_impreso_ok: bool = True
    todas_las_lineas_ok: bool = True
    subtotal_presente: bool = True
    total_presente: bool = True
    diferencia_subtotal: float = 0.0
    diferencia_total: float = 0.0
    diferencia_impreso: float = 0.0

    @property
    def factura_valida(self) -> bool:
        """True solo si TODOS los controles pasaron — es la condición que
        decide si la factura entra al análisis o va a cuarentena.

        `subtotal_presente`/`total_presente` son controles aparte de
        `subtotal_ok`/`total_ok` a propósito: si el modelo no informa
        `subtotal` o `total`, esos dos últimos usan como referencia el
        propio cálculo (`suma_conceptos`, `subtotal + impuestos + recargos`)
        y por eso SIEMPRE dan `True` en ese caso -- el control se compara
        contra sí mismo. Antes de este chequeo explícito, eso hacía que
        CUALQUIER número pasara la validación con `subtotal`/`total` en
        `null` (ver docs/auditoria-2026-09.md, hallazgo A-4)."""
        return (
            self.todas_las_lineas_ok
            and self.subtotal_ok
            and self.total_ok
            and self.total_impreso_ok
            and self.subtotal_presente
            and self.total_presente
        )

    def motivos_de_falla(self) -> list[str]:
        motivos = []
        if not self.todas_las_lineas_ok:
            indices = [str(i.indice) for i in self.items if not i.ok]
            motivos.append(
                f"cantidad×precio no coincide con el importe en la(s) línea(s) {', '.join(indices)}"
            )
        if not self.subtotal_presente:
            motivos.append("el modelo no pudo leer el subtotal de la factura")
        if not self.total_presente:
            motivos.append("el modelo no pudo leer el total de la factura")
        if self.subtotal_presente and not self.subtotal_ok:
            motivos.append(
                f"la suma de los conceptos no coincide con el subtotal "
                f"(diferencia: ${self.diferencia_subtotal:,.2f})"
            )
        if self.total_presente and not self.total_ok:
            motivos.append(
                "subtotal + impuestos + recargos - créditos no coincide con el total "
                f"(diferencia: ${self.diferencia_total:,.2f})"
            )
        if not self.total_impreso_ok:
            motivos.append(
                "el total extraído no coincide con el total impreso en el PDF "
                f"(diferencia: ${self.diferencia_impreso:,.2f})"
            )
        return motivos


def validar_factura(
    factura: FacturaExtraida,
    *,
    total_impreso: float | None = None,
    tolerancia_linea: float = TOLERANCIA_LINEA,
    tolerancia_total_ratio: float = TOLERANCIA_TOTAL_RATIO,
) -> ResultadoValidacion:
    """Corre los controles aritméticos sobre una factura extraída.

    `total_impreso`: el total leído directamente del texto del PDF con una
    regex (no por el modelo) — ver `core/ingesta/pdf_texto.py::total_impreso`.
    Si es `None` (no se pudo leer con la regex), ese control se omite en vez
    de fallar — la doble lectura es una capa extra, no la única.
    """
    if tolerancia_total_ratio != 0:
        raise ValueError("No se admite tolerancia porcentual para aprobar una factura.")
    items = []
    for i, c in enumerate(factura.conceptos):
        esperado = Decimal(str(c.cantidad)) * Decimal(str(c.precio_unitario))
        esperado_centavos = _centavos(esperado)
        diferencia = (esperado_centavos - _centavos(c.importe)) / 100
        ok = abs(diferencia) <= tolerancia_linea
        items.append(
            ItemValidado(indice=i, ok=ok, importe_esperado=esperado_centavos / 100,
                         diferencia=diferencia)
        )

    suma_conceptos = sum(c.importe for c in factura.conceptos)
    suma_impuestos = sum(i.importe for i in factura.impuestos)
    suma_recargos = sum(r.importe for r in factura.recargos)
    suma_creditos = sum(c.importe for c in factura.creditos)

    # subtotal_presente/total_presente son deliberadamente independientes de
    # subtotal_ok/total_ok -- ver docstring de ResultadoValidacion.factura_valida
    # (hallazgo A-4). El fallback a suma_conceptos/total_calculado sigue
    # existiendo para poder MOSTRAR algo razonable en motivos_de_falla() y en
    # el resto de ResultadoValidacion, pero nunca alcanza por sí solo para
    # que la factura se considere válida.
    subtotal_presente = factura.subtotal is not None
    subtotal_referencia = factura.subtotal if subtotal_presente else suma_conceptos
    diferencia_subtotal = (sum(_centavos(c.importe) for c in factura.conceptos)
                           - _centavos(subtotal_referencia)) / 100
    subtotal_ok = diferencia_subtotal == 0

    total_presente = factura.total is not None
    # Fórmula contable: los créditos/bonificaciones reducen el total exigible.
    total_calculado = subtotal_referencia + suma_impuestos + suma_recargos - suma_creditos
    total_referencia = factura.total if total_presente else total_calculado
    diferencia_total = (
        _centavos(subtotal_referencia)
        + sum(_centavos(i.importe) for i in factura.impuestos)
        + sum(_centavos(r.importe) for r in factura.recargos)
        - sum(_centavos(c.importe) for c in factura.creditos)
        - _centavos(total_referencia)
    ) / 100
    total_ok = diferencia_total == 0

    if total_impreso is None or factura.total is None:
        total_impreso_ok = True
    else:
        total_impreso_ok = _centavos(factura.total) == _centavos(total_impreso)
    diferencia_impreso = (
        (_centavos(factura.total) - _centavos(total_impreso)) / 100
        if factura.total is not None and total_impreso is not None else 0.0
    )

    return ResultadoValidacion(
        items=items,
        suma_conceptos=suma_conceptos,
        suma_impuestos=suma_impuestos,
        suma_recargos=suma_recargos,
        suma_creditos=suma_creditos,
        subtotal_ok=subtotal_ok,
        total_ok=total_ok,
        total_impreso_ok=total_impreso_ok,
        todas_las_lineas_ok=all(i.ok for i in items),
        subtotal_presente=subtotal_presente,
        total_presente=total_presente,
        diferencia_subtotal=diferencia_subtotal,
        diferencia_total=diferencia_total,
        diferencia_impreso=diferencia_impreso,
    )
