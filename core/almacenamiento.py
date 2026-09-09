"""Persistencia en DuckDB de las facturas ya validadas y de la cola de
cuarentena (facturas que no pasaron `core/extraccion/validacion.py`).

Tablas:
- `facturas` / `conceptos` / `recargos`: una fila por comprobante, una por
  línea y una por recargo (mora, interés, refacturación), IDEMPOTENTE por
  `hash_pdf` (reprocesar la misma carpeta no duplica nada).
- `cuarentena`: facturas que no cerraron aritméticamente, con el detalle de
  qué control falló, para resolver a mano desde el tablero.

La base vive en `data/reales/facturas.duckdb` -- carpeta excluida de git
(ver CLAUDE.md, zona restringida). No se crea a nivel de módulo: cada
función abre y cierra su propia conexión, así un test puede apuntar a un
archivo temporal sin tocar la base real.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from core.extraccion.esquema import FacturaExtraida
from core.extraccion.validacion import ResultadoValidacion

RUTA_BASE = Path(__file__).resolve().parent.parent / "data" / "reales" / "facturas.duckdb"

_DDL = """
CREATE TABLE IF NOT EXISTS facturas (
    hash_pdf VARCHAR PRIMARY KEY,
    ruta_pdf VARCHAR,
    emisor VARCHAR,
    cuit VARCHAR,
    servicio VARCHAR,
    periodo_desde VARCHAR,
    periodo_hasta VARCHAR,
    fecha_emision VARCHAR,
    numero_comprobante VARCHAR,
    subtotal DOUBLE,
    total DOUBLE,
);
CREATE TABLE IF NOT EXISTS conceptos (
    hash_pdf VARCHAR,
    orden INTEGER,
    descripcion VARCHAR,
    concepto_normalizado VARCHAR,
    cantidad DOUBLE,
    unidad VARCHAR,
    precio_unitario DOUBLE,
    importe DOUBLE,
);
CREATE TABLE IF NOT EXISTS recargos (
    hash_pdf VARCHAR,
    nombre VARCHAR,
    importe DOUBLE,
);
CREATE TABLE IF NOT EXISTS cuarentena (
    hash_pdf VARCHAR PRIMARY KEY,
    ruta_pdf VARCHAR,
    motivos VARCHAR,
);
"""


def conectar(ruta: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Abre (creando si hace falta) la base de facturas y asegura el schema."""
    ruta = ruta or RUTA_BASE
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(ruta))
    con.execute(_DDL)
    return con


def factura_ya_procesada(con: duckdb.DuckDBPyConnection, hash_pdf: str) -> bool:
    """Idempotencia: True si ese PDF (por hash) ya está en `facturas` o `cuarentena`."""
    en_facturas = con.execute("SELECT 1 FROM facturas WHERE hash_pdf = ?", [hash_pdf]).fetchone()
    en_cuarentena = con.execute(
        "SELECT 1 FROM cuarentena WHERE hash_pdf = ?", [hash_pdf]
    ).fetchone()
    return en_facturas is not None or en_cuarentena is not None


def guardar_factura(
    con: duckdb.DuckDBPyConnection,
    factura: FacturaExtraida,
    *,
    conceptos_normalizados: dict[int, str] | None = None,
) -> None:
    """Guarda una factura YA VALIDADA (ver validar_factura) y sus conceptos.
    No hace ningún control aritmético acá -- eso ya pasó antes, este módulo
    solo persiste."""
    conceptos_normalizados = conceptos_normalizados or {}
    con.execute(
        """INSERT OR REPLACE INTO facturas
           (hash_pdf, ruta_pdf, emisor, cuit, servicio, periodo_desde, periodo_hasta,
            fecha_emision, numero_comprobante, subtotal, total)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            factura.hash_pdf,
            factura.ruta_pdf,
            factura.emisor,
            factura.cuit,
            factura.servicio,
            factura.periodo_desde,
            factura.periodo_hasta,
            factura.fecha_emision,
            factura.numero_comprobante,
            factura.subtotal,
            factura.total,
        ],
    )
    con.execute("DELETE FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf])
    for i, c in enumerate(factura.conceptos):
        con.execute(
            """INSERT INTO conceptos
               (hash_pdf, orden, descripcion, concepto_normalizado, cantidad, unidad,
                precio_unitario, importe)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                factura.hash_pdf,
                i,
                c.descripcion,
                conceptos_normalizados.get(i),
                c.cantidad,
                c.unidad,
                c.precio_unitario,
                c.importe,
            ],
        )

    con.execute("DELETE FROM recargos WHERE hash_pdf = ?", [factura.hash_pdf])
    for r in factura.recargos:
        con.execute(
            "INSERT INTO recargos (hash_pdf, nombre, importe) VALUES (?, ?, ?)",
            [factura.hash_pdf, r.nombre, r.importe],
        )


def recargos_del_periodo(
    con: duckdb.DuckDBPyConnection, *, servicio: str, periodo_desde: str
) -> list[tuple[str, float]]:
    """(nombre, importe) de todos los recargos de las facturas de `servicio`
    en `periodo_desde` -- lo que usa la página de evolución para armar la
    alerta de recargos sobre el período comparado."""
    filas = con.execute(
        """SELECT r.nombre, r.importe FROM recargos r
           JOIN facturas f ON f.hash_pdf = r.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ?""",
        [servicio, periodo_desde],
    ).fetchall()
    return [(nombre, importe) for nombre, importe in filas]


def guardar_en_cuarentena(
    con: duckdb.DuckDBPyConnection,
    *,
    hash_pdf: str,
    ruta_pdf: str,
    resultado: ResultadoValidacion,
) -> None:
    """Guarda una factura que NO pasó la validación aritmética, con el
    detalle de qué falló -- CLAUDE.md: nunca entra al análisis."""
    motivos = "; ".join(resultado.motivos_de_falla())
    con.execute(
        "INSERT OR REPLACE INTO cuarentena (hash_pdf, ruta_pdf, motivos) VALUES (?, ?, ?)",
        [hash_pdf, ruta_pdf, motivos],
    )
