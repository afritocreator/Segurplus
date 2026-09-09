"""Página de evolución: elegir un servicio y dos períodos, ver la
descomposición precio/cantidad, la variación real (deflactada por IPC) y
las alertas -- el resultado central que pidió el proyecto (separar "aumentó
por cantidad" de "aumentó por precio").

Cáscara fina: agrega las filas de la base con core.analisis.agregacion,
descompone con core.analisis.variacion, y arma las alertas con
core.analisis.alertas -- ningún cálculo nuevo vive acá.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

import plotly.graph_objects as go
import streamlit as st

from core.almacenamiento import conectar, recargos_del_periodo
from core.analisis.agregacion import (
    FilaConcepto,
    agregar_conceptos,
    conceptos_con_cantidad_neta_cero,
)
from core.analisis.alertas import generar_alertas
from core.analisis.real import inflacion_del_periodo, variacion_real
from core.analisis.variacion import descomponer_conceptos
from core.extraccion.esquema import FacturaExtraida, Recargo
from core.macro.ipc import leer_ipc
from core.reportes.excel import generar_reporte_excel

st.title("📊 Evolución por servicio")

con = conectar()
servicios = [
    r[0]
    for r in con.execute(
        "SELECT DISTINCT servicio FROM facturas WHERE servicio IS NOT NULL ORDER BY 1"
    ).fetchall()
]

if not servicios:
    st.info("Todavía no hay facturas cargadas. Empezá por la página **Cargar facturas**.")
    con.close()
    st.stop()

servicio = st.selectbox("Servicio", servicios)

periodos = [
    r[0]
    for r in con.execute(
        "SELECT DISTINCT periodo_desde FROM facturas "
        "WHERE servicio = ? AND periodo_desde IS NOT NULL ORDER BY 1",
        [servicio],
    ).fetchall()
]

if len(periodos) < 2:
    st.info(f"Hay menos de dos períodos cargados para {servicio}. Cargá al menos dos meses.")
    con.close()
    st.stop()

col1, col2 = st.columns(2)
periodo_0 = col1.selectbox("Período base", periodos, index=max(0, len(periodos) - 2))
periodo_1 = col2.selectbox("Período de comparación", periodos, index=len(periodos) - 1)


def _filas_del_periodo(periodo: str) -> list[FilaConcepto]:
    filas = con.execute(
        """SELECT c.concepto_normalizado, c.descripcion, c.cantidad, c.importe, c.unidad
           FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ?""",
        [servicio, periodo],
    ).fetchall()
    return [FilaConcepto(cn, desc, cant, imp, unidad) for cn, desc, cant, imp, unidad in filas]


filas_0 = _filas_del_periodo(periodo_0)
filas_1 = _filas_del_periodo(periodo_1)
agregado_0 = agregar_conceptos(filas_0)
agregado_1 = agregar_conceptos(filas_1)
descomposiciones = descomponer_conceptos(agregado_0, agregado_1)

# Conceptos cuya cantidad neta dio cero con importe distinto de cero (ver
# core.analisis.agregacion, hallazgo A-20): no pierden plata (ya corregido),
# pero siguen siendo una anomalía real que vale la pena mostrar.
anomalos_0 = conceptos_con_cantidad_neta_cero(filas_0)
anomalos_1 = conceptos_con_cantidad_neta_cero(filas_1)
if anomalos_0 or anomalos_1:
    st.warning(
        "Cantidad neta cero con importe distinto de cero (revisar si hay una nota de "
        f"crédito o ajuste sin homologar bien): {', '.join(sorted(set(anomalos_0 + anomalos_1)))}"
    )

st.subheader(f"{servicio}: {periodo_0} → {periodo_1}")

total_0 = sum(d.total_0 for d in descomposiciones)
total_1 = sum(d.total_1 for d in descomposiciones)
col_a, col_b, col_c = st.columns(3)
col_a.metric("Total período base", f"${total_0:,.2f}")
col_b.metric(
    "Total período comparado",
    f"${total_1:,.2f}",
    delta=f"{total_1 - total_0:+,.2f} (variación nominal)",
)

# ipc_periodo_pct alimenta alertas_por_precio_sobre_ipc -- tiene que ser la
# inflación real del período, NUNCA una aproximación que pueda dar negativa
# (ver docs/auditoria-2026-09.md, hallazgo A-1: antes acá se restaban dos
# porcentajes ya calculados y el resultado se aplastaba a 0 con max(),
# dejando la alerta comparando siempre contra 0% de inflación).
ipc_periodo_pct = 0.0
try:
    fecha_0 = date.fromisoformat(periodo_0)
    fecha_1 = date.fromisoformat(periodo_1)
    df_ipc = leer_ipc()
    ipc_periodo_pct = inflacion_del_periodo(fecha_0, fecha_1, df_ipc=df_ipc)
    if total_0 != 0:
        vr = variacion_real(total_0, fecha_0, total_1, fecha_1, df_ipc=df_ipc)
        col_c.metric("Variación real (descontado el IPC)", f"{vr.variacion_real_pct:+.1%}")
except Exception:
    st.caption("No se pudo calcular la variación real (sin datos de IPC para ese rango).")

fig = go.Figure()
conceptos_orden = [d.concepto for d in descomposiciones]
fig.add_bar(
    name="Efecto cantidad", x=conceptos_orden, y=[d.efecto_cantidad for d in descomposiciones]
)
fig.add_bar(name="Efecto precio", x=conceptos_orden, y=[d.efecto_precio for d in descomposiciones])
fig.add_bar(
    name="Efecto cruzado", x=conceptos_orden, y=[d.efecto_cruzado for d in descomposiciones]
)
fig.update_layout(barmode="relative", title="Descomposición de la variación por concepto")
st.plotly_chart(fig, use_container_width=True)

st.dataframe(
    [
        {
            "Concepto": d.concepto,
            "Cantidad (base)": d.cantidad_0,
            "Precio (base)": d.precio_0,
            "Cantidad (comparado)": d.cantidad_1,
            "Precio (comparado)": d.precio_1,
            "Efecto cantidad": d.efecto_cantidad,
            "Efecto precio": d.efecto_precio,
            "Variación total": d.variacion_total,
        }
        for d in descomposiciones
    ],
    use_container_width=True,
)

# Los recargos son por factura individual; se agregan acá para armar la
# alerta sobre el período comparado (ver core.almacenamiento.recargos_del_periodo).
# El chequeo de ítem duplicado (core.analisis.alertas.alertas_por_item_duplicado)
# corre por factura individual dentro de core.pipeline.procesar_pdf en su
# momento -- no aplica a esta vista agregada de varios períodos.
recargos_periodo_1 = [
    Recargo(nombre=nombre, importe=importe)
    for nombre, importe in recargos_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
]
factura_agregada = FacturaExtraida(
    emisor=None,
    cuit=None,
    servicio=servicio,
    periodo_desde=periodo_1,
    periodo_hasta=None,
    fecha_emision=None,
    fecha_vencimiento=None,
    numero_comprobante=None,
    moneda="ARS",
    recargos=recargos_periodo_1,
)
alertas_totales = generar_alertas(
    factura_agregada,
    descomposiciones,
    ipc_periodo_pct=ipc_periodo_pct,
    conceptos_con_cantidad_sintetica=frozenset(anomalos_0 + anomalos_1),
)

if alertas_totales:
    st.subheader("⚠️ Alertas")
    for a in alertas_totales:
        icono = {"alta": "🔴", "media": "🟡", "baja": "⚪"}[a.severidad]
        st.write(f"{icono} **{a.tipo}**: {a.mensaje}")
else:
    st.success("Sin alertas para esta comparación.")

cuarentena_actual = con.execute("SELECT ruta_pdf, motivos FROM cuarentena").fetchall()

buffer_excel = BytesIO()
generar_reporte_excel(
    servicio=servicio,
    periodo_0=periodo_0,
    periodo_1=periodo_1,
    descomposiciones=descomposiciones,
    alertas=alertas_totales,
    cuarentena=cuarentena_actual,
    ruta_salida=buffer_excel,
)
st.download_button(
    "⬇️ Descargar reporte en Excel",
    data=buffer_excel,
    file_name=f"segurplus_{servicio}_{periodo_0}_{periodo_1}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

con.close()
