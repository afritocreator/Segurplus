"""Pantalla de calibración; toda escritura se previsualiza antes de aplicar."""

from __future__ import annotations

import hashlib
from datetime import date

import plotly.graph_objects as go
import requests
import streamlit as st

from apps.segurplus.estilo import DORADO, aplicar_estilo
from core.almacenamiento import (
    conceptos_sin_clasificar,
    conectar,
    filas_sin_clasificar_por_periodo,
    importes_por_periodo,
    motivos_cuarentena_por_proveedor,
)
from core.almacenamiento import (
    metricas_por_proveedor as metricas_por_proveedor_db,
)
from core.analisis.calibracion import (
    metricas_por_proveedor,
    resumir_sin_clasificar,
    total_en_pesos_constantes,
)
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import margen_cerca_del_umbral, quitar_periodo, umbral_coincidencia
from core.formato import pesos_ars
from core.macro.ipc import leer_ipc
from core.rehomologacion import aplicar_cambios, leer_filas_a_rehomologar, recalcular


def _firma_diccionarios(diccionarios: dict[str, dict[str, list[str]]]) -> str:
    """Huella estable: evita aplicar una previsualización contra YAML cambiado."""
    partes = []
    for servicio, conceptos in sorted(diccionarios.items()):
        partes.append(servicio)
        for concepto, aliases in sorted(conceptos.items()):
            partes.append(f"{concepto}:{'|'.join(sorted(aliases))}")
    return hashlib.sha256("\n".join(partes).encode()).hexdigest()


st.title("🏷️ Conceptos sin clasificar")
st.caption(
    "Priorizá aliases por importe. Los scores sin medición se separan para no sesgar el histograma."
)

con = conectar()
try:
    st.subheader("Calidad de lectura por proveedor")
    st.caption(
        "¿La herramienta está leyendo bien a ESTE proveedor? Ordenado por tasa de "
        "cuarentena descendente -- el que más falla, primero."
    )
    metricas = metricas_por_proveedor(metricas_por_proveedor_db(con))
    if not metricas:
        st.info("Todavía no se cargó ninguna factura.")
    else:
        st.dataframe(
            [
                {
                    "Proveedor": m.emisor,
                    "Facturas cargadas": m.facturas_cargadas,
                    "Facturas en cuarentena": m.facturas_en_cuarentena,
                    "Tasa de cuarentena": (
                        f"{m.tasa_cuarentena:.0%}" if m.tasa_cuarentena is not None else "—"
                    ),
                    "Conceptos sin homologar": m.conceptos_sin_homologar,
                    "Tasa sin homologar": (
                        f"{m.tasa_sin_homologar:.0%}" if m.tasa_sin_homologar is not None else "—"
                    ),
                    "Importe sin homologar": pesos_ars(m.importe_sin_homologar),
                }
                for m in metricas
            ],
            width="stretch",
        )
        motivos = motivos_cuarentena_por_proveedor(con)
        if motivos:
            with st.expander("Por qué fue a cuarentena cada proveedor"):
                st.dataframe(
                    [
                        {"Proveedor": emisor, "Motivo": motivo, "Veces": veces}
                        for emisor, motivo, veces in motivos
                    ],
                    width="stretch",
                )
    st.divider()

    filas = conceptos_sin_clasificar(con)
    if not filas:
        st.success("No hay conceptos sin clasificar.")
        st.stop()

    umbral = umbral_coincidencia()
    margen = margen_cerca_del_umbral()
    importe_total = sum(importe for _s, _d, _sc, importe, _v, _u in filas)
    porcentaje_sin_clasificar = None
    try:
        filas_reales = [
            (servicio, descripcion, score, importe, date.fromisoformat(periodo))
            for servicio, descripcion, score, importe, periodo in filas_sin_clasificar_por_periodo(
                con
            )
        ]
        fecha_base = max(fila[4] for fila in filas_reales)
        df_ipc = leer_ipc()
        resumen_real, importe_total = resumir_sin_clasificar(
            filas_reales, fecha_base=fecha_base, df_ipc=df_ipc
        )
        total_real = total_en_pesos_constantes(
            [
                (importe, date.fromisoformat(periodo))
                for importe, periodo in importes_por_periodo(con)
            ],
            fecha_base=fecha_base,
            df_ipc=df_ipc,
        )
        porcentaje_sin_clasificar = importe_total / total_real if total_real else None
        filas = [
            (
                r.servicio,
                r.descripcion,
                r.score,
                r.importe_real,
                r.veces,
                r.ultimo_periodo.isoformat(),
            )
            for r in resumen_real
        ]
    except (ValueError, requests.exceptions.RequestException):
        st.warning(
            "No hay IPC válido para priorizar entre períodos; los importes se muestran nominales."
        )
    con_score = [score for _s, _d, score, _i, _v, _u in filas if score is not None]
    sin_score = len(filas) - len(con_score)
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Importe sin clasificar", pesos_ars(importe_total))
    col_b.metric("Conceptos distintos", len(filas))
    col_c.metric(
        "% del total" if porcentaje_sin_clasificar is not None else "Sin score medido",
        f"{porcentaje_sin_clasificar:.1%}" if porcentaje_sin_clasificar is not None else sin_score,
    )
    if sin_score:
        st.info(
            "Hay conceptos de cargas anteriores sin score: "
            "re-homologalos antes de calibrar el umbral."
        )

    st.dataframe(
        [
            {
                "Servicio": servicio or "(sin servicio)",
                "Descripción": descripcion,
                "Score": round(score, 3) if score is not None else "sin medición",
                "¿Le falta poco?": "⚪ falta medir"
                if score is None
                else "🟡 agregá un alias"
                if score >= umbral - margen
                else "⚪ concepto nuevo",
                "Importe total": pesos_ars(importe),
                "Veces": veces,
                "Último período": ultimo_periodo,
            }
            for servicio, descripcion, score, importe, veces, ultimo_periodo in filas
        ],
        width="stretch",
    )

    servicios_presentes = sorted({s for s, *_ in filas if s})
    if servicios_presentes:
        st.subheader("Snippet para pegar en data/conceptos/<servicio>.yaml")
        servicio_elegido = st.selectbox("Servicio", servicios_presentes)
        aliases = sorted({quitar_periodo(d) for s, d, *_ in filas if s == servicio_elegido})
        st.code("concepto_nuevo:\n" + "\n".join(f"  - {a}" for a in aliases), language="yaml")

    st.subheader("Distribución de scores medidos")
    fig = go.Figure()
    fig.add_histogram(x=con_score, name="Score")
    fig.add_vline(
        x=umbral, line_dash="dash", line_color=DORADO, annotation_text=f"Umbral ({umbral:.2f})"
    )
    fig.update_layout(xaxis_title="Score de similitud", yaxis_title="Cantidad de conceptos")
    st.plotly_chart(aplicar_estilo(fig, formato_moneda=False), width="stretch")

    st.divider()
    st.subheader("Re-homologar ahora")
    if st.button("Previsualizar cambios", type="primary"):
        filas_rehomologar = leer_filas_a_rehomologar(con)
        servicios = {f.servicio for f in filas_rehomologar}
        if None in servicios:
            st.error("No se puede re-homologar: hay filas sin servicio asignado.")
        else:
            diccionarios = {s: cargar_diccionario(s) for s in servicios}
            inseguros = sorted(s for s, d in diccionarios.items() if not d)
            if inseguros:
                st.error(
                    f"No se puede re-homologar: diccionario vacío para {', '.join(inseguros)}."
                )
            else:
                st.session_state["rehomologacion_preview"] = recalcular(
                    filas_rehomologar, diccionarios, umbral=umbral
                )
                st.session_state["rehomologacion_firma"] = _firma_diccionarios(diccionarios)

    preview = st.session_state.get("rehomologacion_preview")
    if preview is not None:
        cambiados = [c for c in preview if c.tipo != "sin_cambio"]
        regresiones = [c for c in cambiados if c.tipo == "regresion"]
        st.info(f"Previsualización: {len(preview)} filas evaluadas; {len(cambiados)} cambios.")
        for c in cambiados:
            empate = (
                f" (empate: {', '.join(c.candidatos_empatados)})" if c.candidatos_empatados else ""
            )
            antes = c.concepto_antes or "(sin clasificar)"
            despues = c.concepto_despues or "(sin clasificar)"
            st.write(f"- **{c.descripcion}**: {antes} → {despues}{empate}")
        if regresiones:
            st.warning(
                f"Hay {len(regresiones)} regresión(es). "
                "Confirmá que fueron intencionales antes de aplicar."
            )
        confirmar = st.checkbox("Entiendo que esta acción modifica la base de facturas.")
        if st.button("Aplicar cambios confirmados", disabled=not confirmar):
            servicios_actuales = {f.servicio for f in leer_filas_a_rehomologar(con)}
            diccionarios_actuales = {s: cargar_diccionario(s) for s in servicios_actuales if s}
            if _firma_diccionarios(diccionarios_actuales) != st.session_state.get(
                "rehomologacion_firma"
            ):
                st.error("El diccionario cambió desde la previsualización. Volvé a previsualizar.")
                del st.session_state["rehomologacion_preview"]
            else:
                tocadas = aplicar_cambios(con, preview)
                st.session_state["rehomologacion_resultado"] = (
                    f"Aplicado: {tocadas} fila(s) actualizada(s)."
                )
                del st.session_state["rehomologacion_preview"]
                st.rerun()
    if resultado := st.session_state.get("rehomologacion_resultado"):
        st.success(resultado)
finally:
    con.close()
