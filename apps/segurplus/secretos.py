"""Lectura segura de `st.secrets`: Streamlit lanza una excepción
(`StreamlitSecretNotFoundError`) en vez de devolver un dict vacío cuando NO
existe ningún `secrets.toml` en absoluto (típico en un clon fresco sin
configurar todavía) -- `hasattr(st, "secrets")` no alcanza para detectarlo,
porque el atributo siempre existe.

Un solo lugar para este manejo, usado tanto por el gate de contraseña
(apps/segurplus/autenticacion.py) como por la página de carga (necesita
GEMINI_API_KEY) -- para no repetir el mismo try/except en cada lugar que
lee un secret.

docs/auditoria-2026-09.md, hallazgo A-8: antes se atrapaba `Exception` a
secas, así que CUALQUIER falla al leer secrets (un `secrets.toml` mal
formado, un permiso de archivo roto, lo que sea) se interpretaba igual que
"no hay contraseña configurada" y el login de la app dejaba pasar. Ahora se
atrapa solo el caso específico y documentado (no existe secrets.toml en
absoluto); cualquier otra excepción se propaga para que quien llama pueda
distinguir "no configurado" de "error real" en vez de fallar abierto.
"""

from __future__ import annotations

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


def leer_secret(clave: str) -> str | None:
    """Valor de `st.secrets[clave]`, o `None` si no existe la clave o no
    hay ningún secrets.toml configurado. Cualquier otro error al leer
    secrets se propaga -- no es un "no configurado", es una falla real."""
    try:
        return st.secrets.get(clave)
    except StreamlitSecretNotFoundError:
        return None
