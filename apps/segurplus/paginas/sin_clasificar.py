"""Página "Conceptos sin clasificar": el circuito de calibración de la
homologación (docs/auditoria-2026-09.md, Bloque 9 del plan de
correcciones). Muestra qué descripciones de factura NO homologaron a
ningún concepto normalizado, ordenadas por cuánta plata dejan sin
clasificar -- el alias que más conviene agregar a `data/conceptos/*.yaml`
es el que más arriba aparece acá.

Cáscara fina: la consulta vive en `core.almacenamiento.conceptos_sin_
clasificar`, el recálculo en `core.rehomologacion` -- ningún cálculo nuevo
vive en este archivo.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from apps.segurplus.estilo import DORADO, aplicar_estilo
from core.almacenamiento import conceptos_sin_clasificar, conectar
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import quitar_periodo, umbral_coincidencia
from core.rehomologacion import aplicar_cambios, leer_filas_a_rehomologar, recalcular

st.title("🏷️ Conceptos sin clasificar")
st.caption(
    "Descripciones de factura que ningún alias de data/conceptos/*.yaml reconoció todavía "
    "-- ordenadas por importe, porque el alias que más conviene agregar es el que más "
    "plata deja sin clasificar."
)

con = conectar()
filas = conceptos_sin_clasificar(con)

if not filas:
    st.success("No hay conceptos sin clasificar.")
    con.close()
    st.stop()

umbral = umbral_coincidencia()
importe_total_sin_clasificar = sum(importe for _s, _d, _sc, importe, _v, _u in filas)

col_a, col_b = st.columns(2)
col_a.metric("Importe sin clasificar", f"${importe_total_sin_clasificar:,.2f}")
col_b.metric("Conceptos distintos", len(filas))

st.dataframe(
    [
        {
            "Servicio": servicio or "(sin servicio)",
            "Descripción": descripcion,
            "Score": round(score, 3),
            "¿Le falta poco?": "🟡 agregá un alias"
            if score >= umbral - 0.15
            else "⚪ concepto nuevo",
            "Importe total": importe,
            "Veces": veces,
            "Último período": ultimo_periodo,
        }
        for servicio, descripcion, score, importe, veces, ultimo_periodo in filas
    ],
    use_container_width=True,
)

st.subheader("Snippet para pegar en data/conceptos/<servicio>.yaml")
servicios_presentes = sorted({s for s, *_ in filas if s})
servicio_elegido = st.selectbox("Servicio", servicios_presentes) if servicios_presentes else None
if servicio_elegido:
    descripciones_del_servicio = [d for s, d, *_ in filas if s == servicio_elegido]
    alias_sugeridos = sorted({quitar_periodo(d) for d in descripciones_del_servicio})
    snippet = "concepto_nuevo:\n" + "\n".join(f"  - {a}" for a in alias_sugeridos)
    st.code(snippet, language="yaml")

st.subheader("Distribución de scores")
fig = go.Figure()
fig.add_histogram(x=[score for _s, _d, score, _i, _v, _u in filas], name="Score")
fig.add_vline(
    x=umbral, line_dash="dash", line_color=DORADO, annotation_text=f"Umbral ({umbral:.2f})"
)
fig.update_layout(xaxis_title="Score de similitud", yaxis_title="Cantidad de conceptos")
aplicar_estilo(fig, formato_moneda=False)
st.plotly_chart(fig, use_container_width=True)

st.divider()
st.subheader("Re-homologar ahora")
st.caption(
    "Recalcula la homologación de TODO lo ya guardado con el diccionario actual -- sin "
    "llamar a Gemini ni gastar la cuota de llamadas por hora."
)
if st.button("Re-homologar ahora", type="primary"):
    filas_a_rehomologar = leer_filas_a_rehomologar(con)
    servicios = {f.servicio for f in filas_a_rehomologar}
    diccionarios = {s: cargar_diccionario(s) for s in servicios}
    cambios = recalcular(filas_a_rehomologar, diccionarios)
    cambiados = [c for c in cambios if c.tipo != "sin_cambio"]

    if not cambiados:
        st.info("Nada cambió -- el diccionario actual da los mismos resultados que antes.")
    else:
        aplicar_cambios(con, cambios)
        regresiones = [c for c in cambiados if c.tipo == "regresion"]
        if regresiones:
            st.warning(
                f"{len(regresiones)} concepto(s) que antes homologaban ahora NO lo hacen "
                "-- revisar si un alias nuevo le robó el match a otro concepto:"
            )
            for c in regresiones:
                st.write(f"- **{c.descripcion}**: {c.concepto_antes} → sin clasificar")
        nuevos_o_cambiados = [c for c in cambiados if c.tipo in ("nuevo", "cambio")]
        if nuevos_o_cambiados:
            st.success(f"{len(nuevos_o_cambiados)} concepto(s) actualizados:")
            for c in nuevos_o_cambiados:
                antes = c.concepto_antes or "(sin clasificar)"
                st.write(f"- **{c.descripcion}**: {antes} → {c.concepto_despues}")
        st.rerun()

con.close()
