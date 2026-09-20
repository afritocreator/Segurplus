"""App FastAPI de Segurplus -- reemplazo de `apps/segurplus/` (Streamlit),
Bloque 4 del plan de rediseño de septiembre 2026. Tres pantallas: Subir,
Revisar (antes "Confirmar carga") y Ver (antes "Evolución", ahora una sola
pantalla sin pestañas). "Casos" y "Sin clasificar" quedan deliberadamente
fuera de esta primera versión -- son pantallas de administración interna,
no el camino principal que pidió el usuario.

Cáscara fina (mismo criterio que `apps/segurplus/`): ningún cálculo vive
acá, todo pasa por `core/`. Corré con:

    uvicorn web.app:app --reload

Variables de entorno: `APP_PASSWORD` (obligatoria salvo `SEGURPLUS_DEV=1`),
`SECRET_KEY` (firma de la cookie de sesión), `GEMINI_API_KEY` (o lo que
pida `data/extraccion.yaml`), `DATABASE_URL` (Postgres, opcional -- sin
ella usa DuckDB local, igual que el tablero Streamlit)."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from datetime import date
from io import BytesIO
from pathlib import Path

import requests
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.almacenamiento import (
    alertas_del_periodo,
    componentes_financieros_periodo,
    conectar,
    contar_borradores,
    descartar_borrador,
    guardar_factura,
    leer_borrador,
    listar_borradores,
    purgar_todo,
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
from core.analisis.diccionario import cargar_diccionario
from core.analisis.real import inflacion_del_periodo, variacion_real
from core.analisis.serie import serie_nominal_y_real
from core.analisis.variacion import (
    descomponer_conceptos,
    efecto_dominante,
    top_conceptos_por_variacion,
)
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
from core.extraccion.proveedores import RUTA_CONFIGURACION as RUTA_CONFIGURACION_EXTRACCION
from core.extraccion.proveedores import leer_configuracion
from core.extraccion.validacion import validar_factura
from core.formato import pesos_ars
from core.ingesta.pdf_texto import total_impreso
from core.macro.ipc import leer_ipc
from core.pipeline import ResultadoPipeline, confirmar_factura, procesar_pdf
from core.relato import DatosRelato, generar_relato_determinista, redactar_con_modelo
from core.reportes.excel import generar_reporte_excel
from web.auth import NOMBRE_COOKIE, contrasena_configurada, intentar_login, leer_sesion

TOP_N_CONCEPTOS = 12

app = FastAPI(title="Segurplus")
_RAIZ = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=_RAIZ / "static"), name="static")
templates = Jinja2Templates(directory=_RAIZ / "templates")


def _api_key_configurada() -> bool:
    """True si al menos un proveedor de `data/extraccion.yaml` tiene su
    variable de entorno configurada -- o si `GEMINI_API_KEY` sola está
    (compatibilidad con el default de la cascada)."""
    try:
        configuraciones = leer_configuracion(RUTA_CONFIGURACION_EXTRACCION)
    except (OSError, ValueError):
        return bool(os.environ.get("GEMINI_API_KEY"))
    return any(
        c.variable_entorno_clave and os.environ.get(c.variable_entorno_clave)
        for c in configuraciones
    )


def _render(
    request: Request, plantilla: str, contexto: dict, *, pagina_activa: str = ""
) -> HTMLResponse:
    sesion = getattr(request.state, "sesion", None)
    base = {
        "usuario": sesion["usuario"] if sesion else None,
        "pagina_activa": pagina_activa,
        "mensajes": contexto.pop("mensajes", []),
    }
    base.update(contexto)
    return templates.TemplateResponse(request, plantilla, base)


# --- Autenticación -----------------------------------------------------


@app.middleware("http")
async def _gate_de_sesion(request: Request, call_next):
    """Mismo esquema que `apps/segurplus/autenticacion.py`: una contraseña
    compartida, no control de acceso real (ver docstring de
    `core/autenticacion.py`). `SEGURPLUS_DEV=1` salta el login para
    desarrollo local, igual que en el tablero Streamlit."""
    request.state.sesion = leer_sesion(request.cookies.get(NOMBRE_COOKIE))
    es_publica = request.url.path == "/login" or request.url.path.startswith("/static")
    if not es_publica and not request.state.sesion:
        if os.environ.get("SEGURPLUS_DEV") == "1":
            request.state.sesion = {"usuario": "desarrollo-local", "rol": "administrador"}
        else:
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    if request.state.sesion:
        return RedirectResponse("/subir", status_code=303)
    return _render(request, "login.html", {})


@app.post("/login")
def post_login(request: Request, contrasena: str = Form(...)):
    if not contrasena_configurada() and os.environ.get("SEGURPLUS_DEV") != "1":
        return _render(
            request,
            "login.html",
            {
                "mensajes": [
                    (
                        "Falta configurar APP_PASSWORD. Por seguridad, la app no se "
                        "muestra sin contraseña (para desarrollo local, definí "
                        "SEGURPLUS_DEV=1).",
                        "error",
                    )
                ]
            },
        )
    cookie = intentar_login(contrasena)
    if cookie is None:
        return _render(request, "login.html", {"mensajes": [("Contraseña incorrecta.", "error")]})
    respuesta = RedirectResponse("/subir", status_code=303)
    respuesta.set_cookie(NOMBRE_COOKIE, cookie, httponly=True, samesite="lax")
    return respuesta


@app.post("/logout")
def post_logout():
    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.delete_cookie(NOMBRE_COOKIE)
    return respuesta


@app.get("/", response_class=HTMLResponse)
def raiz(request: Request):
    return RedirectResponse("/subir", status_code=303)


# --- Administración --------------------------------------------------------
# Ruta temporal para el primer despliegue en Render: borrar todo lo cargado
# durante las pruebas y arrancar de cero. Protegida por la misma sesión de
# todas las demás rutas (ver `_gate_de_sesion`) más una confirmación escrita
# a mano, porque `purgar_todo` es irreversible. Se saca del código una vez
# usada -- no es una función que la app deba ofrecer de forma permanente.


@app.get("/admin/purgar-todo", response_class=HTMLResponse)
def get_purgar_todo(request: Request):
    return _render(request, "purgar_todo.html", {})


@app.post("/admin/purgar-todo", response_class=HTMLResponse)
def post_purgar_todo(request: Request, confirmacion: str = Form(...)):
    if confirmacion.strip().upper() != "BORRAR":
        return _render(
            request,
            "purgar_todo.html",
            {"mensajes": [("Tenés que escribir BORRAR, tal cual, para confirmar.", "error")]},
        )
    con = conectar()
    purgar_todo(con)
    return _render(
        request,
        "purgar_todo.html",
        {"mensajes": [("Listo, se borró todo. La herramienta arranca de cero.", "ok")]},
    )


# --- Subir ---------------------------------------------------------------


@app.get("/subir", response_class=HTMLResponse)
def get_subir(request: Request):
    return _render(
        request,
        "subir.html",
        {"api_key_configurada": _api_key_configurada()},
        pagina_activa="subir",
    )


@app.post("/subir", response_class=HTMLResponse)
async def post_subir(request: Request, archivos: list[UploadFile]):
    con = conectar()
    resultados: list[ResultadoPipeline] = []
    try:
        for archivo in archivos:
            contenido = await archivo.read()
            ruta_temporal = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(contenido)
                    ruta_temporal = Path(tmp.name)
                resultado = procesar_pdf(ruta_temporal, con)
            except Exception as exc:  # noqa: BLE001 -- un archivo roto no tumba el lote
                resultado = ResultadoPipeline(
                    Path(archivo.filename or "archivo.pdf"),
                    hash_pdf="",
                    estado="error_extraccion",
                    detalle=str(exc),
                )
            finally:
                if ruta_temporal is not None:
                    ruta_temporal.unlink(missing_ok=True)
            resultado.ruta = Path(archivo.filename or "archivo.pdf")
            resultados.append(resultado)
    finally:
        con.close()

    borradores = [r for r in resultados if r.estado == "borrador"]
    repetidas = [r for r in resultados if r.estado == "ya_procesada"]
    errores = [r for r in resultados if r.estado == "error_extraccion"]
    con_problema = [r for r in borradores if r.detalle]

    return _render(
        request,
        "subir.html",
        {
            "api_key_configurada": _api_key_configurada(),
            "resultados": {
                "borradores": borradores,
                "repetidas": repetidas,
                "errores": [{"nombre": r.ruta.name, "detalle": r.detalle} for r in errores],
                "con_problema": [
                    {"nombre": r.ruta.name, "detalle": r.detalle} for r in con_problema
                ],
            },
        },
        pagina_activa="subir",
    )


# --- Revisar ---------------------------------------------------------------

_FILAS_EXTRA_EDITOR = 3  # renglones en blanco al final de cada tabla, para agregar líneas sin JS


def _texto_o_vacio(valor) -> str:
    return "" if valor is None else str(valor)


def _num_o_vacio(valor) -> str:
    return "" if valor is None else f"{valor:g}"


def _filas_con_blancos(filas: list[dict], *, columnas: tuple[str, ...]) -> list[dict]:
    resultado = [dict(f) for f in filas]
    for _ in range(_FILAS_EXTRA_EDITOR):
        resultado.append(dict.fromkeys(columnas, ""))
    return resultado


@app.get("/revisar", response_class=HTMLResponse)
def get_revisar(request: Request):
    con = conectar()
    try:
        filas = listar_borradores(con)
    finally:
        con.close()
    borradores = [
        {
            "hash_pdf": hash_pdf,
            "emisor": emisor,
            "servicio": servicio,
            "periodo_desde": periodo_desde,
            "total_formateado": pesos_ars(total) if total is not None else "—",
        }
        for hash_pdf, emisor, servicio, periodo_desde, total, _evidencia, _motivo in filas
    ]
    return _render(
        request, "revisar_lista.html", {"borradores": borradores}, pagina_activa="revisar"
    )


def _filas_desde_factura(factura: FacturaExtraida) -> dict[str, list[dict]]:
    """Mismas formas que arma `_contexto_detalle` a partir de un borrador
    leído de la base, pero a partir de un `FacturaExtraida` ya armado en
    memoria -- para repoblar el formulario con lo que la persona tipeó
    cuando `confirmar_factura` lo rechaza, en vez de mostrar de nuevo el
    borrador viejo sin la corrección."""
    return {
        "filas_conceptos": _filas_con_blancos(
            [
                {
                    "descripcion": c.descripcion,
                    "cantidad": _num_o_vacio(c.cantidad),
                    "unidad": _texto_o_vacio(c.unidad),
                    "precio_unitario": _num_o_vacio(c.precio_unitario),
                    "importe": _num_o_vacio(c.importe),
                }
                for c in factura.conceptos
            ],
            columnas=("descripcion", "cantidad", "unidad", "precio_unitario", "importe"),
        ),
        "filas_impuestos": _filas_con_blancos(
            [{"nombre": m.nombre, "importe": _num_o_vacio(m.importe)} for m in factura.impuestos],
            columnas=("nombre", "importe"),
        ),
        "filas_recargos": _filas_con_blancos(
            [{"nombre": m.nombre, "importe": _num_o_vacio(m.importe)} for m in factura.recargos],
            columnas=("nombre", "importe"),
        ),
        "filas_creditos": _filas_con_blancos(
            [{"nombre": m.nombre, "importe": _num_o_vacio(m.importe)} for m in factura.creditos],
            columnas=("nombre", "importe"),
        ),
    }


def _contexto_detalle(
    hash_pdf: str, datos: dict, *, errores_aritmetica: list[str], aritmetica_ok: bool
) -> dict:
    filas_conceptos = _filas_con_blancos(
        [
            {
                "descripcion": d,
                "cantidad": _num_o_vacio(c),
                "unidad": _texto_o_vacio(u),
                "precio_unitario": _num_o_vacio(p),
                "importe": _num_o_vacio(i),
            }
            for d, c, u, p, i in datos["conceptos"]
        ],
        columnas=("descripcion", "cantidad", "unidad", "precio_unitario", "importe"),
    )
    filas_monto = lambda clave: _filas_con_blancos(  # noqa: E731
        [{"nombre": n, "importe": _num_o_vacio(i)} for n, i in datos[clave]],
        columnas=("nombre", "importe"),
    )
    return {
        "hash_pdf": hash_pdf,
        "motivo_carga": datos["motivo_carga"],
        "pdf_disponible": bool(leer_pdf(datos["ruta_evidencia"])),
        "texto_extraido": datos["texto_extraido"],
        "servicios_conocidos": SERVICIOS_CONOCIDOS,
        "f": {
            "emisor": datos["emisor"],
            "cuit": datos["cuit"],
            "servicio": datos["servicio"],
            "moneda": datos["moneda"],
            "periodo_desde": datos["periodo_desde"],
            "periodo_hasta": datos["periodo_hasta"],
            "fecha_emision": datos["fecha_emision"],
            "fecha_vencimiento": datos["fecha_vencimiento"],
            "numero_comprobante": datos["numero_comprobante"],
            "subtotal_texto": _num_o_vacio(datos["subtotal"]),
            "total_texto": _num_o_vacio(datos["total"]),
        },
        "filas_conceptos": filas_conceptos,
        "filas_impuestos": filas_monto("impuestos"),
        "filas_recargos": filas_monto("recargos"),
        "filas_creditos": filas_monto("creditos"),
        "errores_aritmetica": errores_aritmetica,
        "aritmetica_ok": aritmetica_ok,
    }


@app.get("/revisar/{hash_pdf}", response_class=HTMLResponse)
def get_revisar_detalle(request: Request, hash_pdf: str):
    con = conectar()
    try:
        datos = leer_borrador(con, hash_pdf)
    finally:
        con.close()
    factura = FacturaExtraida(
        emisor=datos["emisor"],
        cuit=datos["cuit"],
        servicio=datos["servicio"],
        periodo_desde=datos["periodo_desde"],
        periodo_hasta=datos["periodo_hasta"],
        fecha_emision=datos["fecha_emision"],
        fecha_vencimiento=datos["fecha_vencimiento"],
        numero_comprobante=datos["numero_comprobante"],
        moneda=datos["moneda"] or "ARS",
        conceptos=conceptos_desde_filas(
            [
                {"descripcion": d, "cantidad": c, "unidad": u, "precio_unitario": p, "importe": i}
                for d, c, u, p, i in datos["conceptos"]
            ]
        ),
        impuestos=montos_desde_filas(
            [{"nombre": n, "importe": i} for n, i in datos["impuestos"]], Impuesto
        ),
        recargos=montos_desde_filas(
            [{"nombre": n, "importe": i} for n, i in datos["recargos"]], Recargo
        ),
        creditos=montos_desde_filas(
            [{"nombre": n, "importe": i} for n, i in datos["creditos"]], Credito
        ),
        subtotal=datos["subtotal"],
        total=datos["total"],
    )
    total_impreso_valor = total_impreso(datos["texto_extraido"] or "")
    resultado = validar_factura(factura, total_impreso=total_impreso_valor)
    errores = [
        f"Línea {item.indice + 1}: la cantidad × el precio no da el importe de esa línea."
        for item in resultado.items
        if not item.ok
    ]
    if not resultado.subtotal_presente:
        errores.append("Falta el subtotal.")
    elif not resultado.subtotal_ok:
        errores.append("La suma de los conceptos no coincide con el subtotal.")
    if not resultado.total_presente:
        errores.append("Falta el total.")
    elif not resultado.total_ok:
        errores.append("Subtotal + impuestos + recargos - créditos no coincide con el total.")
    if total_impreso_valor is not None and not resultado.total_impreso_ok:
        errores.append(f"En el PDF dice un total distinto: {pesos_ars(total_impreso_valor)}.")

    contexto = _contexto_detalle(
        hash_pdf, datos, errores_aritmetica=errores, aritmetica_ok=resultado.factura_valida
    )
    return _render(request, "revisar_detalle.html", contexto, pagina_activa="revisar")


def _num_desde_texto(texto: str) -> float | None:
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


async def _factura_desde_form(request: Request, hash_pdf: str, datos: dict) -> FacturaExtraida:
    form = await request.form()

    def _campo(nombre: str) -> str | None:
        valor = (form.get(nombre) or "").strip()
        return valor or None

    conceptos_filas = [
        {
            "descripcion": d,
            "cantidad": _num_desde_texto(c),
            "unidad": u,
            "precio_unitario": _num_desde_texto(p),
            "importe": _num_desde_texto(i),
        }
        for d, c, u, p, i in zip(
            form.getlist("concepto_descripcion"),
            form.getlist("concepto_cantidad"),
            form.getlist("concepto_unidad"),
            form.getlist("concepto_precio_unitario"),
            form.getlist("concepto_importe"),
            strict=True,
        )
    ]
    impuestos_filas = [
        {"nombre": n, "importe": _num_desde_texto(i)}
        for n, i in zip(
            form.getlist("impuesto_nombre"), form.getlist("impuesto_importe"), strict=True
        )
    ]
    recargos_filas = [
        {"nombre": n, "importe": _num_desde_texto(i)}
        for n, i in zip(
            form.getlist("recargo_nombre"), form.getlist("recargo_importe"), strict=True
        )
    ]
    creditos_filas = [
        {"nombre": n, "importe": _num_desde_texto(i)}
        for n, i in zip(
            form.getlist("credito_nombre"), form.getlist("credito_importe"), strict=True
        )
    ]

    return FacturaExtraida(
        emisor=_campo("emisor"),
        cuit=_campo("cuit"),
        servicio=_campo("servicio"),
        periodo_desde=_normalizar_fecha(_campo("periodo_desde") or "", fin_de_mes=False),
        periodo_hasta=_normalizar_fecha(_campo("periodo_hasta") or "", fin_de_mes=True),
        fecha_emision=_normalizar_fecha(_campo("fecha_emision") or ""),
        fecha_vencimiento=_normalizar_fecha(_campo("fecha_vencimiento") or ""),
        numero_comprobante=_campo("numero_comprobante"),
        moneda=_campo("moneda") or "ARS",
        conceptos=conceptos_desde_filas(conceptos_filas),
        impuestos=montos_desde_filas(impuestos_filas, Impuesto),
        recargos=montos_desde_filas(recargos_filas, Recargo),
        creditos=montos_desde_filas(creditos_filas, Credito),
        subtotal=_num_desde_texto(form.get("subtotal") or ""),
        total=_num_desde_texto(form.get("total") or ""),
        hash_pdf=hash_pdf,
        ruta_pdf=datos["ruta_pdf"],
        ruta_evidencia=datos["ruta_evidencia"],
        modelo_extraccion=datos["modelo_extraccion"],
        version_prompt=datos["version_prompt"],
        version_esquema=datos["version_esquema"],
        respuesta_extraida=datos["respuesta_extraida"],
    )


@app.post("/revisar/{hash_pdf}/confirmar", response_class=HTMLResponse)
async def post_confirmar(request: Request, hash_pdf: str):
    con = conectar()
    try:
        datos = leer_borrador(con, hash_pdf)
        factura = await _factura_desde_form(request, hash_pdf, datos)
        try:
            diccionario = cargar_diccionario(factura.servicio) if factura.servicio else None
            total_impreso_valor = total_impreso(datos["texto_extraido"] or "")
            estado_final = confirmar_factura(
                con,
                factura,
                diccionario=diccionario,
                total_impreso=total_impreso_valor,
                actor=request.state.sesion["usuario"],
            )
        except ValueError as exc:
            resultado = validar_factura(
                factura, total_impreso=total_impreso(datos["texto_extraido"] or "")
            )
            errores = [str(exc)]
            contexto = _contexto_detalle(
                hash_pdf, datos, errores_aritmetica=errores, aritmetica_ok=resultado.factura_valida
            )
            # Se repuebla el formulario con lo que la persona tipeó, no con
            # lo guardado -- si se perdiera lo editado, corregir un error
            # de tipeo obligaría a empezar de cero.
            contexto["f"].update(
                {
                    "emisor": factura.emisor,
                    "cuit": factura.cuit,
                    "servicio": factura.servicio,
                    "moneda": factura.moneda,
                    "periodo_desde": factura.periodo_desde,
                    "periodo_hasta": factura.periodo_hasta,
                    "fecha_emision": factura.fecha_emision,
                    "fecha_vencimiento": factura.fecha_vencimiento,
                    "numero_comprobante": factura.numero_comprobante,
                    "subtotal_texto": _num_o_vacio(factura.subtotal),
                    "total_texto": _num_o_vacio(factura.total),
                }
            )
            contexto.update(_filas_desde_factura(factura))
            contexto["mensajes"] = [(str(exc), "error")]
            return _render(request, "revisar_detalle.html", contexto, pagina_activa="revisar")
    finally:
        con.close()

    mensaje = (
        "Factura confirmada: ya impacta el análisis."
        if estado_final == "aprobada"
        else "Factura confirmada: queda pendiente de revisión humana antes de impactar."
    )
    respuesta = RedirectResponse("/revisar", status_code=303)
    respuesta.set_cookie("flash", mensaje, max_age=5)
    return respuesta


@app.post("/revisar/{hash_pdf}/guardar")
async def post_guardar(request: Request, hash_pdf: str):
    con = conectar()
    try:
        datos = leer_borrador(con, hash_pdf)
        factura = await _factura_desde_form(request, hash_pdf, datos)
        guardar_factura(
            con,
            factura,
            estado="borrador",
            texto_extraido=datos["texto_extraido"],
            motivo_carga=datos["motivo_carga"],
            actor=request.state.sesion["usuario"],
        )
    finally:
        con.close()
    return RedirectResponse(f"/revisar/{hash_pdf}", status_code=303)


@app.post("/revisar/{hash_pdf}/descartar")
def post_descartar(hash_pdf: str):
    con = conectar()
    try:
        descartar_borrador(con, hash_pdf)
    finally:
        con.close()
    return RedirectResponse("/revisar", status_code=303)


@app.get("/pdf/{hash_pdf}")
def get_pdf(hash_pdf: str):
    con = conectar()
    try:
        datos = leer_borrador(con, hash_pdf)
    finally:
        con.close()
    contenido = leer_pdf(datos["ruta_evidencia"])
    if contenido is None:
        return Response(status_code=404)
    return Response(content=contenido, media_type="application/pdf")


# --- Ver -------------------------------------------------------------------


def _servicios_con_facturas_aprobadas(con) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT servicio FROM facturas "
            "WHERE servicio IS NOT NULL AND estado = 'aprobada' ORDER BY 1"
        ).fetchall()
    ]


def _periodos_del_servicio(con, servicio: str) -> list[tuple[str, str | None]]:
    return con.execute(
        "SELECT periodo_desde, max(periodo_hasta) FROM facturas "
        "WHERE servicio = ? AND periodo_desde IS NOT NULL AND estado = 'aprobada' "
        "GROUP BY periodo_desde ORDER BY 1",
        [servicio],
    ).fetchall()


@app.get("/ver", response_class=HTMLResponse)
def get_ver(
    request: Request,
    servicio: str | None = None,
    periodo_0: str | None = None,
    periodo_1: str | None = None,
):
    con = conectar()
    try:
        servicios = _servicios_con_facturas_aprobadas(con)
        if not servicios:
            return _render(request, "ver_selector.html", {"servicios": []}, pagina_activa="ver")
        servicio_elegido = servicio if servicio in servicios else servicios[0]
        filas_periodos = _periodos_del_servicio(con, servicio_elegido)
        periodos = [f[0] for f in filas_periodos]
        if len(periodos) < 2:
            return _render(
                request,
                "ver_selector.html",
                {"servicios": servicios, "servicio_elegido": servicio_elegido, "periodos": []},
                pagina_activa="ver",
            )
        periodo_0_elegido = periodo_0 if periodo_0 in periodos[:-1] else periodos[-2]
        periodos_posteriores = [p for p in periodos if p > periodo_0_elegido]
        periodo_1_elegido = (
            periodo_1 if periodo_1 in periodos_posteriores else periodos_posteriores[-1]
        )
    finally:
        con.close()
    return _analisis(
        request,
        servicio=servicio_elegido,
        periodo_0=periodo_0_elegido,
        periodo_1=periodo_1_elegido,
        servicios=servicios,
        periodos=periodos,
        periodos_posteriores=periodos_posteriores,
    )


def _analisis(
    request: Request, *, servicio: str, periodo_0: str, periodo_1: str, **contexto_selector
) -> HTMLResponse:
    con = conectar()
    try:
        borradores_pendientes = contar_borradores(con, servicio=servicio)
        filas_periodos = _periodos_del_servicio(con, servicio)
        avisos_calculo: list[str] = []
        try:
            fechas_periodos = [
                (date.fromisoformat(d), date.fromisoformat(h) if h else None)
                for d, h in filas_periodos
            ]
        except ValueError:
            fechas_periodos = []
            avisos_calculo.append(
                "No se pudieron calcular alertas de período: hay una fecha guardada con "
                "formato inválido."
            )
        alertas_periodo_faltante = (
            alertas_por_periodo_faltante(fechas_periodos) if fechas_periodos else []
        )

        def _filas_del_periodo(periodo: str) -> list[FilaConcepto]:
            filas = con.execute(
                """SELECT c.concepto_normalizado, c.descripcion, c.cantidad, c.importe, c.unidad
                   FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
                   WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
                [servicio, periodo],
            ).fetchall()
            return [
                FilaConcepto(cn, desc, cant, imp, unidad) for cn, desc, cant, imp, unidad in filas
            ]

        filas_0 = _filas_del_periodo(periodo_0)
        filas_1 = _filas_del_periodo(periodo_1)
        acumulado_0 = acumular_conceptos(filas_0)
        acumulado_1 = acumular_conceptos(filas_1)
        agregado_0 = agregar_conceptos(filas_0, acumulado=acumulado_0)
        agregado_1 = agregar_conceptos(filas_1, acumulado=acumulado_1)
        descomposiciones = descomponer_conceptos(agregado_0, agregado_1)

        conceptos_sin_clasificar = any(
            d.concepto.startswith(PREFIJO_SIN_HOMOLOGAR) for d in descomposiciones
        )

        anomalos_0 = conceptos_con_cantidad_neta_cero(
            filas_0, acumulado=acumulado_0
        ) + conceptos_con_cantidad_neta_negativa(filas_0, acumulado=acumulado_0)
        anomalos_1 = conceptos_con_cantidad_neta_cero(
            filas_1, acumulado=acumulado_1
        ) + conceptos_con_cantidad_neta_negativa(filas_1, acumulado=acumulado_1)
        anomalos = sorted({etiqueta_legible(a) for a in anomalos_0 + anomalos_1})

        total_0 = sum(d.total_0 for d in descomposiciones)  # consumos, sin impuestos
        total_1 = sum(d.total_1 for d in descomposiciones)

        # Bloque 6 del plan de rediseño de septiembre 2026: el número
        # grande de la pantalla pasa a ser el TOTAL PAGABLE (consumos +
        # impuestos + recargos - créditos), no solo los consumos -- antes
        # `total_0`/`total_1` (arriba) eran el único número mostrado en
        # grande, y esa suma NO es lo que la factura cobra de verdad. Se
        # calcula acá (antes que la variación real e inflación) porque
        # "cuánto cambió en plata real" tiene que hablar del mismo total
        # que se muestra, no de los consumos solos.
        componentes_0 = componentes_financieros_periodo(
            con, servicio=servicio, periodo_desde=periodo_0
        )
        componentes_1 = componentes_financieros_periodo(
            con, servicio=servicio, periodo_desde=periodo_1
        )
        total_pagable_0 = componentes_0["total_pagable"]
        total_pagable_1 = componentes_1["total_pagable"]

        ipc_periodo_pct = None
        vr = None
        try:
            fecha_0 = date.fromisoformat(periodo_0)
            fecha_1 = date.fromisoformat(periodo_1)
            df_ipc = leer_ipc()
            ipc_periodo_pct = inflacion_del_periodo(fecha_0, fecha_1, df_ipc=df_ipc)
            if total_pagable_0 != 0:
                vr = variacion_real(
                    total_pagable_0, fecha_0, total_pagable_1, fecha_1, df_ipc=df_ipc
                )
        except requests.exceptions.RequestException as exc:
            avisos_calculo.append(f"No se pudo descargar el IPC (problema de red): {exc}")
        except ValueError as exc:
            avisos_calculo.append(f"No se pudo calcular la variación real: {exc}")

        recargos_periodo_1 = [
            Recargo(nombre=n, importe=i)
            for n, i in recargos_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
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
                # Sin IPC real no se puede saber si un precio subió "por
                # encima de la inflación" -- 0.0 es el mismo default que ya
                # usaba generar_alertas, explícito acá para no confundirlo
                # con una inflación real de cero (ver "avisos_calculo" de
                # arriba, que ya avisa que el IPC falló).
                ipc_periodo_pct=ipc_periodo_pct if ipc_periodo_pct is not None else 0.0,
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

        tipo_dominante, proporcion_dominante = efecto_dominante(descomposiciones)
        cambio_formateado = pesos_ars(total_1 - total_0, signo=True)
        # Bloque 6 del plan de rediseño: "(73%)" a secas se leía como si
        # $73 de cada $100 de la variación fueran por precio -- en
        # realidad es la fracción del MOVIMIENTO total (incluye el efecto
        # cruzado), no de la variación neta. "del movimiento" lo aclara
        # sin volverse un párrafo aparte.
        proporcion_formateada = f"{abs(proporcion_dominante):.0%} del movimiento"
        if tipo_dominante == "precio":
            veredicto = (
                f"El cambio de {cambio_formateado} en los consumos fue mayormente por "
                f"PRECIO ({proporcion_formateada})."
            )
        elif tipo_dominante == "cantidad":
            veredicto = (
                f"El cambio de {cambio_formateado} en los consumos fue mayormente por "
                f"CANTIDAD ({proporcion_formateada})."
            )
        elif tipo_dominante == "mixto":
            veredicto = (
                f"El cambio de {cambio_formateado} fue una mezcla de cantidad y precio -- "
                "ningún efecto explica la mayor parte por sí solo."
            )
        else:
            veredicto = "No hubo variación nominal entre los períodos seleccionados."

        principales = top_conceptos_por_variacion(descomposiciones, TOP_N_CONCEPTOS)
        if len(principales) < len(descomposiciones):
            avisos_calculo.append(
                f"Mostrando los {TOP_N_CONCEPTOS} conceptos con mayor variación, de "
                f"{len(descomposiciones)} en total -- el resto está en Detalle."
            )

        # Bloque 5 del plan de rediseño: el párrafo en castellano de arriba
        # de todo -- se arma con los MISMOS números que las métricas y la
        # tabla de abajo, nunca un cálculo aparte (ver core/relato.py).
        # Bloque 6: el relato habla del TOTAL PAGABLE ("lo que pagaste"),
        # no de los consumos solos -- son los mismos números que ahora se
        # muestran como número grande, más abajo.
        relato = redactar_con_modelo(
            generar_relato_determinista(
                DatosRelato(
                    servicio=servicio,
                    periodo_0=periodo_0,
                    periodo_1=periodo_1,
                    total_0=total_pagable_0,
                    total_1=total_pagable_1,
                    tipo_dominante=tipo_dominante,
                    proporcion_dominante=abs(proporcion_dominante),
                    variacion_real_pct=vr.variacion_real_pct if vr is not None else None,
                    inflacion_pct=ipc_periodo_pct if ipc_periodo_pct is not None else 0.0,
                    concepto_destacado=principales[0] if principales else None,
                )
            )
        )

        composicion = [
            {
                "etiqueta": etiqueta,
                "valor_0": pesos_ars(componentes_0[clave]),
                "valor_1": pesos_ars(componentes_1[clave]),
                "variacion": pesos_ars(componentes_1[clave] - componentes_0[clave]),
            }
            for clave, etiqueta in [
                ("consumos", "Consumos / abonos"),
                ("impuestos", "Impuestos"),
                ("recargos", "Recargos"),
                ("creditos", "Créditos / descuentos"),
                ("total_pagable", "Total pagable"),
            ]
        ]

        serie = []
        error_serie = None
        try:
            fecha_base = date.fromisoformat(filas_periodos[-1][0])
            totales_por_fecha = {
                date.fromisoformat(p): total
                for p, total in totales_por_periodo(con, servicio=servicio).items()
            }
            df_ipc_serie = leer_ipc()
            puntos = serie_nominal_y_real(
                totales_por_fecha, fecha_base=fecha_base, df_ipc=df_ipc_serie
            )
            serie = [
                {
                    "periodo": p.periodo.isoformat(),
                    "nominal": pesos_ars(p.total_nominal),
                    "real": pesos_ars(p.total_real),
                }
                for p in puntos
            ]
        except requests.exceptions.RequestException as exc:
            error_serie = (
                f"No se pudo descargar el IPC para la serie histórica (problema de red): {exc}"
            )
        except ValueError as exc:
            error_serie = f"No se pudo calcular la serie histórica: {exc}"

        ordenadas = ordenar_por_severidad(alertas_totales)
        conteo = Counter(a.severidad for a in ordenadas)
    finally:
        con.close()

    contexto = {
        "servicio": servicio,
        "periodo_0": periodo_0,
        "periodo_1": periodo_1,
        "borradores_pendientes": borradores_pendientes,
        "avisos_calculo": avisos_calculo,
        "conceptos_sin_clasificar": conceptos_sin_clasificar,
        "anomalos": anomalos,
        # Bloque 6: el número grande es el TOTAL PAGABLE (consumos +
        # impuestos + recargos - créditos) -- antes era solo la suma de
        # consumos, que no es lo que la factura cobra de verdad. Los
        # consumos siguen mostrándose, como referencia secundaria.
        "total_0": pesos_ars(total_pagable_0),
        "total_1": pesos_ars(total_pagable_1),
        "consumos_0": pesos_ars(total_0),
        "consumos_1": pesos_ars(total_1),
        "variacion_nominal": f"{pesos_ars(total_pagable_1 - total_pagable_0, signo=True)} nominal",
        "variacion_real": f"{vr.variacion_real_pct:+.1%}" if vr is not None else None,
        # None (no "+0,0%") cuando el IPC no se pudo descargar/calcular --
        # ver "avisos_calculo" arriba, que ya explica por qué. +0,0% sería
        # indistinguible de una inflación real de cero.
        "inflacion_periodo": f"{ipc_periodo_pct:+.1%}" if ipc_periodo_pct is not None else None,
        "veredicto": veredicto,
        "relato": relato,
        "descomposiciones": [
            {
                "concepto": etiqueta_legible(d.concepto),
                "efecto_cantidad": pesos_ars(d.efecto_cantidad),
                "efecto_precio": pesos_ars(d.efecto_precio),
                "efecto_cruzado": pesos_ars(d.efecto_cruzado),
                "variacion_total": pesos_ars(d.variacion_total),
            }
            for d in principales
        ],
        "composicion": composicion,
        "serie": serie,
        "error_serie": error_serie,
        "alertas": [
            {
                "severidad": a.severidad,
                "tipo_legible": etiqueta_tipo(a.tipo),
                "mensaje": a.mensaje,
            }
            for a in ordenadas
        ],
        "conteo_alertas": {
            "alta": conteo["alta"],
            "media": conteo["media"],
            "baja": conteo["baja"],
        },
        "detalle": [
            {
                "concepto": etiqueta_legible(d.concepto),
                "cantidad_0": f"{d.cantidad_0:g}",
                "precio_0": pesos_ars(d.precio_0),
                "cantidad_1": f"{d.cantidad_1:g}",
                "precio_1": pesos_ars(d.precio_1),
                "efecto_cantidad": pesos_ars(d.efecto_cantidad),
                "efecto_precio": pesos_ars(d.efecto_precio),
                "efecto_cruzado": pesos_ars(d.efecto_cruzado),
                "variacion_total": pesos_ars(d.variacion_total),
            }
            for d in descomposiciones
        ],
    }
    contexto.update(contexto_selector)
    return _render(request, "ver.html", contexto, pagina_activa="ver")


@app.get("/ver/excel")
def get_ver_excel(servicio: str, periodo_0: str, periodo_1: str):
    con = conectar()
    try:
        borradores_pendientes = contar_borradores(con, servicio=servicio)

        def _filas_del_periodo(periodo: str) -> list[FilaConcepto]:
            filas = con.execute(
                """SELECT c.concepto_normalizado, c.descripcion, c.cantidad, c.importe, c.unidad
                   FROM conceptos c JOIN facturas f ON f.hash_pdf = c.hash_pdf
                   WHERE f.servicio = ? AND f.periodo_desde = ? AND f.estado = 'aprobada'""",
                [servicio, periodo],
            ).fetchall()
            return [
                FilaConcepto(cn, desc, cant, imp, unidad) for cn, desc, cant, imp, unidad in filas
            ]

        filas_0 = _filas_del_periodo(periodo_0)
        filas_1 = _filas_del_periodo(periodo_1)
        agregado_0 = agregar_conceptos(filas_0)
        agregado_1 = agregar_conceptos(filas_1)
        descomposiciones = descomponer_conceptos(agregado_0, agregado_1)

        recargos_periodo_1 = [
            Recargo(nombre=n, importe=i)
            for n, i in recargos_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
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
            factura_agregada, descomposiciones, ipc_periodo_pct=0.0
        ) + alertas_del_periodo(con, servicio=servicio, periodo_desde=periodo_1)
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
            borradores_sin_confirmar=borradores_pendientes,
        )
    finally:
        con.close()
    buffer_excel.seek(0)
    nombre = f"segurplus_{servicio}_{periodo_0}_{periodo_1}.xlsx"
    return StreamingResponse(
        buffer_excel,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
