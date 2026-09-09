"""Entrypoint único de la app web de Segurplus.

Vive en la RAÍZ del repo, no en apps/, por el mismo motivo que en
Consultora (ver streamlit_app.py y ADR-004 ahí): Streamlit inserta en
sys.path el directorio del ENTRYPOINT, y Streamlit Community Cloud instala
desde requirements.txt sin hacer `pip install -e .` del repo -- con el
entrypoint en apps/, el deploy fallaría con ModuleNotFoundError: No module
named 'core'.

Es un router: no calcula nada. Todo el contenido vive en
apps/segurplus/paginas/, y toda fórmula vive en core/.

La app se publica como PÚBLICA en Streamlit Community Cloud (el plan
gratuito solo permite una app privada por workspace, y ese lugar ya lo
ocupa Consultora — ver docs/decisiones/ADR-002-deploy.md), así que antes de
mostrar cualquier página se pide una contraseña compartida
(apps/segurplus/autenticacion.py). No es control de acceso real, es una
barrera contra quien encuentra el link por casualidad.
"""

from __future__ import annotations

import streamlit as st

from apps.segurplus.autenticacion import requerir_contrasena

st.set_page_config(page_title="Segurplus", layout="wide", page_icon="🧾")

requerir_contrasena()

pagina_cargar = st.Page(
    "apps/segurplus/paginas/cargar.py", title="Cargar facturas", icon="📥", default=True
)
pagina_evolucion = st.Page(
    "apps/segurplus/paginas/evolucion.py", title="Evolución por servicio", icon="📊"
)
pagina_cuarentena = st.Page("apps/segurplus/paginas/cuarentena.py", title="Cuarentena", icon="🧾")

st.navigation([pagina_cargar, pagina_evolucion, pagina_cuarentena], position="top").run()
