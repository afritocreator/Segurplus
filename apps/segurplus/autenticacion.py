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


def requerir_contrasena() -> None:
    """Si la sesión ya se autenticó, no hace nada. Si no, muestra un
    formulario de login y `st.stop()` -- ninguna página después de este
    llamado en `streamlit_app.py` llega a renderizar sin pasar por acá."""
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
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")

    st.stop()
