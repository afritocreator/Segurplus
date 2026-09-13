"""Revisión humana: una factura validada no afecta análisis hasta aprobarse."""

from __future__ import annotations

import streamlit as st

from apps.segurplus.autenticacion import requerir_rol, usuario_actual
from core.almacenamiento import (
    aprobar_pendientes,
    conectar,
    decision_factura,
    listar_facturas_pendientes,
    registrar_correccion,
    resumen_financiero_factura,
)
from core.formato import pesos_ars
from core.operacion import revision_humana_obligatoria

st.title("✅ Revisar facturas")
requerir_rol("revisor", "responsable", "administrador")

con = conectar()
try:
    if revision_humana_obligatoria():
        st.caption(
            "La revisión humana está OBLIGATORIA (`data/operacion.yaml`): una factura "
            "validada por reglas no impacta Evolución, alertas ni Excel hasta que se "
            "aprueba acá, de a una o en lote."
        )
    else:
        st.caption(
            "La revisión humana está OPCIONAL (`data/operacion.yaml`): las facturas nuevas "
            "ya se guardan aprobadas y aparecen directo en Evolución. Esta pantalla solo "
            "muestra facturas que quedaron pendientes de una carga anterior con la "
            "revisión obligatoria activada, o que fueron rechazadas y corregidas."
        )

    pendientes = listar_facturas_pendientes(con)
    if not pendientes:
        st.success("No hay facturas pendientes de revisión.")
        st.stop()

    st.subheader("Aprobar todas las pendientes")
    st.caption(
        f"Hay {len(pendientes)} factura(s) pendiente(s). Aprobarlas en lote registra el "
        "mismo rastro de auditoría (actor, motivo, momento) que aprobarlas una por una."
    )
    with st.form("aprobar_lote"):
        motivo_lote = st.text_input("Motivo para el lote completo")
        aprobar_lote_enviado = st.form_submit_button("Aprobar todas las pendientes", type="primary")
    if aprobar_lote_enviado:
        if not motivo_lote.strip():
            st.error("La aprobación en lote también necesita una breve constancia.")
        else:
            cantidad = aprobar_pendientes(con, actor=usuario_actual(), motivo=motivo_lote)
            st.success(f"{cantidad} factura(s) aprobada(s).")
            st.rerun()

    st.divider()
    st.subheader("Revisar de a una")
    opciones = {
        f"{emisor or '(sin emisor)'} · {periodo or '(sin período)'} · {hash_pdf[:10]}": hash_pdf
        for hash_pdf, emisor, _servicio, periodo, _total, _evidencia in pendientes
    }
    seleccion = st.selectbox("Factura", list(opciones))
    hash_pdf = opciones[seleccion]
    fila = con.execute(
        """SELECT emisor, cuit, servicio, periodo_desde, periodo_hasta, fecha_emision,
           fecha_vencimiento, numero_comprobante, moneda, subtotal, total, ruta_evidencia,
           respuesta_extraida FROM facturas WHERE hash_pdf = ?""",
        [hash_pdf],
    ).fetchone()
    campos = [
        "emisor",
        "cuit",
        "servicio",
        "periodo_desde",
        "periodo_hasta",
        "fecha_emision",
        "fecha_vencimiento",
        "numero_comprobante",
        "moneda",
    ]
    datos = dict(zip(campos + ["subtotal", "total", "evidencia", "respuesta"], fila, strict=True))
    resumen = resumen_financiero_factura(con, hash_pdf)
    columnas = st.columns(5)
    for columna, (etiqueta, valor) in zip(
        columnas,
        [
            ("Consumo", resumen["consumos"]),
            ("Impuestos", resumen["impuestos"]),
            ("Recargos", resumen["recargos"]),
            ("Créditos", -resumen["creditos"]),
            ("Total pagable", resumen["total_pagable"]),
        ],
        strict=True,
    ):
        columna.metric(etiqueta, pesos_ars(valor))
    st.dataframe(
        [
            {"Campo": campo, "Valor": datos[campo] if datos[campo] is not None else "—"}
            for campo in campos
        ],
        width="stretch",
        hide_index=True,
    )
    if datos["evidencia"]:
        st.caption(f"Evidencia almacenada: {datos['evidencia']}")
    if datos["respuesta"]:
        with st.expander("JSON original de extracción"):
            st.code(datos["respuesta"], language="json")

    st.subheader("Corregir cabecera")
    with st.form(f"correccion_{hash_pdf}"):
        campo = st.selectbox("Campo", campos)
        valor = st.text_input("Valor corregido", value=str(datos[campo] or ""))
        motivo_correccion = st.text_input("Motivo de corrección")
        corregir = st.form_submit_button("Registrar corrección")
    if corregir:
        if not motivo_correccion.strip():
            st.error("Indicá el motivo para que la corrección sea auditable.")
        else:
            registrar_correccion(
                con,
                hash_pdf=hash_pdf,
                campo=campo,
                valor_nuevo=valor or None,
                motivo=motivo_correccion,
                actor=usuario_actual(),
            )
            st.success("Corrección registrada.")
            st.rerun()

    col_aprobar, col_rechazar = st.columns(2)
    with col_aprobar:
        motivo_aprobar = st.text_input("Motivo de aprobación", key=f"aprobar_{hash_pdf}")
        if st.button("Aprobar factura", type="primary"):
            if not motivo_aprobar.strip():
                st.error("La aprobación necesita una breve constancia.")
            else:
                decision_factura(
                    con,
                    hash_pdf=hash_pdf,
                    estado="aprobada",
                    actor=usuario_actual(),
                    motivo=motivo_aprobar,
                )
                st.success("Factura aprobada: ya puede impactar el análisis.")
                st.rerun()
    with col_rechazar:
        motivo_rechazar = st.text_input("Motivo de rechazo", key=f"rechazar_{hash_pdf}")
        if st.button("Rechazar factura"):
            if not motivo_rechazar.strip():
                st.error("El rechazo necesita una explicación para poder resolverlo.")
            else:
                decision_factura(
                    con,
                    hash_pdf=hash_pdf,
                    estado="rechazada",
                    actor=usuario_actual(),
                    motivo=motivo_rechazar,
                )
                st.warning("Factura rechazada: no impactará el análisis.")
                st.rerun()
finally:
    con.close()
