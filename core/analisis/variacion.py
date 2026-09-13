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
from pathlib import Path

import yaml

RUTA_ALERTAS = Path(__file__).resolve().parents[2] / "data" / "alertas.yaml"


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


def _umbral_dominancia() -> float:
    """Sin cache y sin lectura a nivel de módulo, a propósito -- mismo
    patrón que `core/analisis/alertas.py::_leer_umbrales` y
    `core/analisis/homologacion.py::umbral_coincidencia` (un YAML corrupto
    no debe tumbar el import ni la app)."""
    datos = yaml.safe_load(RUTA_ALERTAS.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "umbral_efecto_dominante" not in datos:
        raise ValueError(
            f"{RUTA_ALERTAS} no tiene la forma esperada (falta umbral_efecto_dominante)"
        )
    return float(datos["umbral_efecto_dominante"])


def efecto_dominante(
    descomposiciones: list[DescomposicionVariacion], *, umbral: float | None = None
) -> tuple[str, float]:
    """Resume TODA la comparación (no concepto por concepto) en una sola
    frase: ¿el cambio total fue mayormente por CANTIDAD, por PRECIO, o
    MIXTO? Es la respuesta literal a la pregunta que motivó el proyecto
    ("¿aumentó porque hay más líneas o porque subió el precio?"), hoy solo
    deducible mirando un gráfico apilado concepto por concepto.

    Suma los efectos de TODOS los conceptos (la suma sigue cumpliendo la
    identidad algebraica de `descomponer_variacion`, porque es una suma de
    identidades):

        Σefecto_cantidad + Σefecto_precio + Σefecto_cruzado == variación total

    Devuelve `("cantidad" | "precio" | "mixto" | "sin_variacion",
    proporción)`, donde `proporción` es la fracción de la variación total
    que explica ese efecto (`Σefecto_precio / variación_total` para
    "precio", etc.) -- "mixto" cuando ningún efecto solo llega al umbral
    de `data/alertas.yaml` (`umbral_efecto_dominante`, nunca hardcodeado,
    CLAUDE.md), "sin_variacion" si la variación total es exactamente 0."""
    umbral = umbral if umbral is not None else _umbral_dominancia()
    suma_cantidad = sum(d.efecto_cantidad for d in descomposiciones)
    suma_precio = sum(d.efecto_precio for d in descomposiciones)
    suma_cruzado = sum(d.efecto_cruzado for d in descomposiciones)
    variacion_total = suma_cantidad + suma_precio + suma_cruzado

    if variacion_total == 0:
        return "sin_variacion", 0.0

    proporcion_cantidad = suma_cantidad / variacion_total
    proporcion_precio = suma_precio / variacion_total

    if abs(proporcion_precio) >= umbral:
        return "precio", proporcion_precio
    if abs(proporcion_cantidad) >= umbral:
        return "cantidad", proporcion_cantidad
    return "mixto", proporcion_precio


def top_conceptos_por_variacion(
    descomposiciones: list[DescomposicionVariacion], n: int
) -> list[DescomposicionVariacion]:
    """Los `n` conceptos con mayor variación en VALOR ABSOLUTO (suben o
    bajan, ambos son relevantes), para acotar un gráfico a lo que importa
    en vez de mostrar decenas de barras minúsculas. No modifica ni agrega
    ningún dato -- solo reordena y trunca; la tabla de detalle de la
    página sigue mostrando todos los conceptos."""
    return sorted(descomposiciones, key=lambda d: abs(d.variacion_total), reverse=True)[:n]
