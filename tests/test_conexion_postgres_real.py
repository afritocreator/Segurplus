"""Test opcional que ejercita el camino PostgreSQL REAL de
`core.almacenamiento.ConexionPostgres` -- docs/auditoria-2026-09-piloto.md,
A-57: antes de este test, ese camino solo estaba vigilado por un chequeo
estático del SQL (tests/test_conexion_postgres.py), nunca ejercitado de
verdad contra un Postgres real -- todo lo que corre en la suite normal usa
DuckDB.

Se salta salvo que:
- `TEST_DATABASE_URL` esté configurada, apuntando a una base (o un schema
  dentro de una base, vía `?options=-csearch_path%3D<schema>` en la URL)
  DESCARTABLE -- NUNCA la de producción. Este test escribe y borra sus
  propias filas de prueba (hash_pdf muy distintivo) en su teardown, pero
  nunca hace TRUNCATE ni DROP TABLE, así que tampoco haría un desastre
  irrecuperable si por error apuntara a la real -- aun así, no vale la
  pena arriesgarlo.
- `psycopg` esté instalado (`pip install -e ".[dev]"` no lo trae por sí
  solo -- ya viene con las dependencias base del proyecto, ver
  pyproject.toml, pero puede faltar en un venv de desarrollo mínimo).

Correr (ver también "Crear la base Postgres gratis" en README.md para
armar una base/schema de prueba en Neon o Supabase):

    TEST_DATABASE_URL="postgresql://..." \
        pytest tests/test_conexion_postgres_real.py -v -m red_real
"""

from __future__ import annotations

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

try:
    import psycopg  # noqa: F401

    _PSYCOPG_DISPONIBLE = True
except ImportError:
    _PSYCOPG_DISPONIBLE = False

pytestmark = [
    pytest.mark.red_real,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="requiere TEST_DATABASE_URL (no configurada)"),
    pytest.mark.skipif(not _PSYCOPG_DISPONIBLE, reason="requiere psycopg instalado"),
]

HASH_DE_PRUEBA = "pytest-a57-ejercita-postgres-real"


@pytest.fixture
def con(monkeypatch):
    from core.almacenamiento import conectar

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    conexion = conectar()  # ruta=None -> toma DATABASE_URL, ejercita ConexionPostgres + el DDL real
    try:
        yield conexion
    finally:
        # Limpieza acotada a las filas de ESTE test -- nunca TRUNCATE ni
        # DROP TABLE, la base de prueba puede tener otros datos.
        conexion.execute("DELETE FROM conceptos WHERE hash_pdf = ?", [HASH_DE_PRUEBA])
        conexion.execute("DELETE FROM decisiones_factura WHERE hash_pdf = ?", [HASH_DE_PRUEBA])
        conexion.execute("DELETE FROM facturas WHERE hash_pdf = ?", [HASH_DE_PRUEBA])
        conexion.close()


def test_guardar_factura_y_conceptos_sin_clasificar_contra_postgres_real(con):
    """Ejercita conectar() + el DDL real + guardar_factura (el
    INSERT ... ON CONFLICT DO UPDATE que solo se probó contra DuckDB) +
    conceptos_sin_clasificar leyendo de vuelta."""
    from core.almacenamiento import conceptos_sin_clasificar, guardar_factura
    from core.extraccion.esquema import Concepto, FacturaExtraida

    factura = FacturaExtraida(
        emisor="Test A-57",
        cuit=None,
        servicio="telefonia_test_a57",
        periodo_desde="2026-01-01",
        periodo_hasta="2026-01-31",
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        conceptos=[Concepto("Concepto de prueba A-57", 1, None, 111.0, 111.0)],
        subtotal=111.0,
        total=111.0,
        hash_pdf=HASH_DE_PRUEBA,
        ruta_pdf="/tmp/x.pdf",
    )
    guardar_factura(con, factura, estado="aprobada")

    # ON CONFLICT DO UPDATE: reguardar la misma factura no debe duplicar nada.
    guardar_factura(con, factura, estado="aprobada")
    cantidad = con.execute(
        "SELECT count(*) FROM facturas WHERE hash_pdf = ?", [HASH_DE_PRUEBA]
    ).fetchone()[0]
    assert cantidad == 1

    filas = conceptos_sin_clasificar(con, servicio="telefonia_test_a57")
    assert any(descripcion == "Concepto de prueba A-57" for _s, descripcion, *_ in filas)


def test_aplicar_cambios_begin_commit_contra_postgres_real(con):
    """Ejercita el BEGIN/COMMIT manual de `core.rehomologacion.
    aplicar_cambios` sobre una `ConexionPostgres` abierta con
    `autocommit=True` -- psycopg desaconseja mezclar transacciones
    manuales con autocommit, así que esta es la única forma de saber si en
    la práctica funciona contra un Postgres real (nunca se corre en CI)."""
    from core.almacenamiento import guardar_factura
    from core.extraccion.esquema import Concepto, FacturaExtraida
    from core.rehomologacion import CambioHomologacion, aplicar_cambios

    factura = FacturaExtraida(
        emisor="Test A-57",
        cuit=None,
        servicio="telefonia_test_a57",
        periodo_desde="2026-01-01",
        periodo_hasta="2026-01-31",
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        conceptos=[Concepto("Concepto de prueba A-57", 1, None, 111.0, 111.0)],
        subtotal=111.0,
        total=111.0,
        hash_pdf=HASH_DE_PRUEBA,
        ruta_pdf="/tmp/x.pdf",
    )
    guardar_factura(con, factura, estado="aprobada")

    cambio = CambioHomologacion(
        hash_pdf=HASH_DE_PRUEBA,
        orden=0,
        descripcion="Concepto de prueba A-57",
        servicio="telefonia_test_a57",
        concepto_antes=None,
        score_antes=None,
        concepto_despues="concepto_de_prueba_a57",
        score_despues=1.0,
    )
    tocadas = aplicar_cambios(con, [cambio])
    assert tocadas == 1

    concepto = con.execute(
        "SELECT concepto_normalizado FROM conceptos WHERE hash_pdf = ?", [HASH_DE_PRUEBA]
    ).fetchone()[0]
    assert concepto == "concepto_de_prueba_a57"
