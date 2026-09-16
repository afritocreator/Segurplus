"""Test de la página "Casos" contra AppTest de Streamlit --
docs/auditoria-2026-09-piloto.md, A-54: es una de las dos páginas que
escriben en la base desde la UI y que había quedado sin ningún test."""

from pathlib import Path

import pytest

import core.almacenamiento as almacenamiento_mod
from core.almacenamiento import conectar, sincronizar_casos_alertas
from core.analisis.alertas import Alerta

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "casos.py"


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(_ENTRYPOINT))


def test_sin_rol_suficiente_no_puede_ver_la_pagina(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.session_state["segurplus_rol"] = "cargador"  # sin permiso para Casos
    at.run()
    assert not at.exception
    assert any("No tenés permisos" in e.value for e in at.error)


def test_sin_casos_explica_de_donde_salen(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()
    assert not at.exception
    assert any("No hay ningún caso todavía" in s.value for s in at.success)


@pytest.fixture
def base_con_un_caso(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar()
    sincronizar_casos_alertas(
        con,
        referencia="hash_de_prueba",
        alertas=[
            Alerta(
                tipo="item_duplicado",
                severidad="media",
                mensaje="Abono repetido en la misma factura",
                concepto="Abono",
            )
        ],
    )
    con.close()


def test_pagina_muestra_el_caso(base_con_un_caso):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()
    assert not at.exception
    filas = at.dataframe[0].value
    assert len(filas) == 1
    assert filas.iloc[0]["Tipo"] == "Ítem duplicado"
    assert filas.iloc[0]["Estado"] == "Abierto"


def test_actualizar_caso_persiste_estado_responsable_y_vencimiento(base_con_un_caso):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    at.selectbox[1].select("resuelto")  # selectbox[0] es "Caso a actualizar"
    at.text_input[0].input("compras@empresa.test")  # Responsable
    at.text_input[1].input("2026-12-31")  # Vencimiento
    at.text_area[0].input("Se resolvió hablando con el proveedor.")
    at.button[0].click().run()  # "Guardar caso" (form submit)

    assert not at.exception
    con = conectar()
    fila = con.execute(
        "SELECT estado, responsable, vencimiento, evidencia FROM casos_alerta"
    ).fetchone()
    con.close()
    assert fila == (
        "resuelto",
        "compras@empresa.test",
        "2026-12-31",
        "Se resolvió hablando con el proveedor.",
    )
