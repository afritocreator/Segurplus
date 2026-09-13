"""Tests del almacenamiento DuckDB contra un archivo temporal (nunca
data/reales/facturas.duckdb real -- ver CLAUDE.md)."""

import pytest

from core.almacenamiento import (
    alertas_del_periodo,
    borrar_de_cuarentena,
    conceptos_sin_clasificar,
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


# --- score_homologacion: persistido siempre, haya homologado o no ---------


def test_score_homologacion_se_guarda_para_concepto_homologado(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(
        con, factura, conceptos_normalizados={0: "abono_movil"}, scores_homologacion={0: 0.81}
    )
    score = con.execute(
        "SELECT score_homologacion FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert score == pytest.approx(0.81)
    con.close()


def test_score_homologacion_se_guarda_incluso_sin_homologar(tmp_path):
    # El caso que importa para calibrar: un concepto que NO homologó igual
    # guarda su score (0.44, por ejemplo) -- así se puede distinguir "casi
    # homologa, falta un alias" de "concepto genuinamente nuevo".
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, conceptos_normalizados={}, scores_homologacion={0: 0.44})
    fila = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos WHERE hash_pdf = ?",
        [factura.hash_pdf],
    ).fetchone()
    assert fila == (None, pytest.approx(0.44))
    con.close()


def test_score_homologacion_por_defecto_es_null(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)  # sin pasar scores_homologacion
    score = con.execute(
        "SELECT score_homologacion FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert score is None
    con.close()


def test_alter_table_score_homologacion_es_idempotente(tmp_path):
    ruta = tmp_path / "test.duckdb"
    conectar(ruta).close()
    conectar(ruta).close()  # conectar de nuevo no debe romper por la columna ya existente


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


# --- conceptos_sin_clasificar: ordenado por plata (Bloque 5) --------------


def test_conceptos_sin_clasificar_ordena_por_importe_descendente(tmp_path):
    con = conectar(tmp_path / "test.duckdb")

    barato = _factura("h1")
    barato.conceptos = [Concepto("Cargo raro chico", 1, None, 500.0, 500.0)]
    guardar_factura(con, barato, scores_homologacion={0: 0.3})

    caro = _factura("h2")
    caro.conceptos = [Concepto("Cargo raro grande", 1, None, 8000.0, 8000.0)]
    guardar_factura(con, caro, scores_homologacion={0: 0.4})

    filas = conceptos_sin_clasificar(con)
    assert [f[1] for f in filas] == ["Cargo raro grande", "Cargo raro chico"]
    assert filas[0][3] == pytest.approx(8000.0)
    con.close()


def test_conceptos_sin_clasificar_agrupa_por_servicio_y_descripcion(tmp_path):
    con = conectar(tmp_path / "test.duckdb")

    f1 = _factura("h1")
    f1.periodo_desde = "2026-08-01"
    f1.conceptos = [Concepto("Servicio de telefonía Agosto 2026", 1, None, 30000.0, 30000.0)]
    guardar_factura(con, f1, scores_homologacion={0: 0.588})

    f2 = _factura("h2")
    f2.periodo_desde = "2026-09-01"
    f2.conceptos = [Concepto("Servicio de telefonía Septiembre 2026", 1, None, 36000.0, 36000.0)]
    guardar_factura(con, f2, scores_homologacion={0: 0.588})

    filas = conceptos_sin_clasificar(con)
    # Dos descripciones DISTINTAS (con el período incluido, tal como se
    # guardaron) -- esta consulta lee la descripción cruda, la agrupación
    # por concepto normalizado ya la hace core.analisis.agregacion.
    assert len(filas) == 2
    assert sum(f[3] for f in filas) == pytest.approx(66000.0)
    con.close()


def test_conceptos_sin_clasificar_excluye_los_ya_homologados(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, conceptos_normalizados={0: "abono_movil"})
    assert conceptos_sin_clasificar(con) == []
    con.close()


def test_conceptos_sin_clasificar_acotado_por_servicio(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura("h1"), scores_homologacion={0: 0.3})
    otro = _factura("h2")
    otro.servicio = "gas"
    otro.conceptos = [Concepto("Consumo de gas raro", 1, None, 1000.0, 1000.0)]
    guardar_factura(con, otro, scores_homologacion={0: 0.3})

    filas = conceptos_sin_clasificar(con, servicio="telefonia")
    assert len(filas) == 1
    assert filas[0][0] == "telefonia"
    con.close()
