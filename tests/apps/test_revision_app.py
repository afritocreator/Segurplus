"""Test de la página "Revisar facturas" contra AppTest de Streamlit --
docs/auditoria-2026-09-piloto.md, A-54: es una de las dos páginas que
escriben en la base desde la UI y que había quedado sin ningún test."""

from pathlib import Path

import pytest

import core.almacenamiento as almacenamiento_mod
from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import Concepto, FacturaExtraida

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "revision.py"


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(_ENTRYPOINT))


def _factura(hash_pdf: str = "h1") -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit="30-1",
        servicio="telefonia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision="2026-08-05",
        fecha_vencimiento=None,
        numero_comprobante=hash_pdf,
        moneda="ARS",
        conceptos=[Concepto("Abono", 4, "línea", 2500.0, 10000.0)],
        subtotal=10000.0,
        total=10000.0,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


def test_sin_rol_suficiente_no_puede_ver_la_pagina(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.session_state["segurplus_rol"] = "cargador"
    at.run()
    assert not at.exception
    assert any("No tenés permisos" in e.value for e in at.error)


def test_sin_ninguna_factura_muestra_exito(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "vacia.duckdb")
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()
    assert not at.exception
    assert any("No hay ninguna factura para revisar" in s.value for s in at.success)


@pytest.fixture
def base_con_una_pendiente(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar()
    guardar_factura(con, _factura(), estado="requiere_revision")
    con.close()


@pytest.fixture
def base_con_una_aprobada(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    con = conectar()
    guardar_factura(con, _factura(), estado="aprobada")
    con.close()


def test_pagina_renderiza_con_una_pendiente(base_con_una_pendiente):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()
    assert not at.exception


def test_aprobar_una_factura_pendiente_de_a_una(base_con_una_pendiente):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    at.get_by_key("aprobar_h1").input("Conciliada contra el PDF original.")
    at.get_by_key("btn_aprobar_h1").click().run()

    assert not at.exception
    con = conectar()
    estado = con.execute("SELECT estado FROM facturas WHERE hash_pdf = 'h1'").fetchone()[0]
    con.close()
    assert estado == "aprobada"


def test_rechazar_una_factura_pendiente(base_con_una_pendiente):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    at.get_by_key("rechazar_h1").input("Emisor equivocado.")
    at.get_by_key("btn_rechazar_h1").click().run()

    assert not at.exception
    con = conectar()
    estado = con.execute("SELECT estado FROM facturas WHERE hash_pdf = 'h1'").fetchone()[0]
    con.close()
    assert estado == "rechazada"


def test_aprobar_en_lote(base_con_una_pendiente):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    at.text_input[0].input("Carga inicial revisada en bloque.")  # motivo del lote
    at.button[0].click().run()  # "Aprobar todas las pendientes"

    assert not at.exception
    con = conectar()
    estado = con.execute("SELECT estado FROM facturas WHERE hash_pdf = 'h1'").fetchone()[0]
    con.close()
    assert estado == "aprobada"


def test_corregir_cabecera_de_una_factura_pendiente(base_con_una_pendiente):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    # Dentro de "Revisar de a una": selectbox[0] es el selector de factura
    # (key="selector_pendientes"), selectbox[1] es "Campo" del formulario
    # de corrección (sin key propia). Cambiar "Campo" corre el script una
    # vez (sin enviar el form) para que "Valor corregido" recalcule su
    # default a partir del NUEVO campo elegido -- si no, el input que se
    # cargue ahí queda atado al widget del campo anterior y se pierde al
    # re-ejecutar el script con el campo nuevo. Con "servicio", "Valor
    # corregido" también es un selectbox (hallazgo C-3), no un text_input.
    at.selectbox[1].select("servicio").run()
    at.selectbox[2].select("gas")  # "Valor corregido"
    at.text_input[1].input("El modelo confundió el servicio.")  # "Motivo de corrección"
    at.button[1].click().run()  # "Registrar corrección" (button[0] es el lote)

    assert not at.exception
    con = conectar()
    servicio = con.execute("SELECT servicio FROM facturas WHERE hash_pdf = 'h1'").fetchone()[0]
    con.close()
    assert servicio == "gas"


def test_corregir_fecha_no_interpretable_muestra_error_sin_traceback(base_con_una_pendiente):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-4: antes el
    ValueError de registrar_correccion no estaba atrapado acá -- escribir
    "julio 2022" tiraba el traceback de Streamlit encima de la página en
    vez de mostrar el mensaje de ayuda de la fecha."""
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    at.selectbox[1].select("periodo_desde").run()
    at.text_input[1].input("julio 2022")  # "Valor corregido"
    at.text_input[2].input("Corrigiendo el período.")  # "Motivo de corrección"
    at.button[1].click().run()

    assert not at.exception
    assert any("no se pudo interpretar" in e.value.lower() for e in at.error)
    con = conectar()
    periodo_desde = con.execute(
        "SELECT periodo_desde FROM facturas WHERE hash_pdf = 'h1'"
    ).fetchone()[0]
    con.close()
    assert periodo_desde == "2026-08-01"  # sin cambios: la corrección se rechazó


def test_pagina_muestra_una_factura_ya_aprobada(base_con_una_aprobada):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()
    assert not at.exception


def test_rechazar_una_factura_ya_aprobada(base_con_una_aprobada):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    at.get_by_key("rechazar_aprobada_h1").input("Resultó ser de otro proveedor.")
    at.get_by_key("btn_rechazar_aprobada_h1").click().run()

    assert not at.exception
    con = conectar()
    estado = con.execute("SELECT estado FROM facturas WHERE hash_pdf = 'h1'").fetchone()[0]
    con.close()
    assert estado == "rechazada"


def test_corregir_cabecera_de_una_factura_ya_aprobada(base_con_una_aprobada):
    at = _app()
    at.session_state["segurplus_rol"] = "administrador"
    at.run()

    # Sin pendientes, solo la pestaña "Ya aprobadas" tiene widgets:
    # selectbox[0]=selector_aprobadas, selectbox[1]="Campo" del formulario.
    # Mismo motivo que en el test de la pendiente: correr una vez tras
    # cambiar "Campo" para que "Valor corregido" recalcule su default. Con
    # "servicio", "Valor corregido" también es un selectbox (hallazgo C-3).
    at.selectbox[1].select("servicio").run()
    at.selectbox[2].select("gas")  # "Valor corregido"
    at.text_input[0].input("El modelo confundió el servicio.")  # "Motivo de corrección"
    at.button[0].click().run()  # "Registrar corrección"

    assert not at.exception
    con = conectar()
    servicio = con.execute("SELECT servicio FROM facturas WHERE hash_pdf = 'h1'").fetchone()[0]
    con.close()
    assert servicio == "gas"
