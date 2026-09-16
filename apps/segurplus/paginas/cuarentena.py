"""Página de cuarentena -- ARCHIVO HISTÓRICO, ya no recibe filas nuevas.

Antes, una factura que no cerraba aritméticamente terminaba acá: un
callejón sin salida, con un único botón "Reintentar" (docs/auditoria-
2026-09.md, hallazgo A-17) que solo sacaba el registro de la cola sin
arreglar nada -- corregir el dato en sí era trabajo manual fuera del
tablero. Desde el plan de confirmación de carga (docs/estado.md),
`core.pipeline.procesar_pdf` ya NO manda nada a la tabla `cuarentena`:
una factura con la aritmética
rota queda como un BORRADOR editable en "Confirmar carga", con el PDF al
lado, en vez de en esta cola.

Esta página se deja de solo lectura mientras pueda quedar algo viejo acá
(de antes de este cambio) -- el botón "Reintentar" se mantiene para
liberar esas filas puntuales. Se puede borrar del todo (y del menú) una
vez que esté confirmado que no queda nada."""

from __future__ import annotations

import streamlit as st

from core.almacenamiento import borrar_de_cuarentena, conectar

st.title("🧾 Cuarentena (archivo histórico)")

con = conectar()
filas = con.execute(
    "SELECT hash_pdf, ruta_pdf, motivos FROM cuarentena ORDER BY ruta_pdf"
).fetchall()

if not filas:
    st.success(
        "No hay nada en cuarentena -- y no debería volver a haber nada: las cargas "
        'nuevas que no cierran aritméticamente quedan como borrador en "Confirmar '
        'carga", no acá.'
    )
else:
    st.caption(
        "Filas de ANTES del plan de confirmación de carga -- las cargas nuevas no "
        'pasan más por acá, van directo a "Confirmar carga" como un borrador editable '
        'con el PDF al lado. "Reintentar" libera el hash para volver a subir el PDF.'
    )
    for hash_pdf, ruta, motivos in filas:
        with st.container(border=True):
            col_info, col_boton = st.columns([5, 1])
            with col_info:
                st.write(f"**{ruta}**")
                # motivos se guarda separado por "\n" (core.almacenamiento.
                # guardar_en_cuarentena, A-59) -- acá se muestra en una
                # sola línea, igual que antes.
                st.caption(motivos.replace("\n", " · "))
            with col_boton:
                if st.button("Reintentar", key=f"reintentar_{hash_pdf}"):
                    borrar_de_cuarentena(con, hash_pdf)
                    st.rerun()

con.close()
