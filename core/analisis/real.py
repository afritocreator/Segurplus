"""Variación nominal vs. real: en Argentina "aumentó 8%" no dice nada si la
inflación del período fue 10% -- en términos reales, bajó. Este módulo pasa
un importe nominal a pesos constantes usando el IPC (core/deflactor,
heredado de Consultora) y calcula la variación ya descontada la inflación.

Es el número que sirve para discutir con un proveedor: un aumento nominal
del 15% con IPC del 12% en el período es un aumento REAL del ~2.7%, no del
15% -- la diferencia importa para saber si vale la pena reclamar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from core.deflactor import a_pesos_constantes, coeficiente_ajuste


@dataclass
class VariacionReal:
    importe_0: float
    importe_1: float
    importe_0_en_pesos_de_1: float  # importe_0 llevado a pesos constantes de la fecha 1
    variacion_nominal_pct: float
    variacion_real_pct: float


def variacion_real(
    importe_0: float,
    fecha_0: date,
    importe_1: float,
    fecha_1: date,
    *,
    df_ipc: pl.DataFrame | None = None,
) -> VariacionReal:
    """Compara `importe_0` (de `fecha_0`) contra `importe_1` (de `fecha_1`),
    tanto en términos nominales como reales (deflactado por IPC).

    variacion_nominal_pct = importe_1 / importe_0 - 1
    importe_0_en_pesos_de_1 = importe_0 llevado a moneda constante de fecha_1
    variacion_real_pct = importe_1 / importe_0_en_pesos_de_1 - 1
    """
    if importe_0 == 0:
        raise ValueError(
            "No se puede calcular una variación con importe_0 == 0 (división por cero)"
        )

    importe_0_en_pesos_de_1 = a_pesos_constantes(importe_0, fecha_0, fecha_1, df_ipc=df_ipc)

    return VariacionReal(
        importe_0=importe_0,
        importe_1=importe_1,
        importe_0_en_pesos_de_1=importe_0_en_pesos_de_1,
        variacion_nominal_pct=(importe_1 / importe_0) - 1,
        variacion_real_pct=(importe_1 / importe_0_en_pesos_de_1) - 1,
    )


def inflacion_del_periodo(
    fecha_0: date, fecha_1: date, *, df_ipc: pl.DataFrame | None = None
) -> float:
    """Inflación acumulada entre `fecha_0` y `fecha_1`, como proporción
    (0.12 = 12%), calculada directo del IPC -- no derivada de restar dos
    variaciones porcentuales ya calculadas (nominal y real), que da un
    resultado distinto y de signo contrario.

    inflación = coeficiente_ajuste(fecha_0, fecha_1) - 1 = IPC(fecha_1)/IPC(fecha_0) - 1

    Por qué existe: antes de este fix, `apps/segurplus/paginas/evolucion.py`
    calculaba `ipc_periodo_pct = variacion_real_pct - variacion_nominal_pct`
    para usarlo en las alertas de precio -- una resta que da, por
    construcción algebraica, un número NEGATIVO para cualquier inflación
    positiva (con r = (1+n)/(1+π) - 1, se cumple r - n = -(1+n)·π/(1+π) < 0
    para todo π > 0), y que después se aplastaba a 0 con `max(...,0.0)`. El
    resultado: la alerta de "precio por encima del IPC" comparaba siempre
    contra 0% de inflación, y el texto llegaba a decir "por encima del IPC"
    en casos donde el aumento estuvo por debajo (ver docs/auditoria-2026-09.md,
    hallazgo A-1). Esta función reemplaza esa cuenta con la correcta,
    reutilizando `core.deflactor`, que ya está testeado."""
    return coeficiente_ajuste(fecha_0, fecha_1, df_ipc=df_ipc) - 1
