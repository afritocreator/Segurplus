"""Persistencia en DuckDB de las facturas ya validadas y de la cola de
cuarentena (facturas que no pasaron `core/extraccion/validacion.py`).

Tablas:
- `facturas` / `conceptos` / `recargos`: una fila por comprobante, una por
  línea y una por recargo (mora, interés, refacturación), IDEMPOTENTE por
  `hash_pdf` (reprocesar la misma carpeta no duplica nada).
- `alertas`: alertas que se calculan UNA VEZ, en el momento de cargar la
  factura (hoy solo `alertas_por_item_duplicado`, que necesita los
  conceptos de una factura individual -- ver `core.pipeline.procesar_pdf`).
  El resto de las alertas (recargos, concepto nuevo, salto de cantidad,
  precio sobre IPC) se siguen calculando al vuelo en la página de
  evolución, porque dependen de comparar DOS períodos, no de una factura
  sola.
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

from core.analisis.alertas import Alerta
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
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT now();
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
CREATE TABLE IF NOT EXISTS alertas (
    hash_pdf VARCHAR,
    tipo VARCHAR,
    severidad VARCHAR,
    mensaje VARCHAR,
    concepto VARCHAR,
);
CREATE TABLE IF NOT EXISTS cuarentena (
    hash_pdf VARCHAR PRIMARY KEY,
    ruta_pdf VARCHAR,
    motivos VARCHAR,
);
ALTER TABLE cuarentena ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT now();
"""


def conectar(ruta: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Abre (creando si hace falta) la base de facturas y asegura el schema."""
    ruta = ruta or RUTA_BASE
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(ruta))
    con.execute(_DDL)
    return con


def llamadas_ultima_hora(con: duckdb.DuckDBPyConnection) -> int:
    """Cuenta cuántos PDFs se procesaron (guardados o mandados a cuarentena)
    en la última hora -- proxy de cuántas llamadas a Gemini se hicieron, para
    hacer cumplir `core.extraccion.gemini.MAX_LLAMADAS_POR_HORA` (docs/
    auditoria-2026-09.md, hallazgo A-7: la constante estaba declarada y
    nunca se usaba). No cuenta una llamada que falló ANTES de persistir nada
    (ej. `ExtraccionError` de la propia API) -- subestima un poco el conteo
    real, pero alcanza como freno simple contra un loop o un mal uso del
    tablero, no pretende ser un contador exacto de facturación de la API."""
    fila = con.execute(
        """SELECT
             (SELECT count(*) FROM facturas WHERE creado_en > now() - INTERVAL '1 hour') +
             (SELECT count(*) FROM cuarentena WHERE creado_en > now() - INTERVAL '1 hour')"""
    ).fetchone()
    return fila[0]


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


def guardar_alertas(con: duckdb.DuckDBPyConnection, hash_pdf: str, alertas: list[Alerta]) -> None:
    """Guarda las alertas calculadas UNA VEZ para una factura individual
    (hoy solo item_duplicado -- ver `core.pipeline.procesar_pdf`).
    Idempotente: si se reprocesa el mismo hash, primero se borran las
    alertas viejas de ese hash."""
    con.execute("DELETE FROM alertas WHERE hash_pdf = ?", [hash_pdf])
    for a in alertas:
        con.execute(
            "INSERT INTO alertas (hash_pdf, tipo, severidad, mensaje, concepto) "
            "VALUES (?, ?, ?, ?, ?)",
            [hash_pdf, a.tipo, a.severidad, a.mensaje, a.concepto],
        )


def alertas_del_periodo(
    con: duckdb.DuckDBPyConnection, *, servicio: str, periodo_desde: str
) -> list[Alerta]:
    """Alertas persistidas (por factura individual) de todas las facturas de
    `servicio` en `periodo_desde` -- lo que usa la página de evolución para
    combinarlas con las que se calculan al vuelo comparando dos períodos."""
    filas = con.execute(
        """SELECT a.tipo, a.severidad, a.mensaje, a.concepto FROM alertas a
           JOIN facturas f ON f.hash_pdf = a.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ?""",
        [servicio, periodo_desde],
    ).fetchall()
    return [
        Alerta(tipo=tipo, severidad=severidad, mensaje=mensaje, concepto=concepto)
        for tipo, severidad, mensaje, concepto in filas
    ]


def borrar_de_cuarentena(con: duckdb.DuckDBPyConnection, hash_pdf: str) -> None:
    """Saca una factura de la cola de cuarentena (docs/auditoria-2026-09.md,
    hallazgo A-17: "reintentar" desde el tablero). No borra nada de
    `facturas` porque una factura en cuarentena nunca llegó a esa tabla --
    esto solo libera el hash para que `factura_ya_procesada` deje de
    bloquear un reintento tras corregir el problema (ej. ajustar el prompt
    de extracción, o resubir un PDF distinto del mismo período)."""
    con.execute("DELETE FROM cuarentena WHERE hash_pdf = ?", [hash_pdf])


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
