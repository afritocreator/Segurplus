"""Test de core/rehomologacion.py -- el motor de re-homologación no debe
depender de Gemini ni de la red (ver docstring del módulo), y `recalcular`
tiene que clasificar cada cambio en el tipo correcto (nuevo/regresion/
cambio/sin_cambio) para que el script de calibración avise de regresiones."""

import pytest

from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import Concepto, FacturaExtraida
from core.rehomologacion import (
    CambioHomologacion,
    FilaARehomologar,
    aplicar_cambios,
    leer_filas_a_rehomologar,
    recalcular,
)

DICCIONARIO = {
    "abono_movil": ["abono linea movil", "cargo fijo movil"],
    "consumo_datos": ["consumo de datos"],
}


def test_nunca_importa_gemini_ni_nada_que_salga_a_la_red():
    # Este módulo re-homologa lo que YA está guardado -- no puede consumir
    # la cuota de MAX_LLAMADAS_POR_HORA (core/extraccion/gemini.py) ni
    # pegarle a la red por ningún motivo. Chequeo sobre los IMPORTS reales
    # del módulo (con ast, no un grep sobre el texto -- el docstring
    # menciona "Gemini" en prosa, eso no cuenta).
    import ast

    import core.rehomologacion as modulo

    arbol = ast.parse(modulo.__loader__.get_source(modulo.__name__))
    modulos_importados = {
        alias.name
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Import)
        for alias in nodo.names
    } | {
        nodo.module for nodo in ast.walk(arbol) if isinstance(nodo, ast.ImportFrom) and nodo.module
    }
    assert not any("gemini" in m or "requests" in m for m in modulos_importados)


def test_recalcular_tipo_nuevo():
    # No homologaba (concepto_actual None), ahora sí.
    fila = FilaARehomologar(
        hash_pdf="h1",
        orden=0,
        descripcion="Abono linea movil",
        servicio="telefonia",
        concepto_actual=None,
        score_actual=0.3,
    )
    cambios = recalcular([fila], {"telefonia": DICCIONARIO})
    assert len(cambios) == 1
    assert cambios[0].tipo == "nuevo"
    assert cambios[0].concepto_despues == "abono_movil"


def test_recalcular_tipo_sin_cambio():
    fila = FilaARehomologar(
        hash_pdf="h1",
        orden=0,
        descripcion="Abono linea movil",
        servicio="telefonia",
        concepto_actual="abono_movil",
        score_actual=0.9,
    )
    cambios = recalcular([fila], {"telefonia": DICCIONARIO})
    assert cambios[0].tipo == "sin_cambio"
    assert cambios[0].concepto_despues == "abono_movil"


def test_recalcular_tipo_regresion():
    # Homologaba antes (quizás con un diccionario viejo que ya no tiene
    # ese alias), ahora con el diccionario actual no homologa.
    fila = FilaARehomologar(
        hash_pdf="h1",
        orden=0,
        descripcion="Un concepto rarísimo que no está en ningún alias",
        servicio="telefonia",
        concepto_actual="abono_movil",
        score_actual=0.5,
    )
    cambios = recalcular([fila], {"telefonia": DICCIONARIO})
    assert cambios[0].tipo == "regresion"
    assert cambios[0].concepto_despues is None


def test_recalcular_tipo_cambio():
    fila = FilaARehomologar(
        hash_pdf="h1",
        orden=0,
        descripcion="Consumo de datos",
        servicio="telefonia",
        concepto_actual="abono_movil",  # mal clasificado antes
        score_actual=0.46,
    )
    cambios = recalcular([fila], {"telefonia": DICCIONARIO})
    assert cambios[0].tipo == "cambio"
    assert cambios[0].concepto_despues == "consumo_datos"


def test_recalcular_servicio_sin_diccionario_da_diccionario_vacio():
    fila = FilaARehomologar(
        hash_pdf="h1",
        orden=0,
        descripcion="Abono linea movil",
        servicio="seguro",  # no está en diccionarios_por_servicio
        concepto_actual=None,
        score_actual=None,
    )
    cambios = recalcular([fila], {"telefonia": DICCIONARIO})
    assert cambios[0].concepto_despues is None
    assert cambios[0].score_despues == 0.0


# --- contra DuckDB real (temporal) -----------------------------------------


def _factura(hash_pdf="h1") -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit="30-12345678-1",
        servicio="telefonia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision="2026-08-05",
        fecha_vencimiento=None,
        numero_comprobante="0001-1",
        moneda="ARS",
        conceptos=[Concepto("Abono linea movil", 4, "línea", 2500.0, 10000.0)],
        subtotal=10000.0,
        total=12100.0,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


def test_leer_filas_a_rehomologar_trae_el_servicio_de_facturas(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, scores_homologacion={0: 0.3})  # sin homologar todavía

    filas = leer_filas_a_rehomologar(con)
    assert len(filas) == 1
    assert filas[0].descripcion == "Abono linea movil"
    assert filas[0].servicio == "telefonia"
    assert filas[0].concepto_actual is None
    assert filas[0].score_actual == pytest.approx(0.3)
    con.close()


def test_leer_filas_a_rehomologar_acotado_por_servicio(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura("h1"))
    otra = _factura("h2")
    otra.servicio = "gas"
    otra.conceptos = [Concepto("Consumo de gas", 50, "m3", 100.0, 5000.0)]
    guardar_factura(con, otra)

    solo_telefonia = leer_filas_a_rehomologar(con, servicio="telefonia")
    assert len(solo_telefonia) == 1
    assert solo_telefonia[0].servicio == "telefonia"
    con.close()


def test_aplicar_cambios_actualiza_la_base(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, scores_homologacion={0: 0.3})

    cambio = CambioHomologacion(
        hash_pdf=factura.hash_pdf,
        orden=0,
        descripcion="Abono linea movil",
        servicio="telefonia",
        concepto_antes=None,
        score_antes=0.3,
        concepto_despues="abono_movil",
        score_despues=1.0,
    )
    tocadas = aplicar_cambios(con, [cambio])
    assert tocadas == 1

    fila = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos WHERE hash_pdf = ?",
        [factura.hash_pdf],
    ).fetchone()
    assert fila == ("abono_movil", pytest.approx(1.0))
    con.close()


def test_aplicar_cambios_es_idempotente(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, scores_homologacion={0: 0.3})

    cambio = CambioHomologacion(
        hash_pdf=factura.hash_pdf,
        orden=0,
        descripcion="Abono linea movil",
        servicio="telefonia",
        concepto_antes=None,
        score_antes=0.3,
        concepto_despues="abono_movil",
        score_despues=1.0,
    )
    aplicar_cambios(con, [cambio])
    aplicar_cambios(con, [cambio])  # aplicar dos veces no rompe ni duplica

    cantidad = con.execute(
        "SELECT COUNT(*) FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert cantidad == 1
    con.close()


def test_flujo_completo_end_to_end(tmp_path):
    # El flujo real: guardar sin homologar, agregar el alias "de mentira"
    # (acá pasado directo, en el uso real vendría de editar el YAML),
    # recalcular y aplicar.
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, scores_homologacion={0: 0.3})  # sin match todavía

    filas = leer_filas_a_rehomologar(con)
    cambios = recalcular(filas, {"telefonia": DICCIONARIO})
    assert cambios[0].tipo == "nuevo"

    aplicar_cambios(con, cambios)
    concepto = con.execute(
        "SELECT concepto_normalizado FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert concepto == "abono_movil"
    con.close()
