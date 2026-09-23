"""Confirmar carga: cada factura que procesó Gemini queda como un
BORRADOR editable acá, con el PDF original al lado -- nada entra al
análisis hasta que se confirma. Reemplaza el circuito viejo (cuarentena,
"necesita_datos", un mensaje que se perdía) por un único camino, inspirado
en `app/(app)/facturas/[id]/review-grid.tsx` de Klericó: subir deja un
borrador, revisar con el PDF a la vista, confirmar recién cuando la
aritmética cierra y están período y servicio.

Cáscara delgada sobre `core.pipeline.confirmar_factura` (que hace el
control real, redundante a propósito con lo que acá se bloquea en el
botón) y `core.extraccion.validacion.validar_factura` (para el feedback en
vivo mientras se edita).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from apps.segurplus.autenticacion import requerir_rol, usuario_actual
from core.almacenamiento import (
    conectar,
    descartar_borrador,
    guardar_factura,
    leer_borrador,
    listar_borradores,
)
from core.analisis.diccionario import cargar_diccionario
from core.evidencia import leer_pdf
from core.extraccion.esquema import (
    SERVICIOS_CONOCIDOS,
    Credito,
    FacturaExtraida,
    Impuesto,
    Recargo,
    _normalizar_fecha,
    conceptos_desde_filas,
    montos_desde_filas,
)
from core.extraccion.validacion import validar_factura
from core.formato import pesos_ars
from core.ingesta.pdf_texto import total_impreso
from core.pipeline import confirmar_factura

_COLUMNAS_CONCEPTOS = ["descripcion", "cantidad", "unidad", "precio_unitario", "importe"]
_COLUMNAS_MONTO = ["nombre", "importe"]


def _tabla_conceptos_vacia() -> pd.DataFrame:
    return pd.DataFrame(
        [{"descripcion": "", "cantidad": 1.0, "unidad": "", "precio_unitario": 0.0, "importe": 0.0}]
    )


def _tabla_montos_vacia() -> pd.DataFrame:
    return pd.DataFrame([{"nombre": "", "importe": 0.0}])


def _editor_conceptos(datos_previos: list[tuple], *, key: str) -> pd.DataFrame:
    df = pd.DataFrame(datos_previos, columns=_COLUMNAS_CONCEPTOS)
    if df.empty:
        df = _tabla_conceptos_vacia()
    return st.data_editor(
        df,
        num_rows="dynamic",
        width="stretch",
        key=key,
        column_config={
            "descripcion": st.column_config.TextColumn("Descripción", required=True),
            "cantidad": st.column_config.NumberColumn("Cantidad", min_value=0.0),
            "unidad": st.column_config.TextColumn("Unidad"),
            "precio_unitario": st.column_config.NumberColumn("Precio unit.", format="%.2f"),
            "importe": st.column_config.NumberColumn("Importe", format="%.2f"),
        },
    )


def _editor_montos(datos_previos: list[tuple], *, key: str, etiqueta: str) -> pd.DataFrame:
    df = pd.DataFrame(datos_previos, columns=_COLUMNAS_MONTO)
    if df.empty:
        df = _tabla_montos_vacia()
    return st.data_editor(
        df,
        num_rows="dynamic",
        width="stretch",
        key=key,
        column_config={
            "nombre": st.column_config.TextColumn(etiqueta, required=True),
            "importe": st.column_config.NumberColumn("Importe", format="%.2f"),
        },
    )


def _fecha_editable(
    etiqueta: str, valor_actual: str | None, *, key: str, fin_de_mes: bool = False
) -> tuple[str | None, str | None]:
    """Un `text_input` con el mismo formato libre que acepta
    `registrar_correccion` (AAAA-MM-DD, DD/MM/AAAA, MM/AAAA) -- devuelve
    `(valor_normalizado, mensaje_de_error)`. Vacío es válido (el dato
    sigue faltando); algo que no se puede interpretar es un error, no un
    `None` silencioso."""
    crudo = st.text_input(
        etiqueta,
        value=valor_actual or "",
        key=key,
        help="AAAA-MM-DD, DD/MM/AAAA, o MM/AAAA si solo tenés mes y año.",
    ).strip()
    if not crudo:
        return None, None
    normalizado = _normalizar_fecha(crudo, fin_de_mes=fin_de_mes)
    if normalizado is None:
        return None, f'{etiqueta}: no se pudo interpretar "{crudo}".'
    return normalizado, None


def _monto_editable(
    etiqueta: str, valor_actual: float | None, *, key: str
) -> tuple[float | None, str | None]:
    """Igual que `_fecha_editable` pero para subtotal/total -- vacío es
    `None` (el modelo no pudo leerlo, un motivo de falla en sí mismo, ver
    `validar_factura`), no cero."""
    crudo = st.text_input(
        etiqueta, value="" if valor_actual is None else f"{valor_actual:.2f}", key=key
    ).strip()
    if not crudo:
        return None, None
    try:
        return float(crudo.replace(",", ".")), None
    except ValueError:
        return None, f'{etiqueta}: "{crudo}" no es un número válido.'


st.title("🧾 Confirmar carga")
requerir_rol("cargador", "revisor", "responsable", "administrador")

con = conectar()
try:
    borradores = listar_borradores(con)
    if not borradores:
        st.success("No hay facturas esperando confirmación.")
        st.stop()

    opciones = {
        f"{emisor or '(sin emisor)'} · {servicio or '(sin servicio)'} · "
        f"{periodo or '(sin período)'} · {hash_pdf[:10]}": hash_pdf
        for hash_pdf, emisor, servicio, periodo, _total, _evidencia, _motivo in borradores
    }
    seleccion = st.selectbox(f"Borradores por confirmar ({len(borradores)})", list(opciones))
    hash_pdf = opciones[seleccion]

    datos = leer_borrador(con, hash_pdf)

    if datos["motivo_carga"]:
        st.warning(f"No se pudo leer automáticamente: {datos['motivo_carga']}")

    col_pdf, col_form = st.columns([2, 3])

    with col_pdf:
        contenido_pdf = leer_pdf(datos["ruta_evidencia"])
        if contenido_pdf:
            st.pdf(contenido_pdf, height=700)
        elif datos["texto_extraido"]:
            st.caption("No hay PDF disponible para mostrar -- texto extraído del archivo:")
            with st.expander("Texto extraído", expanded=True):
                st.text(datos["texto_extraido"])
        else:
            st.info("No hay PDF ni texto disponible para esta factura.")

    with col_form:
        st.subheader("Cabecera")
        c1, c2 = st.columns(2)
        emisor = (
            c1.text_input("Emisor", value=datos["emisor"] or "", key=f"emisor_{hash_pdf}").strip()
            or None
        )
        cuit = (
            c2.text_input("CUIT", value=datos["cuit"] or "", key=f"cuit_{hash_pdf}").strip() or None
        )

        c3, c4 = st.columns(2)
        opciones_servicio = ["(elegir)", *SERVICIOS_CONOCIDOS]
        indice_servicio = (
            opciones_servicio.index(datos["servicio"])
            if datos["servicio"] in SERVICIOS_CONOCIDOS
            else 0
        )
        servicio_elegido = c3.selectbox(
            "Servicio", opciones_servicio, index=indice_servicio, key=f"servicio_{hash_pdf}"
        )
        servicio = None if servicio_elegido == "(elegir)" else servicio_elegido
        moneda = (
            c4.text_input(
                "Moneda", value=datos["moneda"] or "ARS", key=f"moneda_{hash_pdf}"
            ).strip()
            or "ARS"
        )

        errores_formulario: list[str] = []

        c5, c6 = st.columns(2)
        with c5:
            periodo_desde, err = _fecha_editable(
                "Período desde", datos["periodo_desde"], key=f"periodo_desde_{hash_pdf}"
            )
        if err:
            errores_formulario.append(err)
        with c6:
            # fin_de_mes=True (hallazgo C-1): "MM/AAAA" cubre el mes ENTERO,
            # no su primer día -- si periodo_desde y periodo_hasta fueran
            # los dos al primer día, alertas_por_periodo_faltante dispara
            # un falso "puede faltar un período" en cada mes consecutivo.
            periodo_hasta, err = _fecha_editable(
                "Período hasta",
                datos["periodo_hasta"],
                key=f"periodo_hasta_{hash_pdf}",
                fin_de_mes=True,
            )
        if err:
            errores_formulario.append(err)

        c7, c8 = st.columns(2)
        with c7:
            fecha_emision, err = _fecha_editable(
                "Fecha de emisión", datos["fecha_emision"], key=f"fecha_emision_{hash_pdf}"
            )
        if err:
            errores_formulario.append(err)
        with c8:
            fecha_vencimiento, err = _fecha_editable(
                "Fecha de vencimiento",
                datos["fecha_vencimiento"],
                key=f"fecha_vencimiento_{hash_pdf}",
            )
        if err:
            errores_formulario.append(err)

        numero_comprobante = (
            st.text_input(
                "Número de comprobante",
                value=datos["numero_comprobante"] or "",
                key=f"numero_{hash_pdf}",
            ).strip()
            or None
        )

        st.subheader("Conceptos")
        # docs/auditoria-2026-09-web.md, E-19: `datos["conceptos"]` ahora
        # trae `concepto_sugerido` como sexto campo (lo usa
        # `core.pipeline.confirmar_factura` de respaldo) -- el editor de
        # Streamlit no lo muestra, se recorta acá.
        df_conceptos = _editor_conceptos(
            [fila[:5] for fila in datos["conceptos"]], key=f"conceptos_{hash_pdf}"
        )

        st.subheader("Impuestos")
        df_impuestos = _editor_montos(
            datos["impuestos"], key=f"impuestos_{hash_pdf}", etiqueta="Impuesto"
        )

        st.subheader("Recargos")
        st.caption("Mora, intereses, refacturación -- nunca consumo normal.")
        df_recargos = _editor_montos(
            datos["recargos"], key=f"recargos_{hash_pdf}", etiqueta="Recargo"
        )

        st.subheader("Créditos")
        st.caption("Bonificaciones, descuentos, notas de crédito -- reducen el total.")
        df_creditos = _editor_montos(
            datos["creditos"], key=f"creditos_{hash_pdf}", etiqueta="Crédito"
        )

        c9, c10 = st.columns(2)
        with c9:
            subtotal, err = _monto_editable(
                "Subtotal", datos["subtotal"], key=f"subtotal_{hash_pdf}"
            )
        if err:
            errores_formulario.append(err)
        with c10:
            total, err = _monto_editable("Total", datos["total"], key=f"total_{hash_pdf}")
        if err:
            errores_formulario.append(err)

    factura_editada = FacturaExtraida(
        emisor=emisor,
        cuit=cuit,
        servicio=servicio,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        numero_comprobante=numero_comprobante,
        moneda=moneda,
        conceptos=conceptos_desde_filas(df_conceptos.to_dict("records")),
        impuestos=montos_desde_filas(df_impuestos.to_dict("records"), Impuesto),
        recargos=montos_desde_filas(df_recargos.to_dict("records"), Recargo),
        creditos=montos_desde_filas(df_creditos.to_dict("records"), Credito),
        subtotal=subtotal,
        total=total,
        hash_pdf=hash_pdf,
        ruta_pdf=datos["ruta_pdf"],
        ruta_evidencia=datos["ruta_evidencia"],
        modelo_extraccion=datos["modelo_extraccion"],
        version_prompt=datos["version_prompt"],
        version_esquema=datos["version_esquema"],
        respuesta_extraida=datos["respuesta_extraida"],
    )

    with col_form:
        st.subheader("Control aritmético")
        total_impreso_valor = total_impreso(datos["texto_extraido"] or "")
        resultado = validar_factura(factura_editada, total_impreso=total_impreso_valor)

        if not factura_editada.conceptos:
            st.warning("No hay ningún concepto cargado todavía.")
        for item, concepto in zip(resultado.items, factura_editada.conceptos, strict=True):
            if item.ok:
                continue
            st.error(
                f'Línea {item.indice + 1} -- "{concepto.descripcion}": '
                f"{concepto.cantidad:g} × {pesos_ars(concepto.precio_unitario)} = "
                f"{pesos_ars(item.importe_esperado)}, pero la factura dice "
                f"{pesos_ars(concepto.importe)} "
                f"(difieren {pesos_ars(abs(concepto.importe - item.importe_esperado))})."
            )
        if not resultado.subtotal_presente:
            st.warning("Falta el subtotal.")
        elif not resultado.subtotal_ok:
            st.error(
                f"La suma de los conceptos ({pesos_ars(resultado.suma_conceptos)}) no coincide "
                f"con el subtotal ({pesos_ars(factura_editada.subtotal)})."
            )
        if not resultado.total_presente:
            st.warning("Falta el total.")
        elif not resultado.total_ok:
            st.error(
                "Subtotal + impuestos + recargos - créditos no coincide con el total "
                f"({pesos_ars(factura_editada.total)})."
            )
        if total_impreso_valor is not None and not resultado.total_impreso_ok:
            st.error(f"En el PDF dice un total distinto: {pesos_ars(total_impreso_valor)}.")
        if resultado.factura_valida and factura_editada.conceptos:
            st.success("La aritmética cierra.")

        faltan_datos = []
        if factura_editada.periodo_desde is None:
            faltan_datos.append("período desde")
        if factura_editada.servicio is None:
            faltan_datos.append("servicio")

        puede_confirmar = (
            resultado.factura_valida
            and bool(factura_editada.conceptos)
            and not faltan_datos
            and not errores_formulario
        )

        if not puede_confirmar:
            razones = list(errores_formulario)
            if faltan_datos:
                razones.append(f"falta completar: {', '.join(faltan_datos)}")
            if not factura_editada.conceptos:
                razones.append("no hay ningún concepto cargado")
            elif not resultado.factura_valida:
                razones.extend(resultado.motivos_de_falla())
            st.warning("No se puede confirmar todavía -- " + "; ".join(razones) + ".")

        col_confirmar, col_guardar, col_descartar = st.columns(3)
        with col_confirmar:
            confirmar_clic = st.button(
                "Confirmar factura",
                type="primary",
                disabled=not puede_confirmar,
                key=f"confirmar_{hash_pdf}",
            )
        if confirmar_clic:
            diccionario = cargar_diccionario(servicio) if servicio else None
            estado_final = confirmar_factura(
                con,
                factura_editada,
                diccionario=diccionario,
                total_impreso=total_impreso_valor,
                actor=usuario_actual(),
            )
            if estado_final == "aprobada":
                st.success("Factura confirmada: ya impacta el análisis.")
            else:
                st.success(
                    "Factura confirmada: queda pendiente de revisión humana en "
                    '"Revisar facturas" antes de impactar (data/operacion.yaml).'
                )
            st.rerun()

        with col_guardar:
            # D-10: mitigación acotada a la pérdida de trabajo a mitad de
            # corregir -- todo el estado del formulario vive en
            # st.session_state, así que cerrar la pestaña o que la app se
            # duerma (Streamlit Community Cloud, por inactividad) perdía
            # lo tipeado. No pasa por confirmar_factura ni su validación:
            # es explícitamente "dejalo a medio corregir, seguís después".
            if st.button("Guardar cambios sin confirmar", key=f"guardar_borrador_{hash_pdf}"):
                guardar_factura(
                    con,
                    factura_editada,
                    estado="borrador",
                    texto_extraido=datos["texto_extraido"],
                    motivo_carga=datos["motivo_carga"],
                    actor=usuario_actual(),
                )
                st.success("Cambios guardados -- sigue como borrador.")
                st.rerun()

        with col_descartar:
            with st.popover("Descartar borrador"):
                st.caption(
                    "Borra este borrador por completo. Si Gemini no pudo leerla bien, "
                    "esta es la forma de que la vuelva a intentar: después de descartar, "
                    'subí el mismo PDF de nuevo en "Cargar facturas".'
                )
                if st.button("Sí, descartar", key=f"descartar_{hash_pdf}"):
                    descartar_borrador(con, hash_pdf)
                    st.success("Borrador descartado.")
                    st.rerun()
finally:
    con.close()
