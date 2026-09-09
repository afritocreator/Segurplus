"""Página de cuarentena: facturas que NO cerraron aritméticamente y por eso
nunca entraron al análisis (ver CLAUDE.md, "la regla que separa esto de
confiar en la IA"). Solo lectura -- resolverlas (corregir el dato a mano y
volver a cargar el PDF) queda para una iteración futura del tablero.
"""

from __future__ import annotations

import streamlit as st

from core.almacenamiento import conectar

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
con.close()

if not filas:
    st.success("No hay facturas en cuarentena.")
else:
    st.dataframe(
        [{"Archivo": ruta, "Motivo": motivos} for _hash, ruta, motivos in filas],
        use_container_width=True,
    )
