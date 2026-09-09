"""Lectura segura de `st.secrets`: Streamlit lanza una excepción
(`StreamlitSecretNotFoundError`) en vez de devolver un dict vacío cuando NO
existe ningún `secrets.toml` en absoluto (típico en un clon fresco sin
configurar todavía) -- `hasattr(st, "secrets")` no alcanza para detectarlo,
porque el atributo siempre existe.

Un solo lugar para este manejo, usado tanto por el gate de contraseña
(apps/segurplus/autenticacion.py) como por la página de carga (necesita
GEMINI_API_KEY) -- para no repetir el mismo try/except en cada lugar que
lee un secret.
"""

from __future__ import annotations

import streamlit as st


def leer_secret(clave: str) -> str | None:
    """Valor de `st.secrets[clave]`, o `None` si no existe la clave o no
    hay ningún secrets.toml configurado."""
    try:
        return st.secrets.get(clave)
    except Exception:
        return None
