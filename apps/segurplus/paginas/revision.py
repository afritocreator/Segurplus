"""Revisión humana: aprobar/rechazar lo pendiente, y corregir o rechazar
después una factura que ya quedó aprobada (docs/auditoria-2026-09-piloto.md,
A-50: con revision_humana_obligatoria en false -- el default -- TODA
factura nace aprobada, así que esta segunda vía es la única forma de sacar
del análisis una factura mal leída o corregirle la cabecera)."""

from __future__ import annotations

import streamlit as st

from apps.segurplus.autenticacion import requerir_rol, usuario_actual
from core.almacenamiento import (
    aprobar_pendientes,
    conectar,
    decision_factura,
    listar_facturas_aprobadas,
    listar_facturas_pendientes,
    registrar_correccion,
    resumen_financiero_factura,
)
from core.extraccion.esquema import SERVICIOS_CONOCIDOS
from core.formato import pesos_ars
from core.operacion import revision_humana_obligatoria

CAMPOS_EDITABLES = [
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


def _selector(facturas: list, *, key: str) -> str:
    opciones = {
        f"{emisor or '(sin emisor)'} · {periodo or '(sin período)'} · {hash_pdf[:10]}": hash_pdf
        for hash_pdf, emisor, _servicio, periodo, _total, _evidencia in facturas
    }
    seleccion = st.selectbox("Factura", list(opciones), key=key)
    return opciones[seleccion]


def _mostrar_detalle(con, hash_pdf: str) -> dict:
    fila = con.execute(
        """SELECT emisor, cuit, servicio, periodo_desde, periodo_hasta, fecha_emision,
           fecha_vencimiento, numero_comprobante, moneda, subtotal, total, ruta_evidencia,
           respuesta_extraida FROM facturas WHERE hash_pdf = ?""",
        [hash_pdf],
    ).fetchone()
    datos = dict(
        zip(CAMPOS_EDITABLES + ["subtotal", "total", "evidencia", "respuesta"], fila, strict=True)
    )
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
            for campo in CAMPOS_EDITABLES
        ],
        width="stretch",
        hide_index=True,
    )
    if datos["evidencia"]:
        st.caption(f"Evidencia almacenada: {datos['evidencia']}")
    if datos["respuesta"]:
        with st.expander("JSON original de extracción"):
            st.code(datos["respuesta"], language="json")
    return datos


def _formulario_correccion(con, hash_pdf: str, datos: dict) -> None:
    st.subheader("Corregir cabecera")
    # `campo` FUERA del form (docs/auditoria-2026-09-facturas-reales.md,
    # hallazgo C-3): así elegirlo dispara un rerun inmediato y el widget de
    # "Valor corregido" de abajo puede cambiar de tipo según el campo, antes
    # de que se envíe el formulario.
    campo = st.selectbox("Campo", CAMPOS_EDITABLES, key=f"campo_correccion_{hash_pdf}")
    with st.form(f"correccion_{hash_pdf}"):
        if campo == "servicio":
            # Selector acotado en vez de texto libre: un valor fuera de
            # SERVICIOS_CONOCIDOS se aceptaba en silencio y la factura
            # perdía el diccionario de homologación de su servicio (A-3 por
            # otra vía). registrar_correccion también valida esto -- este
            # selector solo evita el error antes de que haga falta.
            valor_actual = datos[campo] or ""
            indice = (
                SERVICIOS_CONOCIDOS.index(valor_actual)
                if valor_actual in SERVICIOS_CONOCIDOS
                else 0
            )
            valor = st.selectbox("Valor corregido", SERVICIOS_CONOCIDOS, index=indice)
        else:
            valor = st.text_input("Valor corregido", value=str(datos[campo] or ""))
        motivo_correccion = st.text_input("Motivo de corrección")
        corregir = st.form_submit_button("Registrar corrección")
    if corregir:
        if not motivo_correccion.strip():
            st.error("Indicá el motivo para que la corrección sea auditable.")
        else:
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo C-4: antes
            # esto no estaba protegido -- una fecha no interpretable (ej.
            # "julio 2022") tiraba el ValueError de registrar_correccion
            # como traceback encima de la página, tapando el mensaje de
            # ayuda que esa misma función se toma el trabajo de escribir.
            try:
                registrar_correccion(
                    con,
                    hash_pdf=hash_pdf,
                    campo=campo,
                    valor_nuevo=valor or None,
                    motivo=motivo_correccion,
                    actor=usuario_actual(),
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Corrección registrada.")
                st.rerun()


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
            "ya se guardan aprobadas y aparecen directo en Evolución. Usá la pestaña "
            '"Ya aprobadas" para corregir una cabecera mal leída o rechazar una factura '
            "que resultó estar mal, aunque ya haya impactado el análisis."
        )

    pendientes = listar_facturas_pendientes(con)
    aprobadas = listar_facturas_aprobadas(con)

    if not pendientes and not aprobadas:
        st.success("No hay ninguna factura para revisar.")
        st.stop()

    tab_pendientes, tab_aprobadas = st.tabs(
        [f"Pendientes ({len(pendientes)})", f"Ya aprobadas ({len(aprobadas)})"]
    )

    with tab_pendientes:
        if not pendientes:
            st.info("No hay facturas pendientes de revisión.")
        else:
            st.subheader("Aprobar todas las pendientes")
            st.caption(
                f"Hay {len(pendientes)} factura(s) pendiente(s). Aprobarlas en lote registra "
                "el mismo rastro de auditoría (actor, motivo, momento) que una por una."
            )
            with st.form("aprobar_lote"):
                motivo_lote = st.text_input("Motivo para el lote completo")
                aprobar_lote_enviado = st.form_submit_button(
                    "Aprobar todas las pendientes", type="primary"
                )
            if aprobar_lote_enviado:
                if not motivo_lote.strip():
                    st.error("La aprobación en lote también necesita una breve constancia.")
                else:
                    cantidad = aprobar_pendientes(con, actor=usuario_actual(), motivo=motivo_lote)
                    st.success(f"{cantidad} factura(s) aprobada(s).")
                    st.rerun()

            st.divider()
            st.subheader("Revisar de a una")
            hash_pdf = _selector(pendientes, key="selector_pendientes")
            datos = _mostrar_detalle(con, hash_pdf)
            _formulario_correccion(con, hash_pdf, datos)

            col_aprobar, col_rechazar = st.columns(2)
            with col_aprobar:
                motivo_aprobar = st.text_input("Motivo de aprobación", key=f"aprobar_{hash_pdf}")
                if st.button("Aprobar factura", type="primary", key=f"btn_aprobar_{hash_pdf}"):
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
                if st.button("Rechazar factura", key=f"btn_rechazar_{hash_pdf}"):
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

    with tab_aprobadas:
        if not aprobadas:
            st.info("No hay ninguna factura aprobada todavía.")
        else:
            st.caption(
                "Estas facturas YA impactan Evolución, alertas y Excel. Corregir acá cambia "
                "esos números para adelante; rechazar las saca del análisis y borra sus casos "
                "operativos -- el PDF se puede volver a subir después para reprocesarlo."
            )
            hash_pdf = _selector(aprobadas, key="selector_aprobadas")
            datos = _mostrar_detalle(con, hash_pdf)
            _formulario_correccion(con, hash_pdf, datos)

            st.subheader("Rechazar esta factura")
            motivo_rechazar = st.text_input(
                "Motivo de rechazo", key=f"rechazar_aprobada_{hash_pdf}"
            )
            if st.button("Rechazar factura aprobada", key=f"btn_rechazar_aprobada_{hash_pdf}"):
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
                    st.warning(
                        "Factura rechazada: ya no impacta el análisis. Se puede volver a "
                        "subir el mismo PDF más adelante para reprocesarla."
                    )
                    st.rerun()
finally:
    con.close()
