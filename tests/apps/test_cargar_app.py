"""Test de la página de Carga contra AppTest de Streamlit -- la parte de
subir un archivo real y hacer click en "Procesar" no es simulable con
AppTest (no expone un uploader que acepte bytes), así que esto cubre lo que
sí se puede probar sin Streamlit real: que la página renderiza con y sin
GEMINI_API_KEY configurada (docs/auditoria-2026-09.md, hallazgo A-19). El
resto del flujo (procesar_pdf, el try/except por archivo, el borrado del
temporal) ya está cubierto por tests/test_pipeline.py.

`RUTA_BASE` se parchea en TODOS los tests de este archivo, no solo en los
que suben algo (docs/auditoria-2026-09-piloto.md, hallazgo B-5): el
expander de "Últimos intentos fallidos" conecta a la base en CADA render de
la página, incluso sin subir nada -- sin parchear, `at.run()` tocaría
`data/reales/facturas.duckdb` real."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

import core.almacenamiento as almacenamiento_mod

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "cargar.py"


def _app():
    return AppTest.from_file(str(_ENTRYPOINT))


def test_sin_api_key_muestra_advertencia(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    monkeypatch.setattr("apps.segurplus.secretos.leer_secret", lambda clave: None)
    at = _app()
    at.run()
    assert not at.exception
    assert any("GEMINI_API_KEY" in w.value for w in at.warning)


def test_con_api_key_no_muestra_advertencia(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    monkeypatch.setattr("apps.segurplus.secretos.leer_secret", lambda clave: "fake-key")
    at = _app()
    at.run()
    assert not at.exception
    assert len(at.warning) == 0


def test_expander_sin_intentos_fallidos_no_rompe(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    monkeypatch.setattr("apps.segurplus.secretos.leer_secret", lambda clave: "fake-key")
    at = _app()
    at.run()
    assert not at.exception


def test_expander_muestra_intentos_fallidos_persistidos(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, hallazgo B-5: un fallo de una
    corrida anterior sigue visible después, sin depender de la sesión que
    lo produjo."""
    from core.almacenamiento import conectar, registrar_intento_gemini

    ruta_base = tmp_path / "test.duckdb"
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", ruta_base)
    con = conectar(ruta_base)
    registrar_intento_gemini(
        con,
        hash_pdf="x1",
        ruta_pdf="factura_rota.pdf",
        exito=False,
        mensaje="503 Service Unavailable",
    )
    con.close()

    monkeypatch.setattr("apps.segurplus.secretos.leer_secret", lambda clave: "fake-key")
    at = _app()
    at.run()
    assert not at.exception
    texto = " ".join(m.value for m in at.markdown)
    assert "factura_rota.pdf" in texto
    assert "503" in texto
