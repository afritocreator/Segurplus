"""Recalcula la homologación de las filas YA GUARDADAS en `conceptos`, con
el diccionario ACTUAL de `data/conceptos/*.yaml` -- sin llamar a Gemini ni
re-extraer nada.

Por qué existe: editar `data/conceptos/*.yaml` (agregar un alias, después
de mirar la pantalla de "Conceptos sin clasificar") solo cambia cómo
homologa una factura NUEVA que se procese después -- las que ya están en
`data/reales/facturas.duckdb` quedan con el `concepto_normalizado` viejo.
Sin este módulo, cada ajuste del diccionario obligaría a re-extraer con
Gemini (gastando la cuota de `MAX_LLAMADAS_POR_HORA`,
`core/extraccion/gemini.py`) solo para que la homologación se vuelva a
correr -- un desperdicio, porque el texto de la descripción ya está
guardado y no cambió. Este módulo NUNCA importa `core.extraccion.gemini`
ni nada que llame a la red -- ver `tests/test_rehomologacion.py`.

Cáscara: `recalcular()` es puro, testeable sin DuckDB (recibe el
diccionario ya cargado). `leer_filas_a_rehomologar` y `aplicar_cambios`
son los únicos que tocan la conexión -- ver `scripts/rehomologar.py` para
el CLI que los orquesta.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from core.analisis.homologacion import homologar_concepto


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

    @property
    def tipo(self) -> str:
        """ "nuevo": no homologaba, ahora sí (un alias nuevo lo captó).
        "regresion": homologaba, ahora no -- posible señal de que un alias
        nuevo le robó el match a este concepto (ver el aviso en el
        docstring de `recalcular`). "cambio": homologaba a un concepto,
        ahora homologa a otro. "sin_cambio": el concepto no varió (el
        score puede haber cambiado igual, ej. por un alias nuevo del mismo
        concepto)."""
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
) -> list[CambioHomologacion]:
    """Puro -- no toca DuckDB ni el filesystem, los diccionarios ya vienen
    cargados. `diccionarios_por_servicio`: mapea cada `servicio` (o `None`)
    al diccionario ya obtenido con `cargar_diccionario(servicio)` -- se
    carga UNA VEZ por servicio afuera de acá, no una vez por fila (con
    decenas de conceptos por servicio, releer el YAML por cada uno sería
    decenas de lecturas de disco redundantes)."""
    cambios = []
    for fila in filas:
        diccionario = diccionarios_por_servicio.get(fila.servicio, {})
        concepto, score = homologar_concepto(fila.descripcion, diccionario)
        cambios.append(
            CambioHomologacion(
                hash_pdf=fila.hash_pdf,
                orden=fila.orden,
                descripcion=fila.descripcion,
                servicio=fila.servicio,
                concepto_antes=fila.concepto_actual,
                score_antes=fila.score_actual,
                concepto_despues=concepto,
                score_despues=score,
            )
        )
    return cambios


def leer_filas_a_rehomologar(
    con: duckdb.DuckDBPyConnection, *, servicio: str | None = None
) -> list[FilaARehomologar]:
    """Lee todas las filas de `conceptos`, joineando `facturas` para saber
    el `servicio` de cada una -- necesario para acotar el diccionario a
    usar (docs/auditoria-2026-09.md, hallazgo A-3: homologar sin acotar por
    servicio hace competir conceptos de servicios distintos entre sí)."""
    condicion = "WHERE f.servicio = ?" if servicio is not None else ""
    parametros = [servicio] if servicio is not None else []
    filas = con.execute(
        f"""SELECT c.hash_pdf, c.orden, c.descripcion, f.servicio,
                   c.concepto_normalizado, c.score_homologacion
            FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
            {condicion}
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
    """Escribe `concepto_normalizado` y `score_homologacion` para cada
    cambio, identificado por `(hash_pdf, orden)`. Devuelve cuántas filas se
    tocaron. Idempotente: aplicar la misma lista dos veces deja el mismo
    resultado."""
    for c in cambios:
        con.execute(
            "UPDATE conceptos SET concepto_normalizado = ?, score_homologacion = ? "
            "WHERE hash_pdf = ? AND orden = ?",
            [c.concepto_despues, c.score_despues, c.hash_pdf, c.orden],
        )
    return len(cambios)
