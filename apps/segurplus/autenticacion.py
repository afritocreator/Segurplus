"""Gate de acceso de la app: pide una contraseña compartida antes de mostrar
cualquier página. Ver core/autenticacion.py para el porqué y las
limitaciones de este esquema (no es control de acceso real).

Cáscara fina sobre `core.autenticacion.verificar_contrasena`: acá solo vive
la parte de Streamlit (formulario, session_state), la comparación en sí no.

docs/auditoria-2026-09.md, hallazgo A-8: antes, la ausencia de `APP_PASSWORD`
(incluida la falta total de `secrets.toml`) dejaba pasar sin login -- pensado
para no trabar el desarrollo local, pero es un default ABIERTO: si algo sale
mal desplegando el secret en producción, la app queda pública sin avisar.
Ahora el default es CERRADO: sin `APP_PASSWORD` configurada, bloquea con un
mensaje explícito, salvo que se declare a propósito `SEGURPLUS_DEV=1` (solo
para desarrollo local). Y un error real al leer secrets (no el caso
"no hay secrets.toml", que `leer_secret` ya distingue) también bloquea, en
vez de interpretarse como "no hay contraseña".
"""

from __future__ import annotations

import os

import streamlit as st

from apps.segurplus.secretos import leer_secret
from core.autenticacion import verificar_contrasena


def usuario_actual() -> str:
    """Identidad trazable del usuario actual; nunca registra secretos."""
    return str(st.session_state.get("segurplus_usuario", "operador-transitorio"))


def rol_actual() -> str:
    """Rol de menor privilegio configurado para el usuario autenticado."""
    return str(st.session_state.get("segurplus_rol", "cargador"))


def requerir_rol(*roles: str) -> None:
    if rol_actual() not in roles:
        st.error("No tenés permisos para realizar esta acción.")
        st.stop()


def _rol_oidc(email: str) -> str:
    """Resuelve roles por listas de e-mails en secrets, sin roles en cliente."""
    for rol in ("administrador", "responsable", "revisor", "cargador"):
        permitidos = leer_secret(f"ROLE_{rol.upper()}_EMAILS") or ""
        if email.lower() in {
            valor.strip().lower() for valor in permitidos.split(",") if valor.strip()
        }:
            return rol
    return "cargador"


def _requerir_oidc(proveedor: str) -> None:
    """Login OIDC nativo de Streamlit cuando el deploy declara un proveedor."""
    if not st.user.is_logged_in:
        st.login(proveedor)
        st.stop()
    email = str(getattr(st.user, "email", ""))
    if not email:
        st.error("El proveedor de identidad no informó un e-mail verificable.")
        st.stop()
    st.session_state["segurplus_usuario"] = email
    st.session_state["segurplus_rol"] = _rol_oidc(email)


def requerir_contrasena() -> None:
    """Si la sesión ya se autenticó, no hace nada. Si no, muestra un
    formulario de login y `st.stop()` -- ninguna página después de este
    llamado en `streamlit_app.py` llega a renderizar sin pasar por acá."""
    proveedor_oidc = leer_secret("OIDC_PROVIDER")
    if proveedor_oidc:
        _requerir_oidc(proveedor_oidc)
        return

    if st.session_state.get("autenticado"):
        return

    try:
        contrasena_esperada = leer_secret("APP_PASSWORD")
    except Exception:
        st.title("🧾 Segurplus")
        st.error(
            "Error de configuración al leer la contraseña de acceso. "
            "Avisá a quien administra la herramienta -- no se puede mostrar la app así."
        )
        st.stop()
        return

    if not contrasena_esperada:
        if os.environ.get("SEGURPLUS_DEV") == "1":
            # Desarrollo local explícito -- ver docstring del módulo.
            st.session_state["autenticado"] = True
            st.session_state["segurplus_usuario"] = "desarrollo-local"
            st.session_state["segurplus_rol"] = "administrador"
            return
        st.title("🧾 Segurplus")
        st.error(
            "Falta configurar APP_PASSWORD. Por seguridad, la app no se muestra sin "
            "contraseña (para desarrollo local, definí SEGURPLUS_DEV=1)."
        )
        st.stop()
        return

    st.title("🧾 Segurplus")
    st.caption("Acceso restringido — pedile la contraseña a quien administra la herramienta.")
    with st.form("login"):
        ingresada = st.text_input("Contraseña", type="password")
        enviado = st.form_submit_button("Entrar")

    if enviado:
        if verificar_contrasena(ingresada, contrasena_esperada):
            st.session_state["autenticado"] = True
            # Compatibilidad de piloto: el deploy productivo debe configurar
            # OIDC_PROVIDER. La cuenta compartida no otorga trazabilidad real.
            st.session_state["segurplus_usuario"] = "operador-transitorio"
            st.session_state["segurplus_rol"] = "administrador"
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")

    st.stop()
