"""Tests de web/auth.py: la cookie de sesión firmada -- equivalente HTTP
del `st.session_state` que usaba `apps/segurplus/autenticacion.py`."""

import pytest

from web.auth import (
    contrasena_configurada,
    crear_cookie_sesion,
    intentar_login,
    leer_sesion,
    secret_key_configurada,
)


def test_sin_app_password_no_hay_contrasena_configurada(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert contrasena_configurada() is None


def test_sin_secret_key_no_hay_clave_configurada(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert secret_key_configurada() is None


def test_sin_secret_key_ni_dev_crear_cookie_sesion_no_arma_nada(monkeypatch):
    """docs/auditoria-2026-09-web.md, E-17: antes había una clave de firma
    fija de respaldo si faltaba SECRET_KEY -- ahora directamente no se
    arma la cookie."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SEGURPLUS_DEV", raising=False)
    with pytest.raises(RuntimeError):
        crear_cookie_sesion(usuario="x", rol="administrador")


def test_sin_secret_key_pero_con_dev_si_arma_la_cookie(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SEGURPLUS_DEV", "1")
    cookie = crear_cookie_sesion(usuario="x", rol="administrador")
    assert cookie is not None


def test_sin_secret_key_leer_sesion_no_explota_da_none(monkeypatch):
    """`leer_sesion` corre en cada request (middleware) -- sin SECRET_KEY
    tiene que devolver `None` en vez de levantar una excepción."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SEGURPLUS_DEV", raising=False)
    assert leer_sesion("cualquier-cosa") is None


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
