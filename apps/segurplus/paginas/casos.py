"""Cola de trabajo para alertas aprobadas; no envía notificaciones todavía."""

from __future__ import annotations

import streamlit as st

from apps.segurplus.autenticacion import requerir_rol
from core.almacenamiento import actualizar_caso_alerta, conectar, listar_casos_alerta

st.title("📌 Casos de alertas")
st.caption("Asigná, documentá y cerrá cada alerta relevante sin perder su evidencia.")
requerir_rol("responsable", "administrador")

con = conectar()
try:
    casos = listar_casos_alerta(con)
    if not casos:
        st.success("No hay casos operativos abiertos.")
        st.stop()
    filas = [
        {
            "Clave": clave,
            "Tipo": tipo,
            "Severidad": severidad,
            "Mensaje": mensaje,
            "Concepto": concepto or "—",
            "Estado": estado,
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
    por_clave = {f"{tipo} · {mensaje[:70]}": clave for clave, tipo, _sev, mensaje, *_ in casos}
    seleccion = st.selectbox("Caso a actualizar", list(por_clave))
    clave = por_clave[seleccion]
    actual = next(caso for caso in casos if caso[0] == clave)
    with st.form(f"caso_{clave}"):
        estado = st.selectbox(
            "Estado",
            ["abierto", "en_analisis", "resuelto", "descartado"],
            index=["abierto", "en_analisis", "resuelto", "descartado"].index(actual[5]),
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
