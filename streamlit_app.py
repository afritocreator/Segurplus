"""Entrypoint único de la app web de Segurplus.

Vive en la RAÍZ del repo, no en apps/, por el mismo motivo que en
Consultora (ver streamlit_app.py y ADR-004 ahí): Streamlit inserta en
sys.path el directorio del ENTRYPOINT, y Streamlit Community Cloud instala
desde requirements.txt sin hacer `pip install -e .` del repo -- con el
entrypoint en apps/, el deploy fallaría con ModuleNotFoundError: No module
named 'core'.

Es un router: no calcula nada. Todo el contenido vive en
apps/segurplus/paginas/, y toda fórmula vive en core/.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Segurplus", layout="wide", page_icon="🧾")

pagina_cargar = st.Page(
    "apps/segurplus/paginas/cargar.py", title="Cargar facturas", icon="📥", default=True
)
pagina_evolucion = st.Page(
    "apps/segurplus/paginas/evolucion.py", title="Evolución por servicio", icon="📊"
)
pagina_cuarentena = st.Page("apps/segurplus/paginas/cuarentena.py", title="Cuarentena", icon="🧾")

st.navigation([pagina_cargar, pagina_evolucion, pagina_cuarentena], position="top").run()
