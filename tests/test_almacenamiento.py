"""Tests del almacenamiento DuckDB contra un archivo temporal (nunca
data/reales/facturas.duckdb real -- ver CLAUDE.md)."""

from core.almacenamiento import (
    alertas_del_periodo,
    borrar_de_cuarentena,
    conectar,
    factura_ya_procesada,
    guardar_alertas,
    guardar_en_cuarentena,
    guardar_factura,
    llamadas_ultima_hora,
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


def test_guardar_y_leer_alertas_del_periodo(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)

    guardar_alertas(
        con,
        factura.hash_pdf,
        [Alerta(tipo="item_duplicado", severidad="media", mensaje="test", concepto="Abono")],
    )

    alertas = alertas_del_periodo(con, servicio="telefonia", periodo_desde="2026-08-01")
    assert len(alertas) == 1
    assert alertas[0].tipo == "item_duplicado"
    assert alertas[0].concepto == "Abono"

    sin_alertas = alertas_del_periodo(con, servicio="telefonia", periodo_desde="2020-01-01")
    assert sin_alertas == []
    con.close()


# --- A-7: tope de llamadas por hora --------------------------------------


def test_llamadas_ultima_hora_cuenta_facturas_y_cuarentena(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    assert llamadas_ultima_hora(con) == 0

    guardar_factura(con, _factura(hash_pdf="a1"))
    assert llamadas_ultima_hora(con) == 1

    rota = _factura(hash_pdf="b2")
    resultado = validar_factura(rota)  # subtotal/total consistentes -> válida
    guardar_en_cuarentena(con, hash_pdf="c3", ruta_pdf="/tmp/c3.pdf", resultado=resultado)
    assert llamadas_ultima_hora(con) == 2
    con.close()


# --- A-17: reintentar desde cuarentena ------------------------------------


def test_borrar_de_cuarentena_libera_el_hash_para_reprocesar(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="rota789")
    factura.conceptos[0].importe = 999999.0
    resultado = validar_factura(factura)

    guardar_en_cuarentena(
        con, hash_pdf=factura.hash_pdf, ruta_pdf=factura.ruta_pdf, resultado=resultado
    )
    assert factura_ya_procesada(con, factura.hash_pdf)

    borrar_de_cuarentena(con, factura.hash_pdf)
    assert not factura_ya_procesada(con, factura.hash_pdf)
    con.close()


def test_guardar_alertas_es_idempotente(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)

    alerta = [Alerta(tipo="item_duplicado", severidad="media", mensaje="test", concepto="Abono")]
    guardar_alertas(con, factura.hash_pdf, alerta)
    guardar_alertas(con, factura.hash_pdf, alerta)  # reprocesar no duplica

    cantidad = con.execute(
        "SELECT COUNT(*) FROM alertas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert cantidad == 1
    con.close()
