"""Descomposición precio-cantidad: para cada concepto, separa cuánto de la
variación en pesos entre dos períodos es porque cambió la CANTIDAD y cuánto
porque cambió el PRECIO unitario.

Por qué existe (ver el pedido original): "los servicios telefónicos de
Movistar pueden aumentar por la cantidad de chips o llamadas o uso, etc, o
también porque aumentó el costo unitario". Sin esta descomposición, un
aumento del 20% no dice si hay que hablar con Movistar (precio) o revisar
cuántas líneas tiene la empresa (cantidad).

Es la descomposición estándar de análisis de variaciones (la misma que usa
un análisis de desvíos de costos): dado un concepto con cantidad `q` y
precio unitario `p` en dos períodos (0 y 1),

    efecto_cantidad = (q1 - q0) * p0
    efecto_precio   = (p1 - p0) * q0
    efecto_cruzado  = (q1 - q0) * (p1 - p0)

y por construcción algebraica (identidad, no aproximación):

    efecto_cantidad + efecto_precio + efecto_cruzado == (q1*p1) - (q0*p0)
                                                       == total_1 - total_0

Para un concepto sin cantidad explícita (un cargo fijo, un alquiler) se usa
q0 = q1 = 1 y toda la variación cae en efecto_precio — es matemáticamente
correcto: no hay "cantidad" que descomponer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DescomposicionVariacion:
    concepto: str
    cantidad_0: float
    precio_0: float
    cantidad_1: float
    precio_1: float
    efecto_cantidad: float
    efecto_precio: float
    efecto_cruzado: float

    @property
    def total_0(self) -> float:
        return self.cantidad_0 * self.precio_0

    @property
    def total_1(self) -> float:
        return self.cantidad_1 * self.precio_1

    @property
    def variacion_total(self) -> float:
        return self.total_1 - self.total_0

    @property
    def variacion_pct(self) -> float | None:
        if self.total_0 == 0:
            return None
        return self.variacion_total / self.total_0


def descomponer_variacion(
    concepto: str, *, cantidad_0: float, precio_0: float, cantidad_1: float, precio_1: float
) -> DescomposicionVariacion:
    """Descompone la variación de un concepto entre dos períodos en efecto
    cantidad, efecto precio y efecto cruzado (identidad exacta, ver
    docstring del módulo)."""
    efecto_cantidad = (cantidad_1 - cantidad_0) * precio_0
    efecto_precio = (precio_1 - precio_0) * cantidad_0
    efecto_cruzado = (cantidad_1 - cantidad_0) * (precio_1 - precio_0)

    return DescomposicionVariacion(
        concepto=concepto,
        cantidad_0=cantidad_0,
        precio_0=precio_0,
        cantidad_1=cantidad_1,
        precio_1=precio_1,
        efecto_cantidad=efecto_cantidad,
        efecto_precio=efecto_precio,
        efecto_cruzado=efecto_cruzado,
    )


def descomponer_conceptos(
    conceptos_0: dict[str, tuple[float, float]], conceptos_1: dict[str, tuple[float, float]]
) -> list[DescomposicionVariacion]:
    """Descompone todos los conceptos homologados presentes en cualquiera de
    los dos períodos. `conceptos_N` es {concepto_normalizado: (cantidad, precio_unitario)}.

    Un concepto ausente en un período se trata como cantidad 0 (apareció o
    desapareció) — el efecto cantidad captura exactamente eso, y coincide
    con la alerta de "concepto nuevo/desaparecido" de alertas.py."""
    todos = set(conceptos_0) | set(conceptos_1)
    resultado = []
    for concepto in sorted(todos):
        c0, p0 = conceptos_0.get(concepto, (0.0, 0.0))
        c1, p1 = conceptos_1.get(concepto, (0.0, 0.0))
        # Si el concepto no existía en el período 0, no hay "precio anterior"
        # de referencia -- se usa el precio del período 1 para que
        # efecto_precio dé 0 y toda la variación caiga en efecto_cantidad
        # (que es lo correcto: la variación es 100% "apareció el concepto").
        if c0 == 0.0 and p0 == 0.0:
            p0 = p1
        if c1 == 0.0 and p1 == 0.0:
            p1 = p0
        resultado.append(
            descomponer_variacion(concepto, cantidad_0=c0, precio_0=p0, cantidad_1=c1, precio_1=p1)
        )
    return resultado
