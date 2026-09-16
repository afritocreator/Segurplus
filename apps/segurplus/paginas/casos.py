"""Cola de trabajo para alertas aprobadas; no envía notificaciones todavía."""

from __future__ import annotations

import streamlit as st

from apps.segurplus.autenticacion import requerir_rol
from core.almacenamiento import actualizar_caso_alerta, conectar, listar_casos_alerta
from core.analisis.alertas import etiqueta_tipo

ETIQUETAS_ESTADO_CASO: dict[str, str] = {
    "abierto": "Abierto",
    "en_analisis": "En análisis",
    "resuelto": "Resuelto",
    "descartado": "Descartado",
}

ETIQUETAS_SEVERIDAD: dict[str, str] = {
    "alta": "Alta",
    "media": "Media",
    "baja": "Baja",
}

st.title("📌 Casos de alertas")
st.caption("Asigná, documentá y cerrá cada alerta relevante sin perder su evidencia.")
requerir_rol("responsable", "administrador")

con = conectar()
try:
    casos = listar_casos_alerta(con)
    if not casos:
        st.success(
            "No hay ningún caso todavía. Los casos se generan automáticamente a partir de "
            "las alertas de facturas ya APROBADAS (ítem duplicado, recargos, salto de "
            "cantidad, etc.) -- si esperabas ver alguno, revisá que la factura esté "
            "aprobada en la página **Revisar facturas** (o que la revisión humana esté "
            "desactivada en `data/operacion.yaml`) y que tenga alguna alerta."
        )
        st.stop()
    filas = [
        {
            "Clave": clave,
            "Tipo": etiqueta_tipo(tipo),
            "Severidad": ETIQUETAS_SEVERIDAD.get(severidad, severidad),
            "Mensaje": mensaje,
            "Concepto": concepto or "—",
            "Estado": ETIQUETAS_ESTADO_CASO.get(estado, estado),
            "Responsable": responsable or "—",
            "Vencimiento": vencimiento or "—",
            "Evidencia": evidencia or "—",
        }
        for (
            clave,
            tipo,
            severidad,
            mensaje,
            concepto,
            estado,
            responsable,
            vencimiento,
            evidencia,
        ) in casos
    ]
    st.dataframe(filas, width="stretch", hide_index=True)
    por_clave = {
        f"{etiqueta_tipo(tipo)} · {mensaje[:70]}": clave for clave, tipo, _sev, mensaje, *_ in casos
    }
    seleccion = st.selectbox("Caso a actualizar", list(por_clave))
    clave = por_clave[seleccion]
    actual = next(caso for caso in casos if caso[0] == clave)
    with st.form(f"caso_{clave}"):
        estados_caso = list(ETIQUETAS_ESTADO_CASO)
        estado = st.selectbox(
            "Estado",
            estados_caso,
            index=estados_caso.index(actual[5]),
            format_func=lambda e: ETIQUETAS_ESTADO_CASO[e],
        )
        responsable = st.text_input("Responsable", value=actual[6] or "")
        vencimiento = st.text_input("Vencimiento (YYYY-MM-DD)", value=actual[7] or "")
        evidencia = st.text_area("Evidencia o resolución", value=actual[8] or "")
        guardar = st.form_submit_button("Guardar caso")
    if guardar:
        actualizar_caso_alerta(
            con,
            clave=clave,
            estado=estado,
            responsable=responsable or None,
            vencimiento=vencimiento or None,
            evidencia=evidencia or None,
        )
        st.success("Caso actualizado.")
        st.rerun()
finally:
    con.close()
