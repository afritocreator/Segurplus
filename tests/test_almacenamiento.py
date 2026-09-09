"""Tests del almacenamiento DuckDB contra un archivo temporal (nunca
data/reales/facturas.duckdb real -- ver CLAUDE.md)."""

from core.almacenamiento import (
    conectar,
    factura_ya_procesada,
    guardar_en_cuarentena,
    guardar_factura,
    recargos_del_periodo,
)
from core.extraccion.esquema import Concepto, FacturaExtraida
from core.extraccion.validacion import validar_factura


def _factura(hash_pdf="abc123") -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit="30-12345678-1",
        servicio="telefonia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision="2026-08-05",
        fecha_vencimiento="2026-08-20",
        numero_comprobante="0001-1",
        moneda="ARS",
        conceptos=[Concepto("Abono", 4, "línea", 2500.0, 10000.0)],
        subtotal=10000.0,
        total=12100.0,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


def test_guardar_y_leer_factura(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    assert not factura_ya_procesada(con, factura.hash_pdf)

    guardar_factura(con, factura, conceptos_normalizados={0: "abono_movil"})
    assert factura_ya_procesada(con, factura.hash_pdf)

    fila = con.execute(
        "SELECT emisor, total FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()
    assert fila == ("Movistar", 12100.0)

    concepto = con.execute(
        "SELECT concepto_normalizado, importe FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()
    assert concepto == ("abono_movil", 10000.0)
    con.close()


def test_reprocesar_no_duplica_conceptos(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)
    guardar_factura(con, factura)  # reprocesar la misma carpeta
    cantidad = con.execute(
        "SELECT COUNT(*) FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert cantidad == 1
    con.close()


def test_factura_rota_va_a_cuarentena_no_a_facturas(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="rota456")
    factura.conceptos[0].importe = 999999.0  # rompe la validación
    resultado = validar_factura(factura)
    assert not resultado.factura_valida

    guardar_en_cuarentena(
        con, hash_pdf=factura.hash_pdf, ruta_pdf=factura.ruta_pdf, resultado=resultado
    )
    assert factura_ya_procesada(con, factura.hash_pdf)
    en_facturas = con.execute(
        "SELECT 1 FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()
    assert en_facturas is None  # nunca entró a facturas
    motivos = con.execute(
        "SELECT motivos FROM cuarentena WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert "línea" in motivos
    con.close()


def test_recargos_del_periodo(tmp_path):
    from core.extraccion.esquema import Recargo

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    factura.recargos = [Recargo("Interés por mora", importe=350.0)]
    guardar_factura(con, factura)

    recargos = recargos_del_periodo(con, servicio="telefonia", periodo_desde="2026-08-01")
    assert recargos == [("Interés por mora", 350.0)]

    sin_recargos = recargos_del_periodo(con, servicio="telefonia", periodo_desde="2020-01-01")
    assert sin_recargos == []
    con.close()
