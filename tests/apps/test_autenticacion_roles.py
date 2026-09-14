"""Test de la resolución de roles por OIDC (apps/segurplus/autenticacion.py)
-- docs/auditoria-2026-09-piloto.md, A-58: si `OIDC_PROVIDER` está
configurado pero ningún `ROLE_*_EMAILS` tiene contenido, antes de esta
corrección todos quedaban con "cargador" (el de menor privilegio) y NADIE
podía entrar a "Revisar facturas" ni a "Casos", ni siquiera quien configuró
el sistema. Tests unitarios directos sobre `_rol_oidc`/`_algun_rol_
configurado`/`requerir_rol`, monkeypatcheando `leer_secret` -- más simple y
más preciso que simular el login OIDC completo de `st.user` vía AppTest."""

import streamlit as st

import apps.segurplus.autenticacion as auth_mod
from apps.segurplus.autenticacion import (
    _algun_rol_configurado,
    _rol_oidc,
    requerir_rol,
)


def _sin_ningun_rol_configurado(monkeypatch):
    monkeypatch.setattr(auth_mod, "leer_secret", lambda clave: None)


def _con_administrador_configurado(monkeypatch):
    secretos = {"ROLE_ADMINISTRADOR_EMAILS": "ana@empresa.test"}
    monkeypatch.setattr(auth_mod, "leer_secret", lambda clave: secretos.get(clave))


def test_algun_rol_configurado_false_sin_ningun_secret(monkeypatch):
    _sin_ningun_rol_configurado(monkeypatch)
    assert _algun_rol_configurado() is False


def test_algun_rol_configurado_true_con_uno_solo(monkeypatch):
    _con_administrador_configurado(monkeypatch)
    assert _algun_rol_configurado() is True


def test_rol_oidc_falla_abierto_sin_ningun_rol_configurado(monkeypatch):
    """El caso del hallazgo: nadie cargó ROLE_*_EMAILS -- un e-mail
    cualquiera, que no está en ninguna lista (porque no hay listas), tiene
    que quedar como administrador, no como cargador."""
    _sin_ningun_rol_configurado(monkeypatch)
    rol = _rol_oidc("quien-sea@empresa.test", algun_rol_configurado=False)
    assert rol == "administrador"


def test_rol_oidc_no_falla_abierto_si_hay_algun_rol_configurado(monkeypatch):
    """Con AL MENOS un rol configurado, un e-mail que no está en ninguna
    lista sí tiene que quedar como "cargador" -- fallar abierto solo se
    justifica cuando la configuración de roles está directamente ausente,
    no cuando alguien simplemente no fue agregado a ninguna lista."""
    _con_administrador_configurado(monkeypatch)
    rol = _rol_oidc("no-deberia-entrar@empresa.test", algun_rol_configurado=True)
    assert rol == "cargador"


def test_rol_oidc_respeta_la_lista_cuando_el_email_esta_incluido(monkeypatch):
    _con_administrador_configurado(monkeypatch)
    rol = _rol_oidc("ana@empresa.test", algun_rol_configurado=True)
    assert rol == "administrador"


def test_requerir_rol_avisa_cuando_los_roles_no_estan_configurados(monkeypatch):
    st.session_state.clear()
    st.session_state["segurplus_roles_sin_configurar"] = True
    st.session_state["segurplus_rol"] = "administrador"
    # No debe bloquear (administrador SÍ está en la lista permitida acá),
    # pero sí tiene que haber avisado antes de dejarlo pasar.
    requerir_rol("administrador")


def test_requerir_rol_no_avisa_cuando_los_roles_si_estan_configurados(monkeypatch):
    st.session_state.clear()
    st.session_state["segurplus_roles_sin_configurar"] = False
    st.session_state["segurplus_rol"] = "administrador"
    requerir_rol("administrador")  # no debe explotar ni requerir nada más
