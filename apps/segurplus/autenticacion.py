"""Gate de acceso de la app: pide una contraseña compartida antes de mostrar
cualquier página. Ver core/autenticacion.py para el porqué y las
limitaciones de este esquema (no es control de acceso real).

Cáscara fina sobre `core.autenticacion.verificar_contrasena`: acá solo vive
la parte de Streamlit (formulario, session_state), la comparación en sí no.
"""

from __future__ import annotations

import streamlit as st

from apps.segurplus.secretos import leer_secret
from core.autenticacion import verificar_contrasena


def requerir_contrasena() -> None:
    """Si la sesión ya se autenticó, no hace nada. Si no, muestra un
    formulario de login y `st.stop()` -- ninguna página después de este
    llamado en `streamlit_app.py` llega a renderizar sin pasar por acá."""
    if st.session_state.get("autenticado"):
        return

    contrasena_esperada = leer_secret("APP_PASSWORD")
    if not contrasena_esperada:
        # Sin contraseña configurada (ej. desarrollo local sin secrets.toml):
        # no bloquear el trabajo local por un secret que no existe todavía.
        st.session_state["autenticado"] = True
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
