"""Página de cuarentena: facturas que NO cerraron aritméticamente y por eso
nunca entraron al análisis (ver CLAUDE.md, "la regla que separa esto de
confiar en la IA"). Cada fila tiene un botón "Reintentar" (docs/
auditoria-2026-09.md, hallazgo A-17) que solo saca el registro de la cola --
corregir el dato en sí (ajustar el prompt, resubir un PDF corregido) es
trabajo manual fuera del tablero, esto no reprocesa nada automáticamente.
"""

from __future__ import annotations

import streamlit as st

from core.almacenamiento import borrar_de_cuarentena, conectar

st.title("🧾 Cuarentena")
st.caption(
    "Facturas que un modelo leyó pero cuyos números no cerraron (cantidad×precio, "
    "subtotal, total, o el total impreso en el PDF). Ninguna de estas entró al "
    "análisis -- revisalas a mano."
)

con = conectar()
filas = con.execute(
    "SELECT hash_pdf, ruta_pdf, motivos FROM cuarentena ORDER BY ruta_pdf"
).fetchall()

if not filas:
    st.success("No hay facturas en cuarentena.")
else:
    for hash_pdf, ruta, motivos in filas:
        col_info, col_boton = st.columns([5, 1])
        with col_info:
            st.write(f"**{ruta}**")
            st.caption(motivos)
        with col_boton:
            if st.button("Reintentar", key=f"reintentar_{hash_pdf}"):
                borrar_de_cuarentena(con, hash_pdf)
                st.rerun()
        st.divider()

con.close()
