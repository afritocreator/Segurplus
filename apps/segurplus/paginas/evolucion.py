"""Página de evolución: elegir un servicio y dos períodos, ver la
descomposición precio/cantidad, la variación real (deflactada por IPC) y
las alertas -- el resultado central que pidió el proyecto (separar "aumentó
por cantidad" de "aumentó por precio").

Cáscara fina: agrega las filas de la base con core.analisis.agregacion,
descompone con core.analisis.variacion, y arma las alertas con
core.analisis.alertas -- ningún cálculo nuevo vive acá.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from io import BytesIO

import plotly.graph_objects as go
import polars as pl
import requests
import streamlit as st

from apps.segurplus.estilo import aplicar_estilo
from core.almacenamiento import (
    alertas_del_periodo,
    componentes_financieros_periodo,
    conectar,
    contar_borradores,
    recargos_del_periodo,
    sincronizar_casos_alertas,
    totales_por_periodo,
)
from core.analisis.agregacion import (
    PREFIJO_SIN_HOMOLOGAR,
    FilaConcepto,
    acumular_conceptos,
    agregar_conceptos,
    conceptos_con_cantidad_neta_cero,
    conceptos_con_cantidad_neta_negativa,
    etiqueta_legible,
)
from core.analisis.alertas import (
    alertas_por_periodo_faltante,
    etiqueta_tipo,
    generar_alertas,
    ordenar_por_severidad,
)
from core.analisis.real import inflacion_del_periodo, variacion_real
from core.analisis.serie import serie_nominal_y_real
from core.analisis.variacion import (
    ETIQUETA_EFECTO_CANTIDAD,
    ETIQUETA_EFECTO_CRUZADO,
    ETIQUETA_EFECTO_PRECIO,
    descomponer_conceptos,
    efecto_dominante,
    top_conceptos_por_variacion,
)
from core.extraccion.esquema import FacturaExtraida, Recargo
from core.formato import pesos_ars
from core.macro.ipc import leer_ipc
from core.reportes.excel import generar_reporte_excel

st.title("📊 Evolución por servicio")

TOP_N_CONCEPTOS = 12


@st.cache_data(show_spinner="Descargando IPC...")
def _leer_ipc_cacheado() -> pl.DataFrame:
    # docs/auditoria-2026-09.md, hallazgo A-15: sin cachear, cada rerun de
    # esta página (cambiar un selectbox, por ejemplo) podía volver a pegarle
    # a la red si el parquet cacheado en disco no sobrevivió un reinicio del
    # servidor (disco efímero, ver ADR-002). Cache a nivel de app (no en
    # core/, que no depende de Streamlit) -- vive mientras el proceso del
    # servidor esté arriba, se invalida en cada reinicio.
    return leer_ipc()


con = conectar()
servicios = [
    r[0]
    for r in con.execute(
        "SELECT DISTINCT servicio FROM facturas "
        "WHERE servicio IS NOT NULL AND estado = 'aprobada' ORDER BY 1"
    ).fetchall()
]

if not servicios:
    st.info("Todavía no hay facturas cargadas. Empezá por la página **Cargar facturas**.")
    con.close()
    st.stop()

with st.sidebar:
    servicio = st.selectbox("Servicio", servicios)

# D-3: sin esto, un total podía estar incompleto (facturas todavía sin
# confirmar) sin ningún aviso -- la hoja "Cuarentena" que cumplía ese rol
# dejó de recibir filas nuevas desde el plan de confirmación de carga.
borradores_pendientes = contar_borradores(con, servicio=servicio)
if borradores_pendientes:
    st.warning(
        f"Hay {borradores_pendientes} factura(s) de {servicio} esperando confirmación -- "
        'no están incluidas en este análisis. Ver "Confirmar carga".'
    )

filas_periodos = con.execute(
    "SELECT periodo_desde, max(periodo_hasta) FROM facturas "
    "WHERE servicio = ? AND periodo_desde IS NOT NULL AND estado = 'aprobada' "
    "GROUP BY periodo_desde ORDER BY 1",
    [servicio],
).fetchall()
periodos = [fila[0] for fila in filas_periodos]

if len(periodos) < 2:
    st.info(f"Hay menos de dos períodos cargados para {servicio}. Cargá al menos dos meses.")
    con.close()
    st.stop()

try:
    # docs/auditoria-2026-09-facturas-reales.md, hallazgo B-6: se manda también
    # periodo_hasta -- alertas_por_periodo_faltante lo usa para no asumir
    # periodicidad mensual en un servicio bimestral (gas).
    fechas_periodos = [
        (date.fromisoformat(desde), date.fromisoformat(hasta) if hasta else None)
        for desde, hasta in filas_periodos
    ]
except ValueError:
    fechas_periodos = []
    st.caption(
        "No se pudieron calcular alertas de período: hay una fecha guardada con formato inválido."
    )
alertas_periodo_faltante = alertas_por_periodo_faltante(fechas_periodos) if fechas_periodos else []

with st.sidebar:
    periodo_0 = st.selectbox("Período base", periodos[:-1], index=len(periodos) - 2)
    periodos_posteriores = [p for p in periodos if p > periodo_0]
    periodo_1 = st.selectbox(
        "Período de comparación", periodos_posteriores, index=len(periodos_posteriores) - 1
    )


def _filas_del_periodo(periodo: str) -> list[FilaConcepto]:
    filas = con.execute(
        """SELECT c.concepto_normalizado, c.descripcion, c.cantidad, c.importe, c.unidad
           FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
           WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
        [servicio, periodo],
    ).fetchall()
    return [FilaConcepto(cn, desc, cant, imp, unidad) for cn, desc, cant, imp, unidad in filas]


filas_0 = _filas_del_periodo(periodo_0)
filas_1 = _filas_del_periodo(periodo_1)
acumulado_0 = acumular_conceptos(filas_0)
acumulado_1 = acumular_conceptos(filas_1)
agregado_0 = agregar_conceptos(filas_0, acumulado=acumulado_0)
agregado_1 = agregar_conceptos(filas_1, acumulado=acumulado_1)
descomposiciones = descomponer_conceptos(agregado_0, agregado_1)

if any(d.concepto.startswith(PREFIJO_SIN_HOMOLOGAR) for d in descomposiciones):
    try:
        st.page_link(
            "apps/segurplus/paginas/sin_clasificar.py",
            label="Hay conceptos sin clasificar en esta comparación -- ver y agregar alias",
            icon="🏷️",
        )
    except st.errors.StreamlitPageNotFoundError:
        # st.page_link exige que la página esté registrada en st.navigation
        # (streamlit_app.py) -- no pasa en producción (la app real siempre
        # corre por ese entrypoint), pero sí al testear esta página standalone
        # con AppTest.from_file (tests/apps/test_evolucion_app.py).
        st.caption("Hay conceptos sin clasificar en esta comparación -- ver la página homónima.")

# Conceptos cuya cantidad neta dio cero con importe distinto de cero (ver
# core.analisis.agregacion, hallazgo A-20): no pierden plata (ya corregido),
# pero siguen siendo una anomalía real que vale la pena mostrar. Lo mismo
# para cantidad neta NEGATIVA (A-24): la identidad cierra, pero
# alertas_por_salto_de_cantidad invertiría el signo del mensaje si se la
# deja competir con un período de cantidad positiva -- se excluye de esa
# alerta más abajo, igual que las de cantidad cero.
anomalos_0 = conceptos_con_cantidad_neta_cero(
    filas_0, acumulado=acumulado_0
) + conceptos_con_cantidad_neta_negativa(filas_0, acumulado=acumulado_0)
anomalos_1 = conceptos_con_cantidad_neta_cero(
    filas_1, acumulado=acumulado_1
) + conceptos_con_cantidad_neta_negativa(filas_1, acumulado=acumulado_1)
if anomalos_0 or anomalos_1:
    etiquetas = sorted({etiqueta_legible(a) for a in anomalos_0 + anomalos_1})
    st.warning(
        "Cantidad neta cero o negativa con importe distinto de cero (revisar si hay una "
        f"nota de crédito o ajuste sin homologar bien): {', '.join(etiquetas)}"
    )

total_0 = sum(d.total_0 for d in descomposiciones)
total_1 = sum(d.total_1 for d in descomposiciones)

# ipc_periodo_pct alimenta alertas_por_precio_sobre_ipc -- tiene que ser la
# inflación real del período, NUNCA una aproximación que pueda dar negativa
# (ver docs/auditoria-2026-09.md, hallazgo A-1: antes acá se restaban dos
# porcentajes ya calculados y el resultado se aplastaba a 0 con max(),
# dejando la alerta comparando siempre contra 0% de inflación).
ipc_periodo_pct = 0.0
vr = None
try:
    fecha_0 = date.fromisoformat(periodo_0)
    fecha_1 = date.fromisoformat(periodo_1)
except ValueError:
    # docs/auditoria-2026-09.md, hallazgo A-12: antes esto caía en el mismo
    # `except Exception` genérico de más abajo y se mostraba como "sin datos
    # de IPC", un mensaje falso -- el problema acá es un período con formato
    # de fecha inválido (ver A-11: se normaliza al extraer, pero un dato
    # viejo en la base pudo guardarse antes de ese fix), no el IPC.
    st.caption(
        f"No se pudo calcular la variación real: el período '{periodo_0}' o "
        f"'{periodo_1}' no tiene formato de fecha válido (YYYY-MM-DD)."
    )
else:
    try:
        df_ipc = _leer_ipc_cacheado()
        ipc_periodo_pct = inflacion_del_periodo(fecha_0, fecha_1, df_ipc=df_ipc)
        if total_0 != 0:
            vr = variacion_real(total_0, fecha_0, total_1, fecha_1, df_ipc=df_ipc)
    except requests.exceptions.RequestException as exc:
        st.caption(f"No se pudo descargar el IPC (problema de red): {exc}")
    except ValueError as exc:
        # coeficiente_ajuste/variacion_real lanzan ValueError cuando el rango
        # de fechas queda fuera de la serie de IPC disponible, o importe_0
        # es 0 -- acá sí es fiel decir "sin datos de IPC para ese rango".
        st.caption(f"No se pudo calcular la variación real: {exc}")

# Los recargos son por factura individual; se agregan acá para armar la
# alerta sobre el período comparado (ver core.almacenamiento.recargos_del_periodo).
# El chequeo de ítem duplicado (core.analisis.alertas.alertas_por_item_duplicado)
# SÍ corre por factura individual, pero dentro de core.pipeline.procesar_pdf
# en el momento de la carga -- no acá, porque acá solo se ve la factura
# agregada de todo el período, sin los conceptos de cada comprobante por
# separado. Sus resultados quedan persistidos (core.almacenamiento.alertas)
# y se leen con alertas_del_periodo, para combinarlos con las alertas que sí
# necesitan comparar dos períodos (ver docs/auditoria-2026-09.md, A-6).
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
alertas_totales = (
    generar_alertas(
        factura_agregada,
        descomposiciones,
        ipc_periodo_pct=ipc_periodo_pct,
        conceptos_con_cantidad_sintetica=frozenset(anomalos_0 + anomalos_1),
    )
    + alertas_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
    + alertas_periodo_faltante
)
sincronizar_casos_alertas(
    con,
    referencia=f"comparacion:{servicio}:{periodo_0}:{periodo_1}",
    alertas=alertas_totales,
)

with st.container(border=True):
    st.subheader(f"{servicio}: {periodo_0} → {periodo_1}")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Total período base", pesos_ars(total_0))
    col_b.metric(
        "Total período comparado",
        pesos_ars(total_1),
        delta=f"{pesos_ars(total_1 - total_0, signo=True)} (variación nominal)",
    )
    if vr is not None:
        col_c.metric(
            "Variación real (descontado el IPC)",
            f"{vr.variacion_real_pct:+.1%}",
            help="Cuánto cambió el gasto una vez descontada la inflación del período -- "
            "si el precio subió en línea con la inflación, la variación real es 0%.",
        )
    col_d.metric(
        "Inflación del período",
        f"{ipc_periodo_pct:+.1%}",
        help="La variación nominal (arriba) es el cambio en pesos corrientes, sin "
        "descontar esta inflación.",
    )

    # Frase de veredicto: la respuesta literal a la pregunta que motivó el
    # proyecto ("¿aumentó por cantidad o por precio?"), hoy solo deducible
    # mirando el gráfico apilado concepto por concepto.
    tipo_dominante, proporcion_dominante = efecto_dominante(descomposiciones)
    variacion_total_pesos = total_1 - total_0
    cambio_formateado = pesos_ars(variacion_total_pesos, signo=True)
    if tipo_dominante == "precio":
        st.caption(
            f"El cambio de {cambio_formateado} fue mayormente por **PRECIO** "
            f"({abs(proporcion_dominante):.0%})."
        )
    elif tipo_dominante == "cantidad":
        st.caption(
            f"El cambio de {cambio_formateado} fue mayormente por **CANTIDAD** "
            f"({abs(proporcion_dominante):.0%})."
        )
    elif tipo_dominante == "mixto":
        st.caption(
            f"El cambio de {cambio_formateado} fue una **mezcla** de cantidad y "
            "precio -- ningún efecto explica la mayor parte por sí solo."
        )
    else:
        st.caption("No hubo variación nominal entre los períodos seleccionados.")

tab_descomposicion, tab_composicion, tab_serie, tab_alertas, tab_detalle = st.tabs(
    ["Precio vs. cantidad", "Impuestos y total pagable", "Serie histórica", "Alertas", "Detalle"]
)

with tab_descomposicion:
    st.caption(
        "Qué explica el cambio del gasto en cada concepto: ¿subió el precio, subió el "
        "consumo, o los dos a la vez? El **efecto combinado** es la parte que no se puede "
        "atribuir a uno solo -- precio y cantidad cambiaron juntos."
    )
    principales = top_conceptos_por_variacion(descomposiciones, TOP_N_CONCEPTOS)
    if len(principales) < len(descomposiciones):
        st.caption(
            f"Mostrando los {TOP_N_CONCEPTOS} conceptos con mayor variación, de "
            f"{len(descomposiciones)} en total -- el resto está en la pestaña Detalle."
        )
    fig = go.Figure()
    conceptos_orden = [etiqueta_legible(d.concepto) for d in principales]
    fig.add_bar(
        name=ETIQUETA_EFECTO_CANTIDAD, x=conceptos_orden, y=[d.efecto_cantidad for d in principales]
    )
    fig.add_bar(
        name=ETIQUETA_EFECTO_PRECIO, x=conceptos_orden, y=[d.efecto_precio for d in principales]
    )
    fig.add_bar(
        name=ETIQUETA_EFECTO_CRUZADO, x=conceptos_orden, y=[d.efecto_cruzado for d in principales]
    )
    fig.update_layout(barmode="relative", title="Precio vs. cantidad, por concepto")
    aplicar_estilo(fig)
    st.plotly_chart(fig, width="stretch")

with tab_composicion:
    st.caption(
        "El análisis precio/cantidad usa consumos comparables. Esta vista explica además "
        "impuestos, recargos, créditos y el total pagable de cada período."
    )
    componentes_0 = componentes_financieros_periodo(con, servicio=servicio, periodo_desde=periodo_0)
    componentes_1 = componentes_financieros_periodo(con, servicio=servicio, periodo_desde=periodo_1)
    filas_composicion = []
    for componente, etiqueta in [
        ("consumos", "Consumos / abonos"),
        ("impuestos", "Impuestos"),
        ("recargos", "Recargos"),
        ("creditos", "Créditos / descuentos"),
        ("total_pagable", "Total pagable"),
    ]:
        valor_0 = componentes_0[componente]
        valor_1 = componentes_1[componente]
        filas_composicion.append(
            {
                "Componente": etiqueta,
                periodo_0: pesos_ars(valor_0),
                periodo_1: pesos_ars(valor_1),
                "Variación": pesos_ars(valor_1 - valor_0),
            }
        )
    st.dataframe(filas_composicion, width="stretch", hide_index=True)

with tab_serie:
    st.caption(
        "Todos los períodos cargados de este servicio, en pesos nominales y en pesos "
        "constantes de hoy (descontada la inflación) -- para ver la tendencia, no solo la "
        "comparación entre dos meses puntuales."
    )
    try:
        fecha_base = date.fromisoformat(periodos[-1])
        totales_por_fecha = {
            date.fromisoformat(p): total
            for p, total in totales_por_periodo(con, servicio=servicio).items()
        }
        df_ipc_serie = _leer_ipc_cacheado()
        serie = serie_nominal_y_real(totales_por_fecha, fecha_base=fecha_base, df_ipc=df_ipc_serie)
        fig_serie = go.Figure()
        fig_serie.add_scatter(
            x=[p.periodo for p in serie],
            y=[p.total_nominal for p in serie],
            name="Nominal",
            mode="lines+markers",
            line={"dash": "solid"},
        )
        fig_serie.add_scatter(
            x=[p.periodo for p in serie],
            y=[p.total_real for p in serie],
            name=f"Real (pesos de {fecha_base:%m/%Y})",
            mode="lines+markers",
            line={"dash": "dot"},
        )
        fig_serie.update_layout(yaxis_title="Total del período")
        aplicar_estilo(fig_serie)
        st.plotly_chart(fig_serie, width="stretch")
    except requests.exceptions.RequestException as exc:
        st.caption(f"No se pudo descargar el IPC para la serie histórica (problema de red): {exc}")
    except ValueError as exc:
        st.caption(f"No se pudo calcular la serie histórica: {exc}")

with tab_alertas:
    if alertas_totales:
        ordenadas = ordenar_por_severidad(alertas_totales)
        conteo = Counter(a.severidad for a in ordenadas)
        st.caption(
            f"🔴 {conteo['alta']} alta(s) · 🟡 {conteo['media']} media(s) · "
            f"⚪ {conteo['baja']} baja(s)"
        )
        for a in ordenadas:
            texto = f"**{etiqueta_tipo(a.tipo)}**: {a.mensaje}"
            if a.severidad == "alta":
                st.error(texto, icon="🔴")
            elif a.severidad == "media":
                st.warning(texto, icon="🟡")
            else:
                st.info(texto, icon="⚪")
    else:
        st.success("Sin alertas para esta comparación.")

with tab_detalle:
    st.dataframe(
        [
            {
                "Concepto": etiqueta_legible(d.concepto),
                "Cantidad (base)": d.cantidad_0,
                "Precio (base)": d.precio_0,
                "Cantidad (comparado)": d.cantidad_1,
                "Precio (comparado)": d.precio_1,
                ETIQUETA_EFECTO_CANTIDAD: d.efecto_cantidad,
                ETIQUETA_EFECTO_PRECIO: d.efecto_precio,
                "Variación total": d.variacion_total,
            }
            for d in descomposiciones
        ],
        width="stretch",
    )

cuarentena_actual = con.execute("SELECT ruta_pdf, motivos FROM cuarentena").fetchall()

clave_excel = (
    servicio,
    periodo_0,
    periodo_1,
    len(descomposiciones),
    len(alertas_totales),
    borradores_pendientes,
)
if st.button("Preparar reporte en Excel"):
    buffer_excel = BytesIO()
    generar_reporte_excel(
        servicio=servicio,
        periodo_0=periodo_0,
        periodo_1=periodo_1,
        descomposiciones=descomposiciones,
        alertas=alertas_totales,
        cuarentena=cuarentena_actual,
        ruta_salida=buffer_excel,
        borradores_sin_confirmar=borradores_pendientes,
    )
    st.session_state["excel_preparado"] = (clave_excel, buffer_excel.getvalue())
excel_preparado = st.session_state.get("excel_preparado")
if excel_preparado and excel_preparado[0] == clave_excel:
    st.download_button(
        "⬇️ Descargar reporte en Excel",
        data=excel_preparado[1],
        file_name=f"segurplus_{servicio}_{periodo_0}_{periodo_1}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

con.close()
