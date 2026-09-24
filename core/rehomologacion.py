"""Recalcula la homologación de las filas YA GUARDADAS en `conceptos`, con
el diccionario ACTUAL de `data/conceptos/*.yaml` -- sin llamar a Gemini ni
re-extraer nada.

Por qué existe: editar `data/conceptos/*.yaml` (agregar un alias, después
de mirar la pantalla de "Conceptos sin clasificar") solo cambia cómo
homologa una factura NUEVA que se procese después -- las que ya están en
`data/reales/facturas.duckdb` quedan con el `concepto_normalizado` viejo.
Sin este módulo, cada ajuste del diccionario obligaría a re-extraer con
Gemini (gastando la cuota de `max_llamadas_gemini_por_hora`,
`data/operacion.yaml`) solo para que la homologación se vuelva a
correr -- un desperdicio, porque el texto de la descripción ya está
guardado y no cambió. Este módulo NUNCA importa `core.extraccion.gemini`
ni nada que llame a la red -- ver `tests/test_rehomologacion.py`.

Cáscara: `recalcular()` es puro, testeable sin DuckDB (recibe el
diccionario ya cargado). `leer_filas_a_rehomologar` y `aplicar_cambios`
son los únicos que tocan la conexión -- ver `scripts/rehomologar.py` para
el CLI que los orquesta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import duckdb

from core.almacenamiento import transaccion
from core.analisis.homologacion import homologar_concepto, umbral_coincidencia


@dataclass
class FilaARehomologar:
    hash_pdf: str
    orden: int
    descripcion: str
    servicio: str | None
    concepto_actual: str | None
    score_actual: float | None


@dataclass
class CambioHomologacion:
    hash_pdf: str
    orden: int
    descripcion: str
    servicio: str | None
    concepto_antes: str | None
    score_antes: float | None
    concepto_despues: str | None
    score_despues: float
    candidatos_empatados: tuple[str, ...] = ()
    motivo_omision: str | None = None

    @property
    def tipo(self) -> str:
        """ "omitido": no se evaluó -- fila sin servicio, o con diccionario
        vacío para su servicio (ver `motivo_omision`); homologar a ciegas
        ahí sería peor que no homologar (docs/auditoria-2026-09-rediseno.md,
        A-40). "nuevo": no homologaba, ahora sí (un alias nuevo lo captó).
        "regresion": homologaba, ahora no -- posible señal de que un alias
        nuevo le robó el match a este concepto (ver el aviso en el
        docstring de `recalcular`). "cambio": homologaba a un concepto,
        ahora homologa a otro. "sin_cambio": el concepto no varió (el
        score puede haber cambiado igual, ej. por un alias nuevo del mismo
        concepto)."""
        if self.motivo_omision is not None:
            return "omitido"
        if self.concepto_antes is None and self.concepto_despues is not None:
            return "nuevo"
        if self.concepto_antes is not None and self.concepto_despues is None:
            return "regresion"
        if self.concepto_antes != self.concepto_despues:
            return "cambio"
        return "sin_cambio"


def recalcular(
    filas: list[FilaARehomologar],
    diccionarios_por_servicio: dict[str | None, dict[str, list[str]]],
    *,
    umbral: float | None = None,
) -> list[CambioHomologacion]:
    """Puro -- no toca DuckDB ni el filesystem, los diccionarios ya vienen
    cargados. `diccionarios_por_servicio`: mapea cada `servicio` (o `None`)
    al diccionario ya obtenido con `cargar_diccionario(servicio)` -- se
    carga UNA VEZ por servicio afuera de acá, no una vez por fila (con
    decenas de conceptos por servicio, releer el YAML por cada uno sería
    decenas de lecturas de disco redundantes).

    Una fila sin `servicio` o con diccionario vacío para su servicio se
    OMITE (no se toca, `tipo == "omitido"`), no aborta el lote entero --
    antes de esta corrección (docs/auditoria-2026-09-piloto.md, A-51) una
    sola factura sin servicio detectado dejaba la re-homologación de TODA
    la base inutilizable, sin forma de arreglarla mientras esa factura
    siguiera sin servicio. El criterio de fondo (nunca homologar a ciegas
    contra un diccionario vacío o inexistente, A-40) se mantiene -- ahora
    por fila, no por lote."""
    umbral = umbral if umbral is not None else umbral_coincidencia()
    cambios = []
    for fila in filas:
        if fila.servicio is None:
            motivo = "sin servicio asignado"
        elif not diccionarios_por_servicio.get(fila.servicio):
            motivo = f"diccionario vacío para {fila.servicio!r}"
        else:
            motivo = None
        if motivo is not None:
            cambios.append(
                CambioHomologacion(
                    hash_pdf=fila.hash_pdf,
                    orden=fila.orden,
                    descripcion=fila.descripcion,
                    servicio=fila.servicio,
                    concepto_antes=fila.concepto_actual,
                    score_antes=fila.score_actual,
                    concepto_despues=fila.concepto_actual,
                    score_despues=fila.score_actual or 0.0,
                    motivo_omision=motivo,
                )
            )
            continue
        diccionario = diccionarios_por_servicio[fila.servicio]
        resultado = homologar_concepto(fila.descripcion, diccionario, umbral=umbral)
        cambios.append(
            CambioHomologacion(
                hash_pdf=fila.hash_pdf,
                orden=fila.orden,
                descripcion=fila.descripcion,
                servicio=fila.servicio,
                concepto_antes=fila.concepto_actual,
                score_antes=fila.score_actual,
                concepto_despues=resultado.concepto,
                score_despues=resultado.score,
                candidatos_empatados=resultado.candidatos_empatados,
            )
        )
    return cambios


def leer_filas_a_rehomologar(
    con: duckdb.DuckDBPyConnection, *, servicio: str | None = None
) -> list[FilaARehomologar]:
    """Lee las filas de `conceptos` de facturas APROBADAS, joineando
    `facturas` para saber el `servicio` de cada una -- necesario para
    acotar el diccionario a usar (docs/auditoria-2026-09.md, hallazgo A-3:
    homologar sin acotar por servicio hace competir conceptos de servicios
    distintos entre sí). Solo `estado = 'aprobada'`
    (docs/auditoria-2026-09-piloto.md, A-55): re-homologar reescribe
    `concepto_normalizado`, y hacerlo sobre una factura rechazada tocaría
    datos que el análisis ya ignora, sin ningún beneficio."""
    condicion = "AND f.servicio = ?" if servicio is not None else ""
    parametros = [servicio] if servicio is not None else []
    filas = con.execute(
        f"""SELECT c.hash_pdf, c.orden, c.descripcion, f.servicio,
                   c.concepto_normalizado, c.score_homologacion
            FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
            WHERE f.estado = 'aprobada' AND f.moneda = 'ARS' {condicion}
            ORDER BY f.servicio, c.hash_pdf, c.orden""",
        parametros,
    ).fetchall()
    return [
        FilaARehomologar(
            hash_pdf=hash_pdf,
            orden=orden,
            descripcion=descripcion,
            servicio=servicio_fila,
            concepto_actual=concepto_actual,
            score_actual=score_actual,
        )
        for hash_pdf, orden, descripcion, servicio_fila, concepto_actual, score_actual in filas
    ]


def aplicar_cambios(con: duckdb.DuckDBPyConnection, cambios: list[CambioHomologacion]) -> int:
    """Aplica solo cambios semánticos, en una única transacción -- ni los
    `sin_cambio` ni los `omitido` (fila sin servicio o sin diccionario, ver
    `recalcular`) se escriben."""
    tocados = [c for c in cambios if c.tipo not in ("sin_cambio", "omitido")]
    if not tocados:
        return 0
    modificadas = 0
    with transaccion(con):
        for c in tocados:
            actual = con.execute(
                "SELECT c.concepto_normalizado, c.score_homologacion "
                "FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf "
                "WHERE c.hash_pdf = ? AND c.orden = ? "
                "AND f.estado = 'aprobada' AND f.moneda = 'ARS'",
                [c.hash_pdf, c.orden],
            ).fetchone()
            if actual is None:
                raise ValueError("La factura de la previsualización ya no está aprobada en ARS.")
            if actual[0] == c.concepto_despues and actual[1] == c.score_despues:
                continue
            if actual != (c.concepto_antes, c.score_antes):
                raise ValueError("La homologación cambió después de la previsualización.")
            motivo = "empate" if c.candidatos_empatados else "recalculado"
            filas = con.execute(
                "UPDATE conceptos SET concepto_normalizado = ?, score_homologacion = ?, "
                "motivo_homologacion = ?, candidatos_empatados = ? "
                "WHERE hash_pdf = ? AND orden = ? "
                "AND concepto_normalizado IS NOT DISTINCT FROM ? "
                "AND score_homologacion IS NOT DISTINCT FROM ? "
                "RETURNING hash_pdf",
                [
                    c.concepto_despues,
                    c.score_despues,
                    motivo,
                    json.dumps(c.candidatos_empatados, ensure_ascii=False),
                    c.hash_pdf,
                    c.orden,
                    c.concepto_antes,
                    c.score_antes,
                ],
            ).fetchall()
            if len(filas) != 1:
                raise ValueError(
                    "La homologación cambió durante la aplicación; se revierte el lote."
                )
            modificadas += 1
    return modificadas
