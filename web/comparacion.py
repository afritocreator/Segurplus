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

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
import requests

from core.almacenamiento import (
    alertas_del_periodo,
    componentes_financieros_periodo,
    recargos_del_periodo,
    sincronizar_casos_alertas,
    transaccion,
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
from core.macro.ipc import CACHE_PATH, SERIE_ID, leer_ipc
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


def snapshot_comparacion(con, *, servicio: str, periodo_0: str, periodo_1: str) -> str:
    """Identifica datos aprobados, reglas e IPC usados por Ver y Excel.

    Es una función de lectura: abrir ``Ver`` nunca escribe ni descarga
    datos. La persistencia de publicaciones IPC sucede durante el recálculo
    operativo de casos.
    """
    digest = hashlib.sha256()
    parametros = [servicio, periodo_0, periodo_1]
    facturas = con.execute(
        """SELECT * FROM facturas WHERE servicio = ? AND estado = 'aprobada'
           AND periodo_desde IN (?, ?)""",
        parametros,
    ).fetchall()
    hashes = sorted(str(fila[0]) for fila in facturas)
    digest.update(json.dumps(sorted(repr(fila) for fila in facturas)).encode())
    for tabla in ("conceptos", "impuestos", "recargos", "creditos", "alertas"):
        if not hashes:
            continue
        marcadores = ", ".join("?" for _ in hashes)
        filas = con.execute(
            f"SELECT * FROM {tabla} WHERE hash_pdf IN ({marcadores})", hashes
        ).fetchall()
        digest.update(tabla.encode())
        digest.update(json.dumps(sorted(repr(fila) for fila in filas)).encode())
    raiz = Path(__file__).resolve().parent.parent
    reglas = [
        raiz / "data" / nombre
        for nombre in ("alertas.yaml", "homologacion.yaml", "operacion.yaml")
    ]
    reglas.extend(sorted((raiz / "data" / "conceptos").glob("*.yaml")))
    reglas.extend(sorted((raiz / "core" / "analisis").glob("*.py")))
    for ruta in reglas:
        digest.update(str(ruta.relative_to(raiz)).encode())
        digest.update(ruta.read_bytes())
    if CACHE_PATH.exists():
        datos_json = pl.read_parquet(CACHE_PATH).write_json()
        hash_ipc = hashlib.sha256(datos_json.encode()).hexdigest()
        digest.update(hash_ipc.encode())
    else:
        digest.update(b"sin-ipc")
    return digest.hexdigest()


def registrar_publicacion_ipc(con) -> str | None:
    """Guarda una copia versionada de IPC como parte de un recálculo escrito."""
    if not CACHE_PATH.exists():
        return None
    datos_json = pl.read_parquet(CACHE_PATH).write_json()
    hash_ipc = hashlib.sha256(datos_json.encode()).hexdigest()
    con.execute(
        """INSERT INTO publicaciones_ipc (hash_contenido, serie_id, datos_json)
           VALUES (?, ?, ?) ON CONFLICT (hash_contenido) DO NOTHING""",
        [hash_ipc, SERIE_ID, datos_json],
    )
    return hash_ipc


def _filas_del_periodo(con, *, servicio: str, periodo: str) -> list[FilaConcepto]:
    filas = con.execute(
        """SELECT c.concepto_normalizado, c.descripcion, c.cantidad, c.importe, c.unidad
           FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ?
             AND f.estado = 'aprobada' AND f.moneda = 'ARS'""",
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


def sincronizar_casos_pendientes(con, *, servicio: str | None = None) -> int:
    """Materializa comparaciones aprobadas post-commit, nunca desde un GET.

    Un ticket cambiado mientras se calcula impide publicar un resultado
    obsoleto. Si falta IPC, quedan los casos no dependientes de él y la cola
    sigue pendiente para reintentar cuando exista una publicación válida.
    """
    filtro = "WHERE servicio = ?" if servicio else ""
    pendientes = con.execute(
        f"SELECT servicio, ticket FROM comparaciones_pendientes {filtro}",
        [servicio] if servicio else [],
    ).fetchall()
    procesados = 0
    for nombre_servicio, ticket in pendientes:
        periodos = con.execute(
            """SELECT periodo_desde, max(periodo_hasta) FROM facturas
               WHERE servicio = ? AND estado = 'aprobada' AND moneda = 'ARS'
                 AND periodo_desde IS NOT NULL
               GROUP BY periodo_desde ORDER BY periodo_desde""",
            [nombre_servicio],
        ).fetchall()
        calculados = []
        ipc_completo = True
        for (base, _), (siguiente, _) in zip(periodos, periodos[1:]):
            referencia = f"comparacion:{nombre_servicio}:{base}:{siguiente}"
            resultado = calcular_comparacion(
                con, servicio=nombre_servicio, periodo_0=base, periodo_1=siguiente,
                periodos_del_servicio=periodos,
            )
            ipc_completo &= resultado.ipc_periodo_pct is not None
            calculados.append((referencia, resultado.alertas))
        with transaccion(con):
            actual = con.execute(
                "SELECT ticket FROM comparaciones_pendientes WHERE servicio = ?",
                [nombre_servicio],
            ).fetchone()
            if actual is None or actual[0] != ticket:
                continue
            referencias = {ref for ref, _ in calculados}
            anteriores = con.execute(
                """SELECT DISTINCT hash_pdf FROM casos_alerta
                   WHERE hash_pdf LIKE ?""",
                [f"comparacion:{nombre_servicio}:%"],
            ).fetchall()
            for referencia, alertas in calculados:
                sincronizar_casos_alertas(con, referencia=referencia, alertas=alertas)
            for (referencia,) in anteriores:
                if referencia not in referencias:
                    sincronizar_casos_alertas(con, referencia=referencia, alertas=[])
            if ipc_completo:
                registrar_publicacion_ipc(con)
                con.execute(
                    "DELETE FROM comparaciones_pendientes WHERE servicio = ? AND ticket = ?",
                    [nombre_servicio, ticket],
                )
            procesados += 1
    return procesados
