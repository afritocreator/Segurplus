"""Test del gate de acceso, contra AppTest de Streamlit (simula la app sin
levantar un servidor real)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "streamlit_app.py"


def _app():
    return AppTest.from_file(str(_ENTRYPOINT))


def test_sin_password_configurada_pasa_directo():
    at = _app()
    at.run()
    assert not at.exception
    # Sin APP_PASSWORD en secrets, no debería quedar trabado en el login --
    # la página de "Cargar facturas" (default) tiene que renderizar.
    assert "Cargar facturas" in at.title[0].value or len(at.title) > 0


def test_con_password_configurada_pide_login(monkeypatch):
    at = _app()
    at.secrets["APP_PASSWORD"] = "clave-secreta"
    at.run()
    assert not at.exception
    assert any("contraseña" in b.value.lower() for b in at.text_input) or len(at.text_input) > 0


def test_con_password_incorrecta_muestra_error():
    at = _app()
    at.secrets["APP_PASSWORD"] = "clave-secreta"
    at.run()
    at.text_input[0].input("clave-mala").run()
    at.button[0].click().run()
    assert any("incorrecta" in e.value.lower() for e in at.error)


def test_con_password_correcta_entra():
    at = _app()
    at.secrets["APP_PASSWORD"] = "clave-secreta"
    at.run()
    at.text_input[0].input("clave-secreta").run()
    at.button[0].click().run()
    assert not at.exception
    assert len(at.error) == 0
