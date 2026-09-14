"""Página de carga de facturas: subir PDFs, correr el pipeline de punta a
punta (core.pipeline.procesar_pdf) y mostrar un resumen de qué se guardó,
qué fue a cuarentena y qué ya estaba procesado.

Cáscara fina (CLAUDE.md): no calcula nada acá, todo pasa por core/.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from apps.segurplus.secretos import leer_secret
from core.almacenamiento import conectar, intentos_gemini_fallidos_recientes
from core.evidencia import evidencia_durable_configurada, persistencia_durable_configurada
from core.pipeline import ResultadoPipeline, procesar_pdf

st.title("📥 Cargar facturas")
st.caption(
    "Subí los PDFs de facturas del mes. Cada una pasa por un control aritmético "
    "antes de entrar al análisis -- si algo no cierra, va a la cola de Cuarentena "
    "en vez de mostrarse como si fuera un dato confiable."
)
if not persistencia_durable_configurada():
    st.info(
        "Modo local de desarrollo: configurá DATABASE_URL (Postgres, ver ADR-003) antes de "
        "usar facturas reales de forma cotidiana. El disco de Streamlit no es una fuente "
        "durable, así que un reinicio del servidor puede perder lo cargado."
    )
elif not evidencia_durable_configurada():
    st.caption(
        "El PDF original se guarda en el disco del servidor (no en un bucket S3): puede "
        "perderse en un reinicio de Streamlit Community Cloud, aunque los datos extraídos "
        "ya están seguros en Postgres. Es idempotente por hash -- volver a subir el mismo "
        "PDF no duplica nada."
    )

api_key = leer_secret("GEMINI_API_KEY")
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
        ruta_temporal = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(archivo.getvalue())
                ruta_temporal = Path(tmp.name)
            # docs/auditoria-2026-09.md, hallazgo A-18: un archivo de ESTE
            # loop no puede tumbar el procesamiento de los demás -- aunque
            # procesar_pdf ya atrapa las excepciones esperables del pipeline
            # (ver core/pipeline.py), esto es una segunda red por si algo
            # imprevisto (disco lleno, permisos) explota acá mismo.
            resultado = procesar_pdf(ruta_temporal, con, api_key=api_key)
        except Exception as exc:  # noqa: BLE001 -- ver comentario arriba
            resultado = ResultadoPipeline(
                Path(archivo.name), hash_pdf="", estado="error_extraccion", detalle=str(exc)
            )
        finally:
            # docs/auditoria-2026-09.md, hallazgo A-9: el PDF subido queda en
            # disco del servidor si no se borra explícitamente -- puede ser
            # información de facturación real de un cliente.
            if ruta_temporal is not None:
                ruta_temporal.unlink(missing_ok=True)
        resultado.ruta = Path(archivo.name)  # mostrar el nombre original, no el temporal
        resultados.append(resultado)
        barra.progress((i + 1) / len(archivos))
    con.close()

    guardadas = [r for r in resultados if r.estado == "guardada"]
    # docs/auditoria-2026-09-piloto.md, hallazgo B-1: NO se cuenta junto con
    # "guardadas" a propósito -- son facturas que se guardaron pero sin
    # `periodo_desde` o `servicio`, así que hoy son invisibles para el
    # análisis. Mostrarlas como éxito sería repetir el mismo engaño que
    # causó el hallazgo.
    necesitan_datos = [r for r in resultados if r.estado == "necesita_datos"]
    cuarentena = [r for r in resultados if r.estado == "cuarentena"]
    repetidas = [r for r in resultados if r.estado == "ya_procesada"]
    errores = [r for r in resultados if r.estado == "error_extraccion"]

    col_g, col_n, col_c, col_r, col_e = st.columns(5)
    col_g.metric("Guardadas", len(guardadas))
    col_n.metric("Faltan datos", len(necesitan_datos))
    col_c.metric("Cuarentena", len(cuarentena))
    col_r.metric("Ya procesadas", len(repetidas))
    col_e.metric("Errores", len(errores))

    if guardadas:
        st.success(f"{len(guardadas)} factura(s) guardadas y validadas.")
    if necesitan_datos:
        st.warning(
            f"{len(necesitan_datos)} factura(s) se guardaron pero les falta un dato "
            "que el análisis necesita para verlas -- no van a aparecer en ningún "
            "gráfico hasta que se completen:"
        )
        for r in necesitan_datos:
            st.write(f"- **{r.ruta.name}**: {r.detalle}")
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

# docs/auditoria-2026-09-piloto.md, hallazgo B-5: antes, el único rastro de
# un fallo de extracción era el mensaje de la corrida actual -- se perdía
# apenas se navegaba a otra pantalla o se recargaba. Este historial vive en
# la base (tabla `intentos_gemini`, ver `core/pipeline.py`), así que sigue
# disponible después. Fuera del `if archivos and st.button(...)` a
# propósito: tiene que verse aunque no se haya subido nada en esta visita.
with st.expander("Últimos intentos de extracción fallidos"):
    st.caption(
        "Si una factura no entra y no queda claro por qué, "
        "`scripts/probar_extraccion.py` corre la misma extracción mostrando "
        "el JSON crudo que devolvió Gemini, sin tocar la base (ver README)."
    )
    con_diag = conectar()
    fallidos = intentos_gemini_fallidos_recientes(con_diag)
    con_diag.close()
    if not fallidos:
        st.write("Ninguno registrado.")
    else:
        for ruta_pdf, mensaje, respuesta_cruda, creado_en in fallidos:
            st.write(f"**{ruta_pdf}** ({creado_en:%Y-%m-%d %H:%M}): {mensaje}")
            if respuesta_cruda:
                st.code(respuesta_cruda, language="json")
