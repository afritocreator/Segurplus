"""Validación aritmética determinística de una factura extraída.

Port de `lib/invoice/validate.ts` de Kleric- (mismo problema: "no es
infalible confiar ciegamente en el modelo, acá se audita en código"), con
las tolerancias ya calibradas ahí contra facturas reales ($1 por línea, 2%
en el total), más dos controles propios de facturas de servicios que
Kleric- no necesita:

- impuestos + recargos también tienen que cerrar contra el total, no solo
  el subtotal (una factura de servicios discrimina IVA e Ingresos Brutos
  aparte, a diferencia de una factura de mercadería).
- doble lectura del total: se compara el total que dijo el modelo contra
  el total leído con una regex directamente del texto del PDF
  (`core/ingesta/pdf_texto.py::total_impreso`). Dos lecturas independientes
  del mismo dato — si no coinciden, algo está mal y no hay que confiar en
  ninguna de las dos.
- `subtotal`/`total` AUSENTES (el modelo devolvió `null`) son un motivo de
  cuarentena en sí mismo, no algo que se tapa con un fallback. Antes, si
  faltaban, se usaba el propio cálculo (`suma_conceptos`,
  `subtotal + impuestos + recargos`) como referencia -- y esa referencia se
  comparaba contra sí misma, así que CUALQUIER número pasaba la validación
  con esos campos en `null` (ver docs/auditoria-2026-09.md, hallazgo A-4).

Si CUALQUIERA de estos controles falla, la factura va a cuarentena (ver
`core/almacenamiento.py::guardar_en_cuarentena`) y NO entra al análisis.
CLAUDE.md: "nunca mostrarle a un usuario un número no verificado".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.extraccion.esquema import FacturaExtraida

TOLERANCIA_LINEA = 1.0  # $1 de tolerancia por redondeos de la factura original
TOLERANCIA_TOTAL_RATIO = 0.02  # 2% de tolerancia en el total (percepciones, redondeos)


@dataclass
class ItemValidado:
    indice: int
    ok: bool
    importe_esperado: float


@dataclass
class ResultadoValidacion:
    items: list[ItemValidado] = field(default_factory=list)
    suma_conceptos: float = 0.0
    suma_impuestos: float = 0.0
    suma_recargos: float = 0.0
    subtotal_ok: bool = True
    total_ok: bool = True
    total_impreso_ok: bool = True
    todas_las_lineas_ok: bool = True
    subtotal_presente: bool = True
    total_presente: bool = True

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
            motivos.append("la suma de los conceptos no coincide con el subtotal")
        if self.total_presente and not self.total_ok:
            motivos.append("subtotal + impuestos + recargos no coincide con el total")
        if not self.total_impreso_ok:
            motivos.append("el total extraído no coincide con el total impreso en el PDF")
        return motivos


def validar_factura(
    factura: FacturaExtraida,
    *,
    total_impreso: float | None = None,
    tolerancia_linea: float = TOLERANCIA_LINEA,
    tolerancia_total_ratio: float = TOLERANCIA_TOTAL_RATIO,
) -> ResultadoValidacion:
    """Corre los cuatro controles aritméticos sobre una factura extraída.

    `total_impreso`: el total leído directamente del texto del PDF con una
    regex (no por el modelo) — ver `core/ingesta/pdf_texto.py::total_impreso`.
    Si es `None` (no se pudo leer con la regex), ese control se omite en vez
    de fallar — la doble lectura es una capa extra, no la única.
    """
    items = []
    for i, c in enumerate(factura.conceptos):
        esperado = c.cantidad * c.precio_unitario
        ok = abs(esperado - c.importe) <= tolerancia_linea
        items.append(ItemValidado(indice=i, ok=ok, importe_esperado=esperado))

    suma_conceptos = sum(c.importe for c in factura.conceptos)
    suma_impuestos = sum(i.importe for i in factura.impuestos)
    suma_recargos = sum(r.importe for r in factura.recargos)

    # subtotal_presente/total_presente son deliberadamente independientes de
    # subtotal_ok/total_ok -- ver docstring de ResultadoValidacion.factura_valida
    # (hallazgo A-4). El fallback a suma_conceptos/total_calculado sigue
    # existiendo para poder MOSTRAR algo razonable en motivos_de_falla() y en
    # el resto de ResultadoValidacion, pero nunca alcanza por sí solo para
    # que la factura se considere válida.
    subtotal_presente = factura.subtotal is not None
    subtotal_referencia = factura.subtotal if subtotal_presente else suma_conceptos
    tol_subtotal = max(tolerancia_linea, abs(subtotal_referencia) * tolerancia_total_ratio)
    subtotal_ok = abs(suma_conceptos - subtotal_referencia) <= tol_subtotal

    total_presente = factura.total is not None
    total_calculado = subtotal_referencia + suma_impuestos + suma_recargos
    total_referencia = factura.total if total_presente else total_calculado
    tol_total = max(tolerancia_linea, abs(total_referencia) * tolerancia_total_ratio)
    total_ok = abs(total_calculado - total_referencia) <= tol_total

    if total_impreso is None or factura.total is None:
        total_impreso_ok = True
    else:
        tol_impreso = max(tolerancia_linea, abs(total_impreso) * tolerancia_total_ratio)
        total_impreso_ok = abs(factura.total - total_impreso) <= tol_impreso

    return ResultadoValidacion(
        items=items,
        suma_conceptos=suma_conceptos,
        suma_impuestos=suma_impuestos,
        suma_recargos=suma_recargos,
        subtotal_ok=subtotal_ok,
        total_ok=total_ok,
        total_impreso_ok=total_impreso_ok,
        todas_las_lineas_ok=all(i.ok for i in items),
        subtotal_presente=subtotal_presente,
        total_presente=total_presente,
    )
