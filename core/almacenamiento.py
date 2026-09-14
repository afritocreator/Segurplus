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

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

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
    fecha_vencimiento VARCHAR,
    numero_comprobante VARCHAR,
    moneda VARCHAR,
    subtotal DOUBLE PRECISION,
    total DOUBLE PRECISION,
    estado VARCHAR DEFAULT 'aprobada',
    ruta_evidencia VARCHAR,
    modelo_extraccion VARCHAR,
    version_prompt VARCHAR,
    version_esquema VARCHAR,
    respuesta_extraida VARCHAR
);
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT now();
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMP DEFAULT now();
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS fecha_vencimiento VARCHAR;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS moneda VARCHAR;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS estado VARCHAR DEFAULT 'aprobada';
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS ruta_evidencia VARCHAR;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS modelo_extraccion VARCHAR;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS version_prompt VARCHAR;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS version_esquema VARCHAR;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS respuesta_extraida VARCHAR;
CREATE TABLE IF NOT EXISTS conceptos (
    hash_pdf VARCHAR,
    orden INTEGER,
    descripcion VARCHAR,
    concepto_normalizado VARCHAR,
    cantidad DOUBLE PRECISION,
    unidad VARCHAR,
    precio_unitario DOUBLE PRECISION,
    importe DOUBLE PRECISION
);
ALTER TABLE conceptos ADD COLUMN IF NOT EXISTS score_homologacion DOUBLE PRECISION;
ALTER TABLE conceptos ADD COLUMN IF NOT EXISTS motivo_homologacion VARCHAR;
ALTER TABLE conceptos ADD COLUMN IF NOT EXISTS candidatos_empatados VARCHAR;
CREATE TABLE IF NOT EXISTS recargos (
    hash_pdf VARCHAR,
    nombre VARCHAR,
    importe DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS impuestos (
    hash_pdf VARCHAR,
    nombre VARCHAR,
    importe DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS creditos (
    hash_pdf VARCHAR,
    nombre VARCHAR,
    importe DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS alertas (
    hash_pdf VARCHAR,
    tipo VARCHAR,
    severidad VARCHAR,
    mensaje VARCHAR,
    concepto VARCHAR
);
CREATE TABLE IF NOT EXISTS cuarentena (
    hash_pdf VARCHAR PRIMARY KEY,
    ruta_pdf VARCHAR,
    motivos VARCHAR,
    ruta_evidencia VARCHAR
);
ALTER TABLE cuarentena ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT now();
ALTER TABLE cuarentena ADD COLUMN IF NOT EXISTS ruta_evidencia VARCHAR;
ALTER TABLE cuarentena ADD COLUMN IF NOT EXISTS emisor VARCHAR;
ALTER TABLE cuarentena ADD COLUMN IF NOT EXISTS servicio VARCHAR;
CREATE TABLE IF NOT EXISTS decisiones_factura (
    id VARCHAR,
    hash_pdf VARCHAR,
    accion VARCHAR,
    actor VARCHAR,
    motivo VARCHAR,
    creado_en TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS correcciones_factura (
    id VARCHAR,
    hash_pdf VARCHAR,
    campo VARCHAR,
    valor_anterior VARCHAR,
    valor_nuevo VARCHAR,
    motivo VARCHAR,
    actor VARCHAR,
    creado_en TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS casos_alerta (
    clave VARCHAR PRIMARY KEY,
    hash_pdf VARCHAR,
    tipo VARCHAR,
    severidad VARCHAR,
    mensaje VARCHAR,
    concepto VARCHAR,
    estado VARCHAR DEFAULT 'abierto',
    responsable VARCHAR,
    vencimiento VARCHAR,
    evidencia VARCHAR,
    creado_en TIMESTAMP DEFAULT now(),
    actualizado_en TIMESTAMP DEFAULT now()
);
"""

ESTADOS_FACTURA = frozenset(
    {"recibida", "extraida", "requiere_revision", "aprobada", "rechazada", "cuarentena"}
)
ESTADOS_CASO = frozenset({"abierto", "en_analisis", "resuelto", "descartado"})


class ConexionPostgres:
    """Adaptador mínimo para usar la misma capa SQL con PostgreSQL administrado.

    Se activa únicamente con ``DATABASE_URL``. En desarrollo y tests se mantiene
    DuckDB; producción no debe arrancar con esa ruta local como fuente de verdad.
    """

    def __init__(self, url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depende del deploy
            raise RuntimeError("DATABASE_URL requiere instalar psycopg.") from exc
        self._con = psycopg.connect(url, autocommit=True)

    def execute(self, sql: str, params: list[Any] | None = None) -> Any:
        """Reemplazo textual `?`->`%s`, sin parsear el SQL -- RESTRICCIÓN:
        ningún SQL de este módulo puede tener un `?` que no sea un
        placeholder de parámetro (ej. dentro de un literal de texto), ni un
        `%` suelto (ej. un `LIKE 'x%'`, que psycopg interpretaría como el
        inicio de otro placeholder). Vigilado por
        `tests/test_conexion_postgres.py`."""
        return self._con.execute(sql.replace("?", "%s"), params)

    def close(self) -> None:
        self._con.close()


def _ejecutar_ddl(con: duckdb.DuckDBPyConnection | ConexionPostgres) -> None:
    """Ejecuta migraciones aditivas una instrucción por vez."""
    for sentencia in _DDL.split(";"):
        if sentencia.strip():
            con.execute(sentencia)


def conectar(ruta: Path | None = None) -> duckdb.DuckDBPyConnection | ConexionPostgres:
    """Abre la fuente de verdad configurada y asegura migraciones aditivas.

    ``DATABASE_URL`` apunta a PostgreSQL administrado. DuckDB queda como modo
    local explícito para desarrollo, fixtures y análisis sin conexión externa.

    Pasar `ruta` explícitamente SIEMPRE usa DuckDB en esa ruta, ignorando
    `DATABASE_URL` aunque esté seteada -- pedir un archivo puntual significa
    "quiero este archivo", no "salvo que haya una base de producción
    configurada". Antes de esta corrección, `conectar(tmp_path/"x.duckdb")`
    igual se conectaba a Postgres si `DATABASE_URL` estaba en el entorno --
    con esa variable puesta (el caso normal desde que hay un deploy real),
    CUALQUIER test que pasara una ruta de archivo temporal terminaba
    escribiendo en la base de producción (docs/auditoria-2026-09-piloto.md,
    A-49). `tests/conftest.py` además saca `DATABASE_URL` del entorno para
    toda la corrida de tests, porque varios tests (los de páginas con
    AppTest) llaman `conectar()` SIN pasar `ruta` -- ese caso solo lo cubre
    la variable de entorno, no este chequeo."""
    if ruta is not None:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(ruta))
        _ejecutar_ddl(con)
        return con
    url = os.environ.get("DATABASE_URL")
    if url:
        con = ConexionPostgres(url)
        _ejecutar_ddl(con)
        return con
    RUTA_BASE.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(RUTA_BASE))
    _ejecutar_ddl(con)
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


def _id_auditoria(hash_pdf: str, accion: str) -> str:
    return f"{hash_pdf}:{accion}:{datetime.now().isoformat(timespec='microseconds')}"


def _registrar_decision(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
    hash_pdf: str,
    accion: str,
    actor: str,
    motivo: str | None = None,
) -> None:
    """Escribe un evento de auditoría; no se edita ni se borra al revisar."""
    con.execute(
        "INSERT INTO decisiones_factura (id, hash_pdf, accion, actor, motivo) "
        "VALUES (?, ?, ?, ?, ?)",
        [_id_auditoria(hash_pdf, accion), hash_pdf, accion, actor, motivo],
    )


_ESTADOS_DECIDIBLES = frozenset({"requiere_revision", "aprobada"})


def listar_facturas_pendientes(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
) -> list[tuple[str, str | None, str | None, str | None, float | None, str | None]]:
    """Facturas que requieren decisión humana, ordenadas por antigüedad."""
    return con.execute(
        """SELECT hash_pdf, emisor, servicio, periodo_desde, total, ruta_evidencia
           FROM facturas WHERE estado = 'requiere_revision' ORDER BY creado_en"""
    ).fetchall()


def listar_facturas_aprobadas(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
) -> list[tuple[str, str | None, str | None, str | None, float | None, str | None]]:
    """Facturas ya aprobadas, para poder corregirlas o rechazarlas después de
    la carga (docs/auditoria-2026-09-piloto.md, A-50) -- con
    `revision_humana_obligatoria` en false (el default), TODA factura nace
    aprobada directo, así que esta es la única lista desde la que se puede
    corregir una cabecera mal leída o sacar del análisis una factura que
    resultó estar mal. Mismos campos que `listar_facturas_pendientes`."""
    return con.execute(
        """SELECT hash_pdf, emisor, servicio, periodo_desde, total, ruta_evidencia
           FROM facturas WHERE estado = 'aprobada' ORDER BY creado_en DESC"""
    ).fetchall()


def decision_factura(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
    *,
    hash_pdf: str,
    estado: str,
    actor: str,
    motivo: str,
) -> None:
    """Aprueba o rechaza una factura, desde `requiere_revision` (el flujo de
    revisión normal) o desde `aprobada` (corregir el rumbo después de la
    carga -- docs/auditoria-2026-09-piloto.md, A-50: antes de esto, con
    `revision_humana_obligatoria` en false, ninguna factura llegaba nunca a
    `requiere_revision`, así que rechazar una factura mal leída era
    imposible). Rechazar una factura que estaba aprobada borra sus casos
    operativos (`casos_alerta`): una factura que sale del análisis no debe
    dejar trabajo operativo colgado sobre alertas que ya no cuentan."""
    if estado not in {"aprobada", "rechazada"}:
        raise ValueError("Una decisión solo puede aprobar o rechazar una factura.")
    fila = con.execute("SELECT estado FROM facturas WHERE hash_pdf = ?", [hash_pdf]).fetchone()
    if fila is None:
        raise ValueError("No existe la factura a decidir.")
    if fila[0] not in _ESTADOS_DECIDIBLES:
        raise ValueError("Solo se pueden decidir facturas pendientes o ya aprobadas.")
    con.execute(
        "UPDATE facturas SET estado = ?, actualizado_en = now() WHERE hash_pdf = ?",
        [estado, hash_pdf],
    )
    _registrar_decision(con, hash_pdf, estado, actor, motivo)
    if estado == "aprobada":
        sincronizar_casos_de_factura(con, hash_pdf)
    elif estado == "rechazada":
        con.execute("DELETE FROM casos_alerta WHERE hash_pdf = ?", [hash_pdf])


def aprobar_pendientes(
    con: duckdb.DuckDBPyConnection | ConexionPostgres, *, actor: str, motivo: str
) -> int:
    """Aprueba TODAS las facturas en `requiere_revision`, con el mismo motivo
    para el lote -- para cuando la revisión de a una (`decision_factura`) es
    más fricción de la que el piloto necesita (una o dos personas cargando y
    revisando su propia carga, ver `data/operacion.yaml`).

    Reusa `decision_factura` fila por fila, así que cada aprobación deja el
    mismo rastro de auditoría (actor, motivo, momento) que si se hubiera
    hecho a mano una por una -- no se pierde trazabilidad por aprobar en
    lote. No corre dentro de una única transacción de base (tampoco lo hace
    el resto de este módulo): si el proceso se interrumpe a mitad, algunas
    facturas quedan aprobadas y otras siguen pendientes, exactamente como si
    se hubiera interrumpido una revisión manual a mitad de camino -- no es
    un estado peor que ese.

    Devuelve cuántas facturas se aprobaron."""
    pendientes = listar_facturas_pendientes(con)
    for hash_pdf, *_resto in pendientes:
        decision_factura(con, hash_pdf=hash_pdf, estado="aprobada", actor=actor, motivo=motivo)
    return len(pendientes)


def registrar_correccion(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
    *,
    hash_pdf: str,
    campo: str,
    valor_nuevo: str | None,
    motivo: str,
    actor: str,
) -> None:
    """Corrige una cabecera de una factura pendiente o ya aprobada (ver
    `decision_factura` y A-50 -- una `rechazada` no es corregible: si el
    dato estaba mal y se quiere reintentar, se vuelve a subir el PDF, ver
    A-56), dejando valor anterior y evidencia."""
    permitidos = {
        "emisor",
        "cuit",
        "servicio",
        "periodo_desde",
        "periodo_hasta",
        "fecha_emision",
        "fecha_vencimiento",
        "numero_comprobante",
        "moneda",
    }
    if campo not in permitidos:
        raise ValueError(f"Campo no editable en revisión: {campo}")
    fila = con.execute(
        f"SELECT {campo}, estado FROM facturas WHERE hash_pdf = ?", [hash_pdf]
    ).fetchone()
    if fila is None or fila[1] not in _ESTADOS_DECIDIBLES:
        raise ValueError("La factura no está disponible para corrección.")
    con.execute(
        f"UPDATE facturas SET {campo} = ?, actualizado_en = now() WHERE hash_pdf = ?",
        [valor_nuevo, hash_pdf],
    )
    con.execute(
        """INSERT INTO correcciones_factura
           (id, hash_pdf, campo, valor_anterior, valor_nuevo, motivo, actor)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            _id_auditoria(hash_pdf, "correccion"),
            hash_pdf,
            campo,
            fila[0],
            valor_nuevo,
            motivo,
            actor,
        ],
    )
    _registrar_decision(con, hash_pdf, "correccion", actor, motivo)


def resumen_financiero_factura(
    con: duckdb.DuckDBPyConnection | ConexionPostgres, hash_pdf: str
) -> dict[str, float]:
    """Componentes del total de una factura para revisión y reporte."""
    consultas = {
        "consumos": "SELECT coalesce(sum(importe), 0) FROM conceptos WHERE hash_pdf = ?",
        "impuestos": "SELECT coalesce(sum(importe), 0) FROM impuestos WHERE hash_pdf = ?",
        "recargos": "SELECT coalesce(sum(importe), 0) FROM recargos WHERE hash_pdf = ?",
        "creditos": "SELECT coalesce(sum(importe), 0) FROM creditos WHERE hash_pdf = ?",
    }
    resultado = {
        clave: float(con.execute(sql, [hash_pdf]).fetchone()[0]) for clave, sql in consultas.items()
    }
    resultado["total_pagable"] = (
        resultado["consumos"]
        + resultado["impuestos"]
        + resultado["recargos"]
        - resultado["creditos"]
    )
    return resultado


def componentes_financieros_periodo(
    con: duckdb.DuckDBPyConnection | ConexionPostgres, *, servicio: str, periodo_desde: str
) -> dict[str, float]:
    """Separa el total pagable aprobado sin duplicar joins de líneas.

    ``total = consumos + impuestos + recargos - créditos``. Cada componente
    se consulta de forma independiente porque unir varias tablas de detalle
    multiplicaría importes cuando una factura tiene más de una línea.
    """
    tablas = {
        "consumos": "conceptos",
        "impuestos": "impuestos",
        "recargos": "recargos",
        "creditos": "creditos",
    }
    resultado: dict[str, float] = {}
    for clave, tabla in tablas.items():
        resultado[clave] = float(
            con.execute(
                f"""SELECT coalesce(sum(x.importe), 0) FROM {tabla} x
                   JOIN facturas f ON f.hash_pdf = x.hash_pdf
                   WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
                [servicio, periodo_desde],
            ).fetchone()[0]
        )
    resultado["total_pagable"] = (
        resultado["consumos"]
        + resultado["impuestos"]
        + resultado["recargos"]
        - resultado["creditos"]
    )
    return resultado


def guardar_factura(
    con: duckdb.DuckDBPyConnection,
    factura: FacturaExtraida,
    *,
    conceptos_normalizados: dict[int, str] | None = None,
    scores_homologacion: dict[int, float] | None = None,
    motivos_homologacion: dict[int, str] | None = None,
    candidatos_empatados: dict[int, str] | None = None,
    estado: str = "aprobada",
    actor: str = "sistema",
) -> None:
    """Guarda una factura YA VALIDADA (ver validar_factura) y sus conceptos.
    No hace ningún control aritmético acá -- eso ya pasó antes, este módulo
    solo persiste.

    `scores_homologacion`: el score de similitud que dio `homologar_concepto`
    para cada línea (índice `i`), HAYA homologado o no. El score de las que
    NO homologaron es el dato valioso para calibrar: dice si falta un alias
    (score cerca del umbral, ej. 0.55) o si es un concepto genuinamente
    nuevo (score bajo, ej. 0.12) -- ver `core/rehomologacion.py` y
    `apps/segurplus/paginas/sin_clasificar.py`. Antes de esto el score se
    calculaba y se descartaba en `core/pipeline.py`."""
    if estado not in ESTADOS_FACTURA:
        raise ValueError(f"Estado de factura inválido: {estado}")
    conceptos_normalizados = conceptos_normalizados or {}
    scores_homologacion = scores_homologacion or {}
    motivos_homologacion = motivos_homologacion or {}
    candidatos_empatados = candidatos_empatados or {}
    con.execute(
        """INSERT INTO facturas
           (hash_pdf, ruta_pdf, emisor, cuit, servicio, periodo_desde, periodo_hasta,
            fecha_emision, fecha_vencimiento, numero_comprobante, moneda, subtotal, total,
            estado, ruta_evidencia, modelo_extraccion, version_prompt, version_esquema,
            respuesta_extraida, actualizado_en)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
           ON CONFLICT (hash_pdf) DO UPDATE SET
             ruta_pdf = excluded.ruta_pdf, emisor = excluded.emisor, cuit = excluded.cuit,
             servicio = excluded.servicio, periodo_desde = excluded.periodo_desde,
             periodo_hasta = excluded.periodo_hasta, fecha_emision = excluded.fecha_emision,
             fecha_vencimiento = excluded.fecha_vencimiento,
             numero_comprobante = excluded.numero_comprobante, moneda = excluded.moneda,
             subtotal = excluded.subtotal, total = excluded.total, estado = excluded.estado,
             ruta_evidencia = excluded.ruta_evidencia,
             modelo_extraccion = excluded.modelo_extraccion,
             version_prompt = excluded.version_prompt, version_esquema = excluded.version_esquema,
             respuesta_extraida = excluded.respuesta_extraida, actualizado_en = now()""",
        [
            factura.hash_pdf,
            factura.ruta_pdf,
            factura.emisor,
            factura.cuit,
            factura.servicio,
            factura.periodo_desde,
            factura.periodo_hasta,
            factura.fecha_emision,
            factura.fecha_vencimiento,
            factura.numero_comprobante,
            factura.moneda,
            factura.subtotal,
            factura.total,
            estado,
            factura.ruta_evidencia,
            factura.modelo_extraccion,
            factura.version_prompt,
            factura.version_esquema,
            factura.respuesta_extraida,
        ],
    )
    con.execute("DELETE FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf])
    for i, c in enumerate(factura.conceptos):
        con.execute(
            """INSERT INTO conceptos
               (hash_pdf, orden, descripcion, concepto_normalizado, cantidad, unidad,
                precio_unitario, importe, score_homologacion, motivo_homologacion,
                candidatos_empatados)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                factura.hash_pdf,
                i,
                c.descripcion,
                conceptos_normalizados.get(i),
                c.cantidad,
                c.unidad,
                c.precio_unitario,
                c.importe,
                scores_homologacion.get(i),
                motivos_homologacion.get(i),
                candidatos_empatados.get(i),
            ],
        )

    con.execute("DELETE FROM recargos WHERE hash_pdf = ?", [factura.hash_pdf])
    for r in factura.recargos:
        con.execute(
            "INSERT INTO recargos (hash_pdf, nombre, importe) VALUES (?, ?, ?)",
            [factura.hash_pdf, r.nombre, r.importe],
        )
    con.execute("DELETE FROM impuestos WHERE hash_pdf = ?", [factura.hash_pdf])
    for impuesto in factura.impuestos:
        con.execute(
            "INSERT INTO impuestos (hash_pdf, nombre, importe) VALUES (?, ?, ?)",
            [factura.hash_pdf, impuesto.nombre, impuesto.importe],
        )
    con.execute("DELETE FROM creditos WHERE hash_pdf = ?", [factura.hash_pdf])
    for credito in factura.creditos:
        con.execute(
            "INSERT INTO creditos (hash_pdf, nombre, importe) VALUES (?, ?, ?)",
            [factura.hash_pdf, credito.nombre, credito.importe],
        )
    _registrar_decision(con, factura.hash_pdf, "carga", actor, f"estado inicial: {estado}")


def conceptos_sin_clasificar(
    con: duckdb.DuckDBPyConnection, *, servicio: str | None = None
) -> list[tuple[str, str, float | None, float, int, str]]:
    """`(servicio, descripcion, score_maximo, importe_total, veces, ultimo_periodo)`
    de los conceptos con `concepto_normalizado IS NULL`, agrupados por
    `(servicio, descripcion)` y ORDENADOS POR IMPORTE TOTAL DESCENDENTE --
    la plata manda: el alias que más conviene agregar a
    `data/conceptos/*.yaml` es el que más plata deja sin clasificar (ver
    `apps/segurplus/paginas/sin_clasificar.py` y `core/rehomologacion.py`,
    el circuito de calibración de docs/auditoria-2026-09.md, Bloque 9)."""
    condicion = "AND f.servicio = ?" if servicio is not None else ""
    parametros = [servicio] if servicio is not None else []
    filas = con.execute(
        f"""SELECT f.servicio, c.descripcion, max(c.score_homologacion),
                   sum(c.importe), count(*), max(f.periodo_desde)
            FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
            WHERE c.concepto_normalizado IS NULL {condicion}
            GROUP BY f.servicio, c.descripcion
            ORDER BY sum(c.importe) DESC""",
        parametros,
    ).fetchall()
    # NULL significa "no se midió" en una carga anterior, no score cero.
    return filas


def filas_sin_clasificar_por_periodo(
    con: duckdb.DuckDBPyConnection,
) -> list[tuple[str | None, str, float | None, float, str]]:
    """Filas sin clasificar sin mezclar períodos; la calibración las deflacta después."""
    return con.execute(
        """SELECT f.servicio, c.descripcion, c.score_homologacion, c.importe, f.periodo_desde
           FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE c.concepto_normalizado IS NULL AND f.periodo_desde IS NOT NULL"""
    ).fetchall()


def metricas_por_proveedor(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
) -> list[tuple[str, int, int, int, int, float]]:
    """Cifras crudas por `emisor` (nunca proporciones -- eso es cálculo,
    va a `core.analisis.calibracion.MetricasProveedor`, con test):
    `(emisor, facturas_cargadas, facturas_en_cuarentena, conceptos_totales,
    conceptos_sin_homologar, importe_sin_homologar)`.

    Sirve para responder "¿la herramienta está leyendo bien a ESTE
    proveedor?" -- una tasa alta de cuarentena o de conceptos sin
    homologar en un emisor puntual señala dónde ajustar el prompt de
    extracción o `data/conceptos/*.yaml`, en vez de mirar el agregado de
    todos los proveedores mezclados. `emisor IS NULL` se agrupa aparte
    (una extracción que no pudo leer el emisor sigue siendo información).

    Cuatro consultas simples combinadas en Python en vez de un único JOIN
    con FULL OUTER: mezclar `facturas` y `cuarentena` (que no comparten
    columnas de conceptos) en un solo JOIN sería más difícil de leer que
    combinar los conteos ya agregados."""
    facturas_por_emisor = dict(
        con.execute("SELECT emisor, count(*) FROM facturas GROUP BY emisor").fetchall()
    )
    cuarentena_por_emisor = dict(
        con.execute("SELECT emisor, count(*) FROM cuarentena GROUP BY emisor").fetchall()
    )
    conceptos_por_emisor = dict(
        con.execute(
            """SELECT f.emisor, count(*)
               FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
               GROUP BY f.emisor"""
        ).fetchall()
    )
    sin_homologar_por_emisor = dict(
        con.execute(
            """SELECT f.emisor, count(*)
               FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
               WHERE c.concepto_normalizado IS NULL
               GROUP BY f.emisor"""
        ).fetchall()
    )
    importe_sin_homologar_por_emisor = dict(
        con.execute(
            """SELECT f.emisor, sum(c.importe)
               FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
               WHERE c.concepto_normalizado IS NULL
               GROUP BY f.emisor"""
        ).fetchall()
    )

    emisores = (
        set(facturas_por_emisor)
        | set(cuarentena_por_emisor)
        | set(conceptos_por_emisor)
        | set(sin_homologar_por_emisor)
    )
    return [
        (
            emisor if emisor is not None else "(sin emisor)",
            facturas_por_emisor.get(emisor, 0),
            cuarentena_por_emisor.get(emisor, 0),
            conceptos_por_emisor.get(emisor, 0),
            sin_homologar_por_emisor.get(emisor, 0),
            importe_sin_homologar_por_emisor.get(emisor, 0.0) or 0.0,
        )
        for emisor in sorted(emisores, key=lambda e: e or "")
    ]


def motivos_cuarentena_por_proveedor(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
) -> list[tuple[str, str, int]]:
    """`(emisor, motivo, veces)` -- por qué fue a cuarentena cada proveedor,
    no solo cuántas veces. `motivos` guarda varios motivos separados por
    "; " en una sola factura (ver `guardar_en_cuarentena`), así que cada
    motivo individual se cuenta por separado."""
    filas = con.execute(
        "SELECT coalesce(emisor, '(sin emisor)'), motivos FROM cuarentena"
    ).fetchall()
    conteo: dict[tuple[str, str], int] = {}
    for emisor, motivos in filas:
        for motivo in (motivos or "").split("; "):
            motivo = motivo.strip()
            if motivo:
                clave = (emisor, motivo)
                conteo[clave] = conteo.get(clave, 0) + 1
    return [(emisor, motivo, veces) for (emisor, motivo), veces in sorted(conteo.items())]


def importes_por_periodo(con: duckdb.DuckDBPyConnection) -> list[tuple[float, str]]:
    """Todos los importes de conceptos con su período, para totales comparables."""
    return con.execute(
        """SELECT c.importe, f.periodo_desde FROM conceptos c
           JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE f.periodo_desde IS NOT NULL"""
    ).fetchall()


def totales_por_periodo(con: duckdb.DuckDBPyConnection, *, servicio: str) -> dict[str, float]:
    """`{periodo_desde: total}` de TODOS los períodos cargados de
    `servicio` -- lo que usa `apps/segurplus/paginas/evolucion.py` para la
    serie temporal (`core.analisis.serie`).

    Suma `conceptos.importe`, NO `facturas.total` -- a propósito: `total`
    incluye impuestos y recargos, y la comparación de dos períodos que ya
    existe en esta misma página (`agregar_conceptos` + `descomponer_
    conceptos`) también trabaja solo sobre `conceptos`. Sumar `facturas.
    total` acá haría que la serie y la comparación de dos puntos muestren
    números distintos para el mismo período -- confuso e innecesario."""
    filas = con.execute(
        """SELECT f.periodo_desde, sum(c.importe)
           FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde IS NOT NULL AND f.estado = 'aprobada'
           GROUP BY f.periodo_desde""",
        [servicio],
    ).fetchall()
    return {periodo: total for periodo, total in filas}


def recargos_del_periodo(
    con: duckdb.DuckDBPyConnection, *, servicio: str, periodo_desde: str
) -> list[tuple[str, float]]:
    """(nombre, importe) de todos los recargos de las facturas de `servicio`
    en `periodo_desde` -- lo que usa la página de evolución para armar la
    alerta de recargos sobre el período comparado."""
    filas = con.execute(
        """SELECT r.nombre, r.importe FROM recargos r
           JOIN facturas f ON f.hash_pdf = r.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
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


def _clave_caso(hash_pdf: str, alerta: Alerta) -> str:
    base = "|".join([hash_pdf, alerta.tipo, alerta.concepto or "", alerta.mensaje])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def sincronizar_casos_de_factura(
    con: duckdb.DuckDBPyConnection | ConexionPostgres, hash_pdf: str
) -> None:
    """Crea casos para las alertas de UNA factura -- llamar solo cuando esa
    factura ya está aprobada (`decision_factura` lo hace al aprobar de a una
    o en lote; `core.pipeline.procesar_pdf` lo hace también cuando
    `data/operacion.yaml::revision_humana_obligatoria` está en false, porque
    ahí la factura queda aprobada directo al guardarse, sin pasar nunca por
    `decision_factura` -- sin este llamado extra, las alertas de una factura
    aprobada directo nunca se convertían en caso).

    La clave es estable: reaprobar o reabrir no duplica trabajo operativo.
    """
    alertas = con.execute(
        "SELECT tipo, severidad, mensaje, concepto FROM alertas WHERE hash_pdf = ?", [hash_pdf]
    ).fetchall()
    sincronizar_casos_alertas(
        con,
        referencia=hash_pdf,
        alertas=[
            Alerta(tipo=tipo, severidad=severidad, mensaje=mensaje, concepto=concepto)
            for tipo, severidad, mensaje, concepto in alertas
        ],
    )


def sincronizar_casos_alertas(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
    *,
    referencia: str,
    alertas: list[Alerta],
) -> None:
    """Materializa alertas aprobadas en casos, sin duplicar una comparación."""
    for alerta in alertas:
        clave = _clave_caso(referencia, alerta)
        con.execute(
            """INSERT INTO casos_alerta
               (clave, hash_pdf, tipo, severidad, mensaje, concepto, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, now())
               ON CONFLICT (clave) DO UPDATE SET severidad = excluded.severidad,
                 mensaje = excluded.mensaje, actualizado_en = now()""",
            [
                clave,
                referencia,
                alerta.tipo,
                alerta.severidad,
                alerta.mensaje,
                alerta.concepto,
            ],
        )


def listar_casos_alerta(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
) -> list[tuple[str, str, str, str, str | None, str, str | None, str | None, str | None]]:
    """Casos abiertos y cerrados con los datos necesarios para su gestión."""
    return con.execute(
        """SELECT clave, tipo, severidad, mensaje, concepto, estado, responsable,
                  vencimiento, evidencia
           FROM casos_alerta
           ORDER BY CASE severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,
           creado_en"""
    ).fetchall()


def actualizar_caso_alerta(
    con: duckdb.DuckDBPyConnection | ConexionPostgres,
    *,
    clave: str,
    estado: str,
    responsable: str | None,
    vencimiento: str | None,
    evidencia: str | None,
) -> None:
    """Asigna y cierra un caso sin borrar su historia operativa."""
    if estado not in ESTADOS_CASO:
        raise ValueError(f"Estado de caso inválido: {estado}")
    con.execute(
        """UPDATE casos_alerta SET estado = ?, responsable = ?, vencimiento = ?, evidencia = ?,
           actualizado_en = now() WHERE clave = ?""",
        [estado, responsable, vencimiento, evidencia, clave],
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
           WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
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
    ruta_evidencia: str | None = None,
    emisor: str | None = None,
    servicio: str | None = None,
) -> None:
    """Guarda una factura que NO pasó la validación aritmética, con el
    detalle de qué falló -- CLAUDE.md: nunca entra al análisis.

    `emisor`/`servicio`: aunque la factura no haya validado aritméticamente,
    la extracción sí pudo leer quién la emitió y de qué servicio se trata --
    guardarlos permite `metricas_por_proveedor` (core/almacenamiento.py) sin
    tener que adivinar de qué proveedor viene una cuarentena."""
    motivos = "; ".join(resultado.motivos_de_falla())
    con.execute(
        """INSERT INTO cuarentena (hash_pdf, ruta_pdf, motivos, ruta_evidencia, emisor, servicio)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (hash_pdf) DO UPDATE SET ruta_pdf = excluded.ruta_pdf,
             motivos = excluded.motivos, ruta_evidencia = excluded.ruta_evidencia,
             emisor = excluded.emisor, servicio = excluded.servicio""",
        [hash_pdf, ruta_pdf, motivos, ruta_evidencia, emisor, servicio],
    )
