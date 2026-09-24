"""App FastAPI de Segurplus -- reemplazo de `apps/segurplus/` (Streamlit).

El circuito principal es Subir, Revisar y Ver; la cola de Casos agrega el
seguimiento operativo de las alertas sin escribir al abrir la pantalla.

Cáscara fina (mismo criterio que `apps/segurplus/`): ningún cálculo vive
acá, todo pasa por `core/`. Corré con:

    uvicorn web.app:app --reload

Producción requiere `DATABASE_URL`, `SECRET_KEY`, credenciales de Google OIDC
y bucket de evidencia. `APP_PASSWORD` solo persiste en desarrollo local."""

from __future__ import annotations

import os
import secrets
import tempfile
import time
from collections import Counter
from datetime import date
from io import BytesIO
from pathlib import Path

import requests
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from core.almacenamiento import (
    actualizar_caso_alerta,
    componentes_financieros_periodo,
    conectar,
    contar_borradores,
    descartar_borrador,
    filas_sin_clasificar_por_periodo,
    guardar_factura,
    historial_caso,
    importes_por_periodo,
    leer_borrador,
    listar_borradores,
    listar_casos_alerta,
    totales_pagables_por_periodo,
)
from core.analisis.agregacion import etiqueta_legible
from core.analisis.alertas import etiqueta_tipo, ordenar_por_severidad
from core.analisis.calibracion import resumir_sin_clasificar, total_en_pesos_constantes
from core.analisis.diccionario import cargar_diccionario
from core.analisis.serie import serie_nominal_y_real
from core.analisis.variacion import top_conceptos_por_variacion
from core.evidencia import LIMITE_BORRADORES_BUCKET_BYTES, leer_pdf, uso_evidencia_bytes
from core.extraccion.esquema import (
    SERVICIOS_CONOCIDOS,
    Concepto,
    Credito,
    FacturaExtraida,
    Impuesto,
    Recargo,
    _normalizar_fecha,
    conceptos_desde_filas,
    montos_desde_filas,
)
from core.extraccion.validacion import validar_factura
from core.formato import mes_anio, nombre_servicio, pesos_ars
from core.ingesta.pdf_texto import total_impreso
from core.macro.ipc import leer_ipc
from core.pipeline import ResultadoPipeline, confirmar_factura, procesar_pdf, procesar_pdf_manual
from core.relato import DatosRelato, generar_relato_determinista
from core.reportes.excel import generar_reporte_excel
from web.auth import (
    NOMBRE_COOKIE,
    cliente_google,
    contrasena_configurada,
    correo_autorizado,
    crear_cookie_sesion,
    crear_token_csrf,
    google_configurado,
    intentar_login,
    leer_sesion,
    secret_key_configurada,
    verificar_token_csrf,
)
from web.comparacion import calcular_comparacion

TOP_N_CONCEPTOS = 12
# docs/auditoria-2026-09-web.md, E-4/E-3 del plan de arreglos: sin
# EVIDENCIA_DIR ni S3_BUCKET, el PDF se guarda en la base (base64, en una
# columna VARCHAR) -- un PDF escaneado sin tope llenaría el plan gratis de
# Supabase (500 MB) en pocas facturas grandes.
_TAMANIO_MAXIMO_PDF_BYTES = 10 * 1024 * 1024
# docs/auditoria-2026-09-web.md, E-8: subir muchas facturas juntas es una
# sola request de varios minutos (cada una tarda 15-35s contra Gemini,
# según el banco de medición), expuesta al corte del proxy de Render --
# mejor pedir subir de a tandas que arriesgar perder el lote entero.
_MAXIMO_ARCHIVOS_POR_SUBIDA = 10

app = FastAPI(title="Segurplus")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32),
    same_site="lax",
    https_only=os.environ.get("SEGURPLUS_PRODUCTION") == "1",
    max_age=600,
)
_RAIZ = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=_RAIZ / "static"), name="static")
templates = Jinja2Templates(directory=_RAIZ / "templates")
templates.env.filters["pesos_ars"] = pesos_ars


def _api_key_configurada() -> bool:
    """True si `GEMINI_API_KEY` está configurada -- es la ÚNICA clave que
    usa `core.pipeline.procesar_pdf` (la cascada de `data/extraccion.yaml`
    con otros proveedores todavía no está conectada al pipeline real, ver
    su propio comentario). Antes esta función consideraba "configurado"
    cualquier proveedor de la cascada, así que con solo `GROQ_API_KEY` el
    botón de Subir se habilitaba y cada factura fallaba igual
    (docs/auditoria-2026-09-web.md, E-9)."""
    return bool(os.environ.get("GEMINI_API_KEY"))


def _render(
    request: Request, plantilla: str, contexto: dict, *, pagina_activa: str = ""
) -> HTMLResponse:
    sesion = getattr(request.state, "sesion", None)
    base = {
        "usuario": sesion["usuario"] if sesion else None,
        "google_configurado": google_configurado(),
        "login_local": os.environ.get("SEGURPLUS_PRODUCTION") != "1",
        "csrf_token": (
            crear_token_csrf(request.cookies[NOMBRE_COOKIE])
            if sesion and NOMBRE_COOKIE in request.cookies and secret_key_configurada()
            else ""
        ),
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
    if request.state.sesion and request.state.sesion.get("rol") != "administrador":
        request.state.sesion = None
    es_publica = request.url.path in {
        "/login", "/login/google", "/auth/google"
    } or request.url.path.startswith("/static")
    if not es_publica and not request.state.sesion:
        if os.environ.get("SEGURPLUS_DEV") == "1" and os.environ.get("SEGURPLUS_PRODUCTION") != "1":
            request.state.sesion = {"usuario": "desarrollo-local", "rol": "administrador"}
        else:
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# docs/auditoria-2026-09-web.md, E-18: 5 intentos fallidos cada 15 minutos
# por IP, en memoria -- alcanza porque el plan gratis de Render corre un
# solo proceso. Se pierde si el proceso se reinicia (no es defensa contra
# un atacante con muchas IP, es contra probar contraseñas a mano o con un
# script simple).
_VENTANA_INTENTOS_SEGUNDOS = 15 * 60
_MAXIMO_INTENTOS_FALLIDOS = 5
_intentos_fallidos_por_ip: dict[str, list[float]] = {}


def _intentos_recientes(ip: str) -> list[float]:
    ahora = time.monotonic()
    intentos = [
        t for t in _intentos_fallidos_por_ip.get(ip, []) if ahora - t < _VENTANA_INTENTOS_SEGUNDOS
    ]
    _intentos_fallidos_por_ip[ip] = intentos
    return intentos


@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    if request.state.sesion:
        return RedirectResponse("/subir", status_code=303)
    return _render(
        request,
        "login.html",
        {
            "google_configurado": google_configurado(),
            "login_local": os.environ.get("SEGURPLUS_PRODUCTION") != "1",
        },
    )


@app.get("/login/google")
async def login_google(request: Request):
    if not google_configurado():
        return Response("Google OIDC no está configurado.", status_code=503)
    cliente = cliente_google()
    return await cliente.authorize_redirect(request, os.environ["GOOGLE_REDIRECT_URI"])


@app.get("/auth/google")
async def auth_google(request: Request):
    if not google_configurado():
        return Response("Google OIDC no está configurado.", status_code=503)
    try:
        token = await cliente_google().authorize_access_token(request)
        usuario = token["userinfo"]
    except Exception:  # noqa: BLE001 -- no exponer detalles del proveedor ni tokens
        return Response("No se pudo verificar el inicio de sesión.", status_code=401)
    correo = usuario.get("email")
    if not usuario.get("sub") or not correo_autorizado(
        correo, verificado=usuario.get("email_verified") is True
    ):
        return Response("Cuenta no autorizada.", status_code=403)
    respuesta = RedirectResponse("/subir", status_code=303)
    respuesta.set_cookie(
        NOMBRE_COOKIE,
        crear_cookie_sesion(usuario=correo, rol="administrador", subject=usuario["sub"]),
        httponly=True,
        samesite="lax",
        secure=os.environ.get("SEGURPLUS_PRODUCTION") == "1",
        max_age=4 * 60 * 60,
    )
    return respuesta


@app.post("/login")
def post_login(request: Request, contrasena: str = Form(...)):
    if os.environ.get("SEGURPLUS_PRODUCTION") == "1":
        return Response("Usá Google para iniciar sesión.", status_code=403)
    falta = []
    if not contrasena_configurada() and os.environ.get("SEGURPLUS_DEV") != "1":
        falta.append("APP_PASSWORD")
    if not secret_key_configurada() and os.environ.get("SEGURPLUS_DEV") != "1":
        falta.append("SECRET_KEY")
    if falta:
        return _render(
            request,
            "login.html",
            {
                "mensajes": [
                    (
                        f"Falta configurar {' y '.join(falta)}. Por seguridad, la app no "
                        "se muestra sin eso (para desarrollo local, definí "
                        "SEGURPLUS_DEV=1).",
                        "error",
                    )
                ]
            },
        )

    ip = request.client.host if request.client else "desconocida"
    if len(_intentos_recientes(ip)) >= _MAXIMO_INTENTOS_FALLIDOS:
        return _render(
            request,
            "login.html",
            {
                "mensajes": [
                    (
                        "Demasiados intentos fallidos. Esperá unos minutos antes de "
                        "volver a probar.",
                        "error",
                    )
                ]
            },
        )

    cookie = intentar_login(contrasena)
    if cookie is None:
        _intentos_fallidos_por_ip.setdefault(ip, []).append(time.monotonic())
        return _render(request, "login.html", {"mensajes": [("Contraseña incorrecta.", "error")]})
    _intentos_fallidos_por_ip.pop(ip, None)
    respuesta = RedirectResponse("/subir", status_code=303)
    # docs/auditoria-2026-09-web.md, E-18: `secure=True` cuando la request
    # llegó por HTTPS -- detrás del proxy de Render, `request.url.scheme`
    # solo refleja eso si uvicorn arranca con `--proxy-headers` (ver
    # render.yaml). En desarrollo local (HTTP puro) `secure=False`, si no
    # el navegador nunca mandaría la cookie de vuelta.
    respuesta.set_cookie(
        NOMBRE_COOKIE,
        cookie,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return respuesta


def _exigir_csrf(request: Request, token: str) -> Response | None:
    if os.environ.get("SEGURPLUS_PRODUCTION") != "1":
        return None
    if verificar_token_csrf(request.cookies.get(NOMBRE_COOKIE), token):
        return None
    return Response("Solicitud inválida: token CSRF ausente o vencido.", status_code=403)


@app.post("/logout")
def post_logout(request: Request, csrf: str = Form("")):
    if error := _exigir_csrf(request, csrf):
        return error
    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.delete_cookie(NOMBRE_COOKIE)
    return respuesta


@app.get("/", response_class=HTMLResponse)
def raiz(request: Request):
    return RedirectResponse("/subir", status_code=303)


@app.get("/casos", response_class=HTMLResponse)
def get_casos(request: Request, aviso: str | None = None):
    """La navegación solo consulta: nunca genera casos por mirar la cola."""
    con = conectar()
    try:
        filas = listar_casos_alerta(con)
        casos = [
            dict(zip(
                ("clave", "tipo", "severidad", "mensaje", "concepto", "estado",
                 "responsable", "vencimiento", "evidencia"),
                fila,
                strict=True,
            ))
            for fila in filas
        ]
        for caso in casos:
            caso["historial"] = historial_caso(con, caso["clave"])
    finally:
        con.close()
    return _render(
        request,
        "casos.html",
        {"casos": casos, "mensajes": [(aviso, "exito")] if aviso else []},
        pagina_activa="casos",
    )


@app.post("/casos/{clave}")
def post_caso(
    request: Request,
    clave: str,
    estado: str = Form(...),
    responsable: str = Form(""),
    vencimiento: str = Form(""),
    evidencia: str = Form(""),
    motivo: str = Form(""),
    csrf: str = Form(""),
):
    if error := _exigir_csrf(request, csrf):
        return error
    con = conectar()
    try:
        actualizar_caso_alerta(
            con,
            clave=clave,
            estado=estado,
            responsable=responsable.strip() or None,
            vencimiento=vencimiento.strip() or None,
            evidencia=evidencia.strip() or None,
            actor=request.state.sesion["usuario"],
            motivo=motivo.strip() or None,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=422)
    finally:
        con.close()
    return RedirectResponse("/casos?aviso=Caso+actualizado", status_code=303)


@app.get("/sin-clasificar", response_class=HTMLResponse)
def get_sin_clasificar(request: Request):
    """Prioriza únicamente importes comparables en pesos constantes."""
    con = conectar()
    try:
        filas_crudas = filas_sin_clasificar_por_periodo(con)
        universo_crudo = importes_por_periodo(con)
    finally:
        con.close()
    resumen = []
    importe_real = porcentaje = fecha_base = None
    error_ipc = None
    if filas_crudas:
        try:
            filas = [
                (servicio, descripcion, score, importe, date.fromisoformat(periodo))
                for servicio, descripcion, score, importe, periodo in filas_crudas
            ]
            fecha_base = max(fila[4] for fila in filas)
            ipc = leer_ipc()
            resumen, importe_real = resumir_sin_clasificar(
                filas, fecha_base=fecha_base, df_ipc=ipc
            )
            universo_real = total_en_pesos_constantes(
                [(importe, date.fromisoformat(periodo)) for importe, periodo in universo_crudo],
                fecha_base=fecha_base,
                df_ipc=ipc,
            )
            porcentaje = importe_real / universo_real if universo_real else None
        except (OSError, ValueError, requests.exceptions.RequestException) as exc:
            error_ipc = str(exc)
    return _render(
        request,
        "sin_clasificar.html",
        {
            "filas": resumen,
            "total_conceptos": len({(fila[0], fila[1]) for fila in filas_crudas}),
            "sin_medicion": sum(fila[2] is None for fila in filas_crudas),
            "importe_real": pesos_ars(importe_real) if importe_real is not None else None,
            "porcentaje": f"{porcentaje:.1%}" if porcentaje is not None else None,
            "fecha_base": fecha_base,
            "error_ipc": error_ipc,
        },
        pagina_activa="sin_clasificar",
    )


# --- Subir ---------------------------------------------------------------


@app.get("/subir", response_class=HTMLResponse)
def get_subir(request: Request):
    try:
        uso_evidencia = uso_evidencia_bytes()
    except Exception:  # noqa: BLE001 -- se muestra desconocido; la escritura bloquea si no mide
        uso_evidencia = None
    return _render(
        request,
        "subir.html",
        {
            "api_key_configurada": _api_key_configurada(),
            "uso_evidencia_mb": (
                round(uso_evidencia / 1024**2, 1) if uso_evidencia is not None else None
            ),
            "limite_evidencia_mb": LIMITE_BORRADORES_BUCKET_BYTES // 1024**2,
        },
        pagina_activa="subir",
    )


@app.post("/subir", response_class=HTMLResponse)
async def post_subir(
    request: Request,
    archivos: list[UploadFile],
    modo: str | None = Form(None),
    apto_gemini: str | None = Form(None),
    csrf: str = Form(""),
):
    if error := _exigir_csrf(request, csrf):
        return error
    if modo is None and os.environ.get("SEGURPLUS_PRODUCTION") != "1":
        modo = "gemini"  # compatibilidad con clientes locales anteriores
    if modo not in {"gemini", "manual"}:
        return Response("Elegí lectura con Gemini o carga manual.", status_code=400)
    if modo == "gemini" and (
        not _api_key_configurada()
        or (os.environ.get("SEGURPLUS_PRODUCTION") == "1" and apto_gemini != "si")
    ):
        return Response("No se autorizó el envío a Gemini.", status_code=400)
    if len(archivos) > _MAXIMO_ARCHIVOS_POR_SUBIDA:
        return _render(
            request,
            "subir.html",
            {
                "api_key_configurada": _api_key_configurada(),
                "mensajes": [
                    (
                        f"Subiste {len(archivos)} archivos -- el máximo por tanda es "
                        f"{_MAXIMO_ARCHIVOS_POR_SUBIDA}. Subilos en grupos más chicos.",
                        "error",
                    )
                ],
            },
            pagina_activa="subir",
        )

    con = conectar()
    resultados: list[ResultadoPipeline] = []
    try:
        for archivo in archivos:
            contenido = await archivo.read()
            if len(contenido) > _TAMANIO_MAXIMO_PDF_BYTES:
                resultados.append(
                    ResultadoPipeline(
                        Path(archivo.filename or "archivo.pdf"),
                        hash_pdf="",
                        estado="error_extraccion",
                        detalle=(
                            f"El archivo pesa más de {_TAMANIO_MAXIMO_PDF_BYTES // (1024 * 1024)} "
                            "MB -- no se subió."
                        ),
                    )
                )
                continue
            ruta_temporal = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(contenido)
                    ruta_temporal = Path(tmp.name)
                # docs/auditoria-2026-09-web.md, E-8: procesar_pdf es
                # sincrónico y tarda 15-35s contra Gemini (ver el banco de
                # medición) -- correrlo directo acá bloquearía el único
                # event loop del proceso, así que nadie más podría usar la
                # app mientras tanto. `run_in_threadpool` lo corre en un
                # hilo aparte sin tocar el resto del código de core/.
                if modo == "manual":
                    resultado = await run_in_threadpool(
                        procesar_pdf_manual,
                        ruta_temporal,
                        con,
                        actor=request.state.sesion["usuario"],
                    )
                else:
                    resultado = await run_in_threadpool(
                        procesar_pdf,
                        ruta_temporal,
                        con,
                        apto_gemini=apto_gemini == "si",
                        actor=request.state.sesion["usuario"],
                    )
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


# docs/auditoria-2026-09-web.md, E-10: reemplaza la cookie `flash` (nadie
# la leía -- el mensaje de "factura confirmada" nunca se mostraba). Un
# parámetro `?aviso=` fijo, traducido acá a un mensaje fijo, para no
# mostrar texto arbitrario que venga de la URL.
_AVISOS_REVISAR = {
    "confirmada": ("Factura confirmada: ya impacta el análisis.", "ok"),
    "pendiente": (
        "Factura confirmada: queda pendiente de revisión humana antes de impactar.",
        "ok",
    ),
    "descartada": ("Borrador descartado.", "ok"),
    # E-15: antes esto era un error 500 -- volver con el botón "atrás",
    # doblar click en "Confirmar", o que otra persona ya haya confirmado o
    # descartado la misma factura, todos terminaban en "Internal Server
    # Error" en vez de un aviso.
    "ya_procesada": (
        "Esa factura ya no está esperando confirmación -- puede que ya se haya "
        "confirmado o descartado.",
        "info",
    ),
}


@app.get("/revisar", response_class=HTMLResponse)
def get_revisar(request: Request, aviso: str | None = None):
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
    mensajes = [_AVISOS_REVISAR[aviso]] if aviso in _AVISOS_REVISAR else []
    return _render(
        request,
        "revisar_lista.html",
        {"borradores": borradores, "mensajes": mensajes},
        pagina_activa="revisar",
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
    hash_pdf: str,
    datos: dict,
    *,
    con,
    errores_aritmetica: list[str],
    aritmetica_ok: bool,
    mostrar_boton_un_renglon: bool = False,
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
            for d, c, u, p, i, _sugerido in datos["conceptos"]
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
        "pdf_disponible": bool(leer_pdf(datos["ruta_evidencia"], con=con)),
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
        "mostrar_boton_un_renglon": mostrar_boton_un_renglon,
    }


def _leer_borrador_o_none(con, hash_pdf: str) -> dict | None:
    """`None` si `hash_pdf` no existe o ya no está en estado 'borrador' --
    en vez de dejar pasar el `ValueError` de `leer_borrador` (docs/
    auditoria-2026-09-web.md, E-15): volver con el botón "atrás", doble
    click en "Confirmar", o que otra persona ya haya confirmado o
    descartado la misma factura, todos terminaban en un error 500."""
    try:
        return leer_borrador(con, hash_pdf)
    except ValueError:
        return None


@app.get("/revisar/{hash_pdf}", response_class=HTMLResponse)
def get_revisar_detalle(request: Request, hash_pdf: str):
    con = conectar()
    try:
        datos = _leer_borrador_o_none(con, hash_pdf)
        if datos is None:
            return RedirectResponse("/revisar?aviso=ya_procesada", status_code=303)
        contexto = _contexto_detalle_completo(con, hash_pdf, datos)
    finally:
        con.close()
    return _render(request, "revisar_detalle.html", contexto, pagina_activa="revisar")


def _puede_cargar_un_renglon(datos: dict) -> bool:
    """docs/auditoria-2026-09-web.md, E-14: True solo cuando el diseño de
    gas a dos columnas dejó las líneas de concepto rotas pero el subtotal,
    el total y el total impreso del PDF sí cierran -- la misma condición
    que decide si se MUESTRA el botón "Cargar como un solo renglón"
    (`_contexto_detalle_completo`, más abajo) la usa también
    `post_un_renglon` para revalidar server-side antes de colapsar las
    líneas, no solo `datos["servicio"] == "gas"` (eso solo no alcanza:
    alguien podría armar el POST a mano para una factura de gas cuyo
    subtotal nunca se validó)."""
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
                for d, c, u, p, i, _sugerido in datos["conceptos"]
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
    return (
        datos["servicio"] == "gas"
        and not resultado.todas_las_lineas_ok
        and resultado.subtotal_presente
        and resultado.subtotal_ok
        and resultado.total_presente
        and resultado.total_ok
        and (total_impreso_valor is None or resultado.total_impreso_ok)
    )


def _contexto_detalle_completo(con, hash_pdf: str, datos: dict) -> dict:
    """Arma el contexto completo (incluida la validación aritmética) para
    `revisar_detalle.html` -- separado de `get_revisar_detalle` para que
    `con` siga abierta mientras `_contexto_detalle` necesita leer el PDF
    de la base (docs/auditoria-2026-09-web.md, E-4: `leer_pdf` con
    `ruta_evidencia` de la forma `db://...` necesita una conexión viva)."""
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
                for d, c, u, p, i, _sugerido in datos["conceptos"]
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

    return _contexto_detalle(
        hash_pdf,
        datos,
        con=con,
        errores_aritmetica=errores,
        aritmetica_ok=resultado.factura_valida,
        mostrar_boton_un_renglon=_puede_cargar_un_renglon(datos),
    )


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


def _error_de_formulario(
    request: Request, con, hash_pdf: str, datos: dict, mensaje: str
) -> HTMLResponse:
    """E-15: `_factura_desde_form` puede lanzar `ValueError` si las listas
    del formulario no tienen todas la misma longitud (`zip(strict=True)`)
    -- un formulario armado a mano o roto en el navegador, no algo que
    antes se manejara: tumbaba la request con un error 500."""
    contexto = _contexto_detalle(
        hash_pdf, datos, con=con, errores_aritmetica=[], aritmetica_ok=False
    )
    contexto["mensajes"] = [(mensaje, "error")]
    return _render(request, "revisar_detalle.html", contexto, pagina_activa="revisar")


@app.post("/revisar/{hash_pdf}/confirmar", response_class=HTMLResponse)
async def post_confirmar(request: Request, hash_pdf: str, csrf: str = Form("")):
    if error := _exigir_csrf(request, csrf):
        return error
    con = conectar()
    try:
        datos = _leer_borrador_o_none(con, hash_pdf)
        if datos is None:
            return RedirectResponse("/revisar?aviso=ya_procesada", status_code=303)
        try:
            factura = await _factura_desde_form(request, hash_pdf, datos)
        except ValueError as exc:
            return _error_de_formulario(
                request, con, hash_pdf, datos, f"No se pudo leer el formulario: {exc}"
            )
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
                hash_pdf,
                datos,
                con=con,
                errores_aritmetica=errores,
                aritmetica_ok=resultado.factura_valida,
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

    aviso = "confirmada" if estado_final == "aprobada" else "pendiente"
    return RedirectResponse(f"/revisar?aviso={aviso}", status_code=303)


@app.post("/revisar/{hash_pdf}/guardar")
async def post_guardar(request: Request, hash_pdf: str, csrf: str = Form("")):
    if error := _exigir_csrf(request, csrf):
        return error
    con = conectar()
    try:
        datos = _leer_borrador_o_none(con, hash_pdf)
        if datos is None:
            return RedirectResponse("/revisar?aviso=ya_procesada", status_code=303)
        try:
            factura = await _factura_desde_form(request, hash_pdf, datos)
        except ValueError as exc:
            return _error_de_formulario(
                request, con, hash_pdf, datos, f"No se pudo leer el formulario: {exc}"
            )
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


def _cantidad_m3_conocida(conceptos: list[tuple]) -> float | None:
    """docs/auditoria-2026-09-web.md, E-14: cuántos m³ se consumieron, SOLO
    si todas las líneas del borrador ya miden en m³ (o "m3") -- si mezclan
    unidades o falta alguna cantidad, no hay una cifra confiable que sumar,
    y se prefiere dejarla sin dato antes que inventar un número."""
    cantidades = []
    for _descripcion, cantidad, unidad, _precio, _importe, _sugerido in conceptos:
        unidad_normalizada = (unidad or "").strip().lower().replace("³", "3")
        if unidad_normalizada != "m3" or cantidad is None:
            return None
        cantidades.append(cantidad)
    return sum(cantidades) if cantidades else None


def _factura_como_un_renglon(hash_pdf: str, datos: dict) -> FacturaExtraida:
    """docs/auditoria-2026-09-web.md, E-14: reemplaza TODAS las líneas de
    concepto del borrador por una sola ("Consumo de gas") con el subtotal
    ya validado -- se pierde el detalle línea por línea, que en el layout
    de gas a dos columnas de todos modos no era confiable."""
    subtotal = datos["subtotal"]
    cantidad_m3 = _cantidad_m3_conocida(datos["conceptos"])
    if cantidad_m3:
        concepto = Concepto("Consumo de gas", cantidad_m3, "m³", subtotal / cantidad_m3, subtotal)
    else:
        concepto = Concepto("Consumo de gas", 1.0, None, subtotal, subtotal)
    return FacturaExtraida(
        emisor=datos["emisor"],
        cuit=datos["cuit"],
        servicio=datos["servicio"],
        periodo_desde=datos["periodo_desde"],
        periodo_hasta=datos["periodo_hasta"],
        fecha_emision=datos["fecha_emision"],
        fecha_vencimiento=datos["fecha_vencimiento"],
        numero_comprobante=datos["numero_comprobante"],
        moneda=datos["moneda"] or "ARS",
        conceptos=[concepto],
        impuestos=montos_desde_filas(
            [{"nombre": n, "importe": i} for n, i in datos["impuestos"]], Impuesto
        ),
        recargos=montos_desde_filas(
            [{"nombre": n, "importe": i} for n, i in datos["recargos"]], Recargo
        ),
        creditos=montos_desde_filas(
            [{"nombre": n, "importe": i} for n, i in datos["creditos"]], Credito
        ),
        subtotal=subtotal,
        total=datos["total"],
        hash_pdf=hash_pdf,
        ruta_pdf=datos["ruta_pdf"],
        ruta_evidencia=datos["ruta_evidencia"],
        modelo_extraccion=datos["modelo_extraccion"],
        version_prompt=datos["version_prompt"],
        version_esquema=datos["version_esquema"],
        respuesta_extraida=datos["respuesta_extraida"],
    )


@app.post("/revisar/{hash_pdf}/un_renglon")
def post_un_renglon(request: Request, hash_pdf: str, csrf: str = Form("")):
    if error := _exigir_csrf(request, csrf):
        return error
    con = conectar()
    try:
        datos = _leer_borrador_o_none(con, hash_pdf)
        if datos is None:
            return RedirectResponse("/revisar?aviso=ya_procesada", status_code=303)
        # Mismo criterio que decide si se MUESTRA el botón
        # (`_puede_cargar_un_renglon`) -- se revalida acá server side, no
        # solo en el template, por si alguien arma el POST a mano para una
        # factura de gas cuyo subtotal nunca se validó.
        if _puede_cargar_un_renglon(datos):
            factura = _factura_como_un_renglon(hash_pdf, datos)
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
def post_descartar(request: Request, hash_pdf: str, csrf: str = Form("")):
    if error := _exigir_csrf(request, csrf):
        return error
    con = conectar()
    try:
        try:
            descartar_borrador(con, hash_pdf)
        except ValueError:
            return RedirectResponse("/revisar?aviso=ya_procesada", status_code=303)
    finally:
        con.close()
    return RedirectResponse("/revisar?aviso=descartada", status_code=303)


@app.get("/pdf/{hash_pdf}")
def get_pdf(hash_pdf: str):
    # docs/auditoria-2026-09-web.md, E-4: no usa `leer_borrador` (que
    # rechaza cualquier factura que no esté en estado 'borrador') -- esta
    # ruta sirve el PDF tanto de un borrador como de una factura ya
    # confirmada, y `leer_pdf` con una `ruta_evidencia` de la forma
    # `db://...` necesita una conexión viva para leer la base.
    con = conectar()
    try:
        fila = con.execute(
            "SELECT ruta_evidencia FROM facturas WHERE hash_pdf = ?", [hash_pdf]
        ).fetchone()
        ruta_evidencia = fila[0] if fila else None
        contenido = leer_pdf(ruta_evidencia, con=con)
    finally:
        con.close()
    if contenido is None:
        return Response(status_code=404)
    return Response(content=contenido, media_type="application/pdf")


# --- Ver -------------------------------------------------------------------


def _servicios_con_facturas_aprobadas(con) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT servicio FROM facturas "
            "WHERE servicio IS NOT NULL AND estado = 'aprobada' AND moneda = 'ARS' ORDER BY 1"
        ).fetchall()
    ]


def _periodos_del_servicio(con, servicio: str) -> list[tuple[str, str | None]]:
    return con.execute(
        "SELECT periodo_desde, max(periodo_hasta) FROM facturas "
        "WHERE servicio = ? AND periodo_desde IS NOT NULL "
        "AND estado = 'aprobada' AND moneda = 'ARS' "
        "GROUP BY periodo_desde ORDER BY 1",
        [servicio],
    ).fetchall()


def _resumen_periodo_unico(con, *, servicio: str, periodo: str) -> dict:
    """docs/auditoria-2026-09-web.md, E-12: con un solo período cargado no
    hay con qué comparar, pero mostrar "cargá dos meses" y nada más
    desperdicia el único dato que sí tenemos. Se arma el mismo resumen que
    usaría `_analisis`, pero con el período anterior en cero -- reutiliza
    la rama de `generar_relato_determinista` ya probada para "primera
    factura del servicio" (`total_0 == 0`), con el mes calendario previo
    como referencia (aunque no haya factura cargada para ese mes)."""
    componentes = componentes_financieros_periodo(con, servicio=servicio, periodo_desde=periodo)
    fecha = date.fromisoformat(periodo)
    anio_anterior, mes_anterior = (
        (fecha.year, fecha.month - 1)
        if fecha.month > 1
        else (
            fecha.year - 1,
            12,
        )
    )
    periodo_anterior = date(anio_anterior, mes_anterior, 1).isoformat()

    relato = generar_relato_determinista(
        DatosRelato(
            servicio=servicio,
            periodo_0=periodo_anterior,
            periodo_1=periodo,
            total_0=0.0,
            total_1=componentes["total_pagable"],
            consumos_0=0.0,
            consumos_1=componentes["consumos"],
            impuestos_0=0.0,
            impuestos_1=componentes["impuestos"],
            recargos_0=0.0,
            recargos_1=componentes["recargos"],
            creditos_0=0.0,
            creditos_1=componentes["creditos"],
            tipo_dominante="sin_variacion",
            proporcion_dominante=0.0,
            efecto_precio_total=0.0,
            efecto_cantidad_total=0.0,
            variacion_real_pct=None,
            inflacion_pct=None,
            concepto_destacado=None,
        )
    )
    composicion = [
        {"etiqueta": etiqueta, "valor": pesos_ars(componentes[clave])}
        for clave, etiqueta in [
            ("consumos", "Consumos / abonos"),
            ("impuestos", "Impuestos"),
            ("recargos", "Recargos"),
            ("creditos", "Créditos / descuentos"),
            ("total_pagable", "Total pagable"),
        ]
    ]
    return {
        "periodo_legible": mes_anio(periodo),
        "total": pesos_ars(componentes["total_pagable"]),
        "composicion": composicion,
        "relato": relato,
    }


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
        if len(periodos) == 1:
            resumen = _resumen_periodo_unico(con, servicio=servicio_elegido, periodo=periodos[0])
            return _render(
                request,
                "ver_selector.html",
                {
                    "servicios": servicios,
                    "servicio_elegido": servicio_elegido,
                    "servicio_elegido_legible": nombre_servicio(servicio_elegido),
                    "periodos": [],
                    "resumen": resumen,
                },
                pagina_activa="ver",
            )
        if not periodos:
            return _render(
                request,
                "ver_selector.html",
                {
                    "servicios": servicios,
                    "servicio_elegido": servicio_elegido,
                    "servicio_elegido_legible": nombre_servicio(servicio_elegido),
                    "periodos": [],
                },
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

        # docs/auditoria-2026-09-web.md, E-11: el cálculo de la comparación
        # es el MISMO que usa el Excel (`get_ver_excel`, más abajo) -- antes
        # cada pantalla tenía su propia copia y podían mostrar alertas
        # distintas para el mismo par de períodos.
        # docs/auditoria-2026-09-web.md, E-22: este GET ya no escribe en la
        # base -- antes sincronizaba "casos" en cada visita a la pantalla,
        # una pantalla que no existe en web/ (queda en el tablero
        # Streamlit, que la sigue sincronizando desde su propia página).
        resultado = calcular_comparacion(
            con,
            servicio=servicio,
            periodo_0=periodo_0,
            periodo_1=periodo_1,
            periodos_del_servicio=filas_periodos,
        )

        total_pagable_0 = resultado.componentes_0["total_pagable"]
        total_pagable_1 = resultado.componentes_1["total_pagable"]
        consumos_0 = resultado.componentes_0["consumos"]
        consumos_1 = resultado.componentes_1["consumos"]

        cambio_formateado = pesos_ars(consumos_1 - consumos_0, signo=True)
        # Bloque 6 del plan de rediseño: "(73%)" a secas se leía como si
        # $73 de cada $100 de la variación fueran por precio -- en
        # realidad es la fracción del MOVIMIENTO total (incluye el efecto
        # cruzado), no de la variación neta. "del movimiento" lo aclara
        # sin volverse un párrafo aparte.
        proporcion_formateada = f"{abs(resultado.proporcion_dominante):.0%} del movimiento"
        if resultado.tipo_dominante == "precio":
            veredicto = (
                f"El cambio de {cambio_formateado} en los consumos fue mayormente por "
                f"PRECIO ({proporcion_formateada})."
            )
        elif resultado.tipo_dominante == "cantidad":
            veredicto = (
                f"El cambio de {cambio_formateado} en los consumos fue mayormente por "
                f"CANTIDAD ({proporcion_formateada})."
            )
        elif resultado.tipo_dominante == "mixto":
            veredicto = (
                f"El cambio de {cambio_formateado} fue una mezcla de cantidad y precio -- "
                "ningún efecto explica la mayor parte por sí solo."
            )
        else:
            veredicto = "No hubo variación nominal entre los períodos seleccionados."

        avisos_calculo = list(resultado.avisos_calculo)
        principales = top_conceptos_por_variacion(resultado.descomposiciones, TOP_N_CONCEPTOS)
        if len(principales) < len(resultado.descomposiciones):
            avisos_calculo.append(
                f"Mostrando los {TOP_N_CONCEPTOS} conceptos con mayor variación, de "
                f"{len(resultado.descomposiciones)} en total -- el resto está en Detalle."
            )

        composicion = [
            {
                "etiqueta": etiqueta,
                "valor_0": pesos_ars(resultado.componentes_0[clave]),
                "valor_1": pesos_ars(resultado.componentes_1[clave]),
                "variacion": pesos_ars(
                    resultado.componentes_1[clave] - resultado.componentes_0[clave]
                ),
            }
            for clave, etiqueta in [
                ("consumos", "Consumos / abonos"),
                ("impuestos", "Impuestos"),
                ("recargos", "Recargos"),
                ("creditos", "Créditos / descuentos"),
                ("total_pagable", "Total pagable"),
            ]
        ]

        # La serie histórica (multi-período) es propia de la pantalla, no
        # de una comparación de dos períodos -- no la calcula
        # `calcular_comparacion`. Usa el TOTAL PAGABLE
        # (`totales_pagables_por_periodo`), no solo consumos, para no
        # mostrar dos cifras distintas del mismo mes en la misma pantalla
        # (docs/auditoria-2026-09-web.md, E-11).
        serie = []
        error_serie = None
        try:
            fecha_base = date.fromisoformat(filas_periodos[-1][0])
            totales_por_fecha = {
                date.fromisoformat(p): total
                for p, total in totales_pagables_por_periodo(con, servicio=servicio).items()
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

        ordenadas = ordenar_por_severidad(resultado.alertas)
        conteo = Counter(a.severidad for a in ordenadas)
    finally:
        con.close()

    contexto = {
        "servicio": servicio,
        "periodo_0": periodo_0,
        "periodo_1": periodo_1,
        # docs/auditoria-2026-09-web.md, E-13: el slug (`servicio`) y las
        # fechas ISO (`periodo_0`/`periodo_1`) se conservan tal cual para
        # los links que arman query params (Excel, "Cambiar servicio o
        # período") -- estas versiones son solo para mostrar en el título,
        # el encabezado y las etiquetas de las métricas.
        "servicio_legible": nombre_servicio(servicio),
        "periodo_0_legible": mes_anio(periodo_0),
        "periodo_1_legible": mes_anio(periodo_1),
        "borradores_pendientes": borradores_pendientes,
        "avisos_calculo": avisos_calculo,
        "conceptos_sin_clasificar": resultado.conceptos_sin_clasificar,
        "anomalos": resultado.anomalos,
        # Bloque 6: el número grande es el TOTAL PAGABLE (consumos +
        # impuestos + recargos - créditos) -- antes era solo la suma de
        # consumos, que no es lo que la factura cobra de verdad. Los
        # consumos siguen mostrándose, como referencia secundaria.
        "total_0": pesos_ars(total_pagable_0),
        "total_1": pesos_ars(total_pagable_1),
        "consumos_0": pesos_ars(consumos_0),
        "consumos_1": pesos_ars(consumos_1),
        "variacion_nominal": f"{pesos_ars(total_pagable_1 - total_pagable_0, signo=True)} nominal",
        "variacion_real": (
            f"{resultado.variacion_real_pct:+.1%}"
            if resultado.variacion_real_pct is not None
            else None
        ),
        # None (no "+0,0%") cuando el IPC no se pudo descargar/calcular --
        # ver "avisos_calculo" arriba, que ya explica por qué. +0,0% sería
        # indistinguible de una inflación real de cero.
        "inflacion_periodo": (
            f"{resultado.ipc_periodo_pct:+.1%}" if resultado.ipc_periodo_pct is not None else None
        ),
        "veredicto": veredicto,
        "relato": resultado.relato,
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
            for d in resultado.descomposiciones
        ],
    }
    contexto.update(contexto_selector)
    return _render(request, "ver.html", contexto, pagina_activa="ver")


@app.get("/ver/excel")
def get_ver_excel(servicio: str, periodo_0: str, periodo_1: str):
    con = conectar()
    try:
        borradores_pendientes = contar_borradores(con, servicio=servicio)
        filas_periodos = _periodos_del_servicio(con, servicio)

        # docs/auditoria-2026-09-web.md, E-11: mismo cálculo que la
        # pantalla (`_analisis`, más arriba) -- antes el Excel no tenía en
        # cuenta `acumulado`, ni la alerta de período faltante, ni los
        # conceptos con cantidad sintética, así que podía mostrar una lista
        # de alertas distinta de la que la persona acababa de ver.
        resultado = calcular_comparacion(
            con,
            servicio=servicio,
            periodo_0=periodo_0,
            periodo_1=periodo_1,
            periodos_del_servicio=filas_periodos,
        )
        cuarentena_actual = con.execute("SELECT ruta_pdf, motivos FROM cuarentena").fetchall()

        buffer_excel = BytesIO()
        generar_reporte_excel(
            servicio=servicio,
            periodo_0=periodo_0,
            periodo_1=periodo_1,
            descomposiciones=resultado.descomposiciones,
            alertas=resultado.alertas,
            cuarentena=cuarentena_actual,
            ruta_salida=buffer_excel,
            borradores_sin_confirmar=borradores_pendientes,
            componentes_0=resultado.componentes_0,
            componentes_1=resultado.componentes_1,
            relato=resultado.relato,
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
