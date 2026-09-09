"""Página de carga de facturas: subir PDFs, correr el pipeline de punta a
punta (core.pipeline.procesar_pdf) y mostrar un resumen de qué se guardó,
qué fue a cuarentena y qué ya estaba procesado.

Cáscara fina (CLAUDE.md): no calcula nada acá, todo pasa por core/.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from core.almacenamiento import conectar
from core.pipeline import procesar_pdf

st.title("📥 Cargar facturas")
st.caption(
    "Subí los PDFs de facturas del mes. Cada una pasa por un control aritmético "
    "antes de entrar al análisis -- si algo no cierra, va a la cola de Cuarentena "
    "en vez de mostrarse como si fuera un dato confiable."
)

api_key = st.secrets.get("GEMINI_API_KEY", None) if hasattr(st, "secrets") else None
if not api_key:
    st.warning(
        "No hay GEMINI_API_KEY configurada (Settings → Secrets en Streamlit Community "
        "Cloud, o `.streamlit/secrets.toml` en local). Sin esto no se puede leer "
        "ninguna factura nueva."
    )

archivos = st.file_uploader("PDFs de facturas", type=["pdf"], accept_multiple_files=True)

if archivos and st.button("Procesar", type="primary", disabled=not api_key):
    con = conectar()
    resultados = []
    barra = st.progress(0.0)
    for i, archivo in enumerate(archivos):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(archivo.getvalue())
            ruta_temporal = Path(tmp.name)
        resultado = procesar_pdf(ruta_temporal, con, api_key=api_key)
        resultado.ruta = Path(archivo.name)  # mostrar el nombre original, no el temporal
        resultados.append(resultado)
        barra.progress((i + 1) / len(archivos))
    con.close()

    guardadas = [r for r in resultados if r.estado == "guardada"]
    cuarentena = [r for r in resultados if r.estado == "cuarentena"]
    repetidas = [r for r in resultados if r.estado == "ya_procesada"]
    errores = [r for r in resultados if r.estado == "error_extraccion"]

    if guardadas:
        st.success(f"{len(guardadas)} factura(s) guardadas y validadas.")
    if repetidas:
        st.info(f"{len(repetidas)} factura(s) ya estaban procesadas (se ignoraron).")
    if cuarentena:
        st.error(
            f"{len(cuarentena)} factura(s) fueron a cuarentena -- no cerraron aritméticamente:"
        )
        for r in cuarentena:
            st.write(f"- **{r.ruta.name}**: {r.detalle}")
    if errores:
        st.warning(f"{len(errores)} factura(s) no se pudieron leer:")
        for r in errores:
            st.write(f"- **{r.ruta.name}**: {r.detalle}")
