"""Cálculo de una comparación de dos períodos, compartido por la pantalla
"Ver" y el Excel que se descarga desde ahí (`web/app.py`).

docs/auditoria-2026-09-web.md, E-11: antes cada uno tenía su propia copia
de esta lógica (`_analisis` y `get_ver_excel`), y se habían separado --
`get_ver_excel` no tenía en cuenta `acumulado`, ni la alerta de período
faltante, ni los conceptos con cantidad sintética, así que el Excel podía
mostrar una lista de alertas distinta de la que la persona acababa de ver
en pantalla para la misma comparación. Ahora las dos pantallas llaman a
`calcular_comparacion` y usan el mismo resultado."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import requests

from core.almacenamiento import (
    alertas_del_periodo,
    componentes_financieros_periodo,
    recargos_del_periodo,
)
from core.analisis.agregacion import (
    PREFIJO_SIN_HOMOLOGAR,
    FilaConcepto,
    acumular_conceptos,
    agregar_conceptos,
    conceptos_con_cantidad_neta_cero,
    conceptos_con_cantidad_neta_negativa,
    etiqueta_legible,
)
from core.analisis.alertas import (
    Alerta,
    alertas_por_periodo_faltante,
    generar_alertas,
)
from core.analisis.real import inflacion_del_periodo, variacion_real
from core.analisis.variacion import (
    DescomposicionVariacion,
    descomponer_conceptos,
    efecto_dominante,
    top_conceptos_por_variacion,
)
from core.extraccion.esquema import FacturaExtraida, Recargo
from core.macro.ipc import leer_ipc
from core.relato import DatosRelato, generar_relato_determinista


@dataclass(frozen=True)
class ResultadoComparacion:
    descomposiciones: list[DescomposicionVariacion]
    componentes_0: dict[str, float]
    componentes_1: dict[str, float]
    ipc_periodo_pct: float | None
    variacion_real_pct: float | None
    tipo_dominante: str
    proporcion_dominante: float
    anomalos_0: list[str]
    anomalos_1: list[str]
    anomalos: list[str]  # etiquetas legibles, para mostrar en el aviso
    conceptos_sin_clasificar: bool
    alertas: list[Alerta]
    relato: str
    avisos_calculo: list[str]


def _filas_del_periodo(con, *, servicio: str, periodo: str) -> list[FilaConcepto]:
    filas = con.execute(
        """SELECT c.concepto_normalizado, c.descripcion, c.cantidad, c.importe, c.unidad
           FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
        [servicio, periodo],
    ).fetchall()
    return [FilaConcepto(cn, desc, cant, imp, unidad) for cn, desc, cant, imp, unidad in filas]


def calcular_comparacion(
    con,
    *,
    servicio: str,
    periodo_0: str,
    periodo_1: str,
    periodos_del_servicio: list[tuple[str, str | None]],
) -> ResultadoComparacion:
    """`periodos_del_servicio`: `(periodo_desde, periodo_hasta)` de TODOS
    los períodos cargados de `servicio` (de
    `web/app.py::_periodos_del_servicio`), para la alerta de período
    faltante -- no se recalcula acá para no repetir esa consulta en cada
    llamada."""
    avisos_calculo: list[str] = []
    try:
        fechas_periodos = [
            (date.fromisoformat(d), date.fromisoformat(h) if h else None)
            for d, h in periodos_del_servicio
        ]
    except ValueError:
        fechas_periodos = []
        avisos_calculo.append(
            "No se pudieron calcular alertas de período: hay una fecha guardada con "
            "formato inválido."
        )
    alertas_periodo_faltante = (
        alertas_por_periodo_faltante(fechas_periodos) if fechas_periodos else []
    )

    filas_0 = _filas_del_periodo(con, servicio=servicio, periodo=periodo_0)
    filas_1 = _filas_del_periodo(con, servicio=servicio, periodo=periodo_1)
    acumulado_0 = acumular_conceptos(filas_0)
    acumulado_1 = acumular_conceptos(filas_1)
    agregado_0 = agregar_conceptos(filas_0, acumulado=acumulado_0)
    agregado_1 = agregar_conceptos(filas_1, acumulado=acumulado_1)
    descomposiciones = descomponer_conceptos(agregado_0, agregado_1)

    conceptos_sin_clasificar = any(
        d.concepto.startswith(PREFIJO_SIN_HOMOLOGAR) for d in descomposiciones
    )

    anomalos_0 = conceptos_con_cantidad_neta_cero(
        filas_0, acumulado=acumulado_0
    ) + conceptos_con_cantidad_neta_negativa(filas_0, acumulado=acumulado_0)
    anomalos_1 = conceptos_con_cantidad_neta_cero(
        filas_1, acumulado=acumulado_1
    ) + conceptos_con_cantidad_neta_negativa(filas_1, acumulado=acumulado_1)
    anomalos = sorted({etiqueta_legible(a) for a in anomalos_0 + anomalos_1})

    componentes_0 = componentes_financieros_periodo(con, servicio=servicio, periodo_desde=periodo_0)
    componentes_1 = componentes_financieros_periodo(con, servicio=servicio, periodo_desde=periodo_1)
    total_pagable_0 = componentes_0["total_pagable"]
    total_pagable_1 = componentes_1["total_pagable"]

    ipc_periodo_pct: float | None = None
    variacion_real_pct: float | None = None
    try:
        fecha_0 = date.fromisoformat(periodo_0)
        fecha_1 = date.fromisoformat(periodo_1)
        df_ipc = leer_ipc()
        ipc_periodo_pct = inflacion_del_periodo(fecha_0, fecha_1, df_ipc=df_ipc)
        if total_pagable_0 != 0:
            vr = variacion_real(total_pagable_0, fecha_0, total_pagable_1, fecha_1, df_ipc=df_ipc)
            variacion_real_pct = vr.variacion_real_pct
        else:
            avisos_calculo.append(
                "No se pudo calcular la variación real: el total pagable del período "
                "base es $0 (no hay contra qué comparar)."
            )
    except requests.exceptions.RequestException as exc:
        avisos_calculo.append(f"No se pudo descargar el IPC (problema de red): {exc}")
    except ValueError as exc:
        avisos_calculo.append(f"No se pudo calcular la variación real: {exc}")

    recargos_periodo_1 = [
        Recargo(nombre=n, importe=i)
        for n, i in recargos_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
    ]
    factura_agregada = FacturaExtraida(
        emisor=None,
        cuit=None,
        servicio=servicio,
        periodo_desde=periodo_1,
        periodo_hasta=None,
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        recargos=recargos_periodo_1,
    )
    # docs/auditoria-2026-09-web.md, E-5: `ipc_periodo_pct` va tal cual (con
    # su `None` si no se pudo calcular) -- generar_alertas ya sabe no correr
    # la regla de "precio sobre IPC" sin un IPC real, en vez de tratar un
    # IPC desconocido como si fuera cero.
    alertas = (
        generar_alertas(
            factura_agregada,
            descomposiciones,
            ipc_periodo_pct=ipc_periodo_pct,
            conceptos_con_cantidad_sintetica=frozenset(anomalos_0 + anomalos_1),
        )
        + alertas_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
        + alertas_periodo_faltante
    )

    tipo_dominante, proporcion_dominante = efecto_dominante(descomposiciones)
    concepto_destacado_lista = top_conceptos_por_variacion(descomposiciones, 1)
    concepto_destacado = concepto_destacado_lista[0] if concepto_destacado_lista else None

    relato = generar_relato_determinista(
        DatosRelato(
            servicio=servicio,
            periodo_0=periodo_0,
            periodo_1=periodo_1,
            total_0=total_pagable_0,
            total_1=total_pagable_1,
            consumos_0=componentes_0["consumos"],
            consumos_1=componentes_1["consumos"],
            impuestos_0=componentes_0["impuestos"],
            impuestos_1=componentes_1["impuestos"],
            recargos_0=componentes_0["recargos"],
            recargos_1=componentes_1["recargos"],
            creditos_0=componentes_0["creditos"],
            creditos_1=componentes_1["creditos"],
            tipo_dominante=tipo_dominante,
            proporcion_dominante=abs(proporcion_dominante),
            efecto_precio_total=sum(d.efecto_precio for d in descomposiciones),
            efecto_cantidad_total=sum(d.efecto_cantidad for d in descomposiciones),
            variacion_real_pct=variacion_real_pct,
            inflacion_pct=ipc_periodo_pct,
            concepto_destacado=concepto_destacado,
        )
    )

    return ResultadoComparacion(
        descomposiciones=descomposiciones,
        componentes_0=componentes_0,
        componentes_1=componentes_1,
        ipc_periodo_pct=ipc_periodo_pct,
        variacion_real_pct=variacion_real_pct,
        tipo_dominante=tipo_dominante,
        proporcion_dominante=proporcion_dominante,
        anomalos_0=anomalos_0,
        anomalos_1=anomalos_1,
        anomalos=anomalos,
        conceptos_sin_clasificar=conceptos_sin_clasificar,
        alertas=alertas,
        relato=relato,
        avisos_calculo=avisos_calculo,
    )
