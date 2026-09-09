"""Test de la página de Carga contra AppTest de Streamlit -- la parte de
subir un archivo real y hacer click en "Procesar" no es simulable con
AppTest (no expone un uploader que acepte bytes), así que esto cubre lo que
sí se puede probar sin Streamlit real: que la página renderiza con y sin
GEMINI_API_KEY configurada (docs/auditoria-2026-09.md, hallazgo A-19). El
resto del flujo (procesar_pdf, el try/except por archivo, el borrado del
temporal) ya está cubierto por tests/test_pipeline.py."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "apps" / "segurplus" / "paginas" / "cargar.py"


def _app():
    return AppTest.from_file(str(_ENTRYPOINT))


def test_sin_api_key_muestra_advertencia(monkeypatch):
    monkeypatch.setattr("apps.segurplus.secretos.leer_secret", lambda clave: None)
    at = _app()
    at.run()
    assert not at.exception
    assert any("GEMINI_API_KEY" in w.value for w in at.warning)


def test_con_api_key_no_muestra_advertencia(monkeypatch):
    monkeypatch.setattr("apps.segurplus.secretos.leer_secret", lambda clave: "fake-key")
    at = _app()
    at.run()
    assert not at.exception
    assert len(at.warning) == 0
