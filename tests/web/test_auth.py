"""Tests de web/auth.py: la cookie de sesión firmada -- equivalente HTTP
del `st.session_state` que usaba `apps/segurplus/autenticacion.py`."""

from web.auth import contrasena_configurada, crear_cookie_sesion, intentar_login, leer_sesion


def test_sin_app_password_no_hay_contrasena_configurada(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert contrasena_configurada() is None


def test_intentar_login_sin_contrasena_configurada_falla(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert intentar_login("cualquier-cosa") is None


def test_intentar_login_correcto_da_una_cookie(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "correcta123")
    monkeypatch.setenv("SECRET_KEY", "clave-de-test")
    cookie = intentar_login("correcta123")
    assert cookie is not None
    sesion = leer_sesion(cookie)
    assert sesion == {"usuario": "operador-transitorio", "rol": "administrador"}


def test_intentar_login_incorrecto_no_da_cookie(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "correcta123")
    monkeypatch.setenv("SECRET_KEY", "clave-de-test")
    assert intentar_login("incorrecta") is None


def test_leer_sesion_cookie_ausente_da_none():
    assert leer_sesion(None) is None
    assert leer_sesion("") is None


def test_leer_sesion_cookie_forjada_da_none(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "clave-de-test")
    assert leer_sesion("esto-no-es-una-cookie-valida") is None


def test_leer_sesion_cookie_firmada_con_otra_clave_no_es_valida(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "clave-a")
    cookie = crear_cookie_sesion(usuario="x", rol="administrador")
    monkeypatch.setenv("SECRET_KEY", "clave-b")
    assert leer_sesion(cookie) is None
