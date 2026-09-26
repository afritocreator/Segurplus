"""Orquesta el pipeline de facturas en DOS pasos, ninguno de los cuales
decide solo si una factura entra al análisis:

1. `procesar_pdf` -- ingesta + extracción con Gemini. Deja SIEMPRE un
   BORRADOR (`estado="borrador"` en `facturas`), sea cual sea su calidad:
   la aritmética no cierre, falte período o servicio, o la extracción haya
   fallado del todo. Nunca escribe `aprobada`/`requiere_revision` -- eso
   pasa a `confirmar_factura`, después de que alguien revisó el borrador
   con el PDF a la vista en `apps/segurplus/paginas/confirmar.py`.
2. `confirmar_factura` -- guarda como DEFINITIVA una factura ya editada y
   validada en esa pantalla: re-homologa, guarda alertas, sincroniza casos.

Antes de este rediseño, el pipeline decidía solo y una factura que no
cerraba terminaba en uno de tres callejones sin salida (cuarentena, con
solo un botón "reintentar" que no arreglaba nada; "necesita_datos", que
mandaba a corregir de a un campo sin ver el PDF; o un mensaje que se
perdía). El circuito de confirmación reemplaza los tres.

Cáscara delgada sobre `core/ingesta/`, `core/extraccion/` y `core/analisis/`
-- no hace ningún cálculo nuevo, compone lo que ya está escrito y probado.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from core.almacenamiento import (
    ConexionPostgres,
    _transaccional,
    actualizar_fallo_extraccion_borrador,
    duplicado_de_negocio,
    encolar_comparaciones,
    factura_ya_procesada,
    finalizar_intento_gemini,
    guardar_alertas,
    guardar_factura,
    leer_borrador,
    llamadas_ultima_hora,
    proxima_ventana_libre,
    registrar_clasificacion_documento,
    registrar_correccion_conocida,
    reservar_intento_gemini,
    sincronizar_casos_de_factura,
    transaccion,
    ultima_clasificacion_documento,
)
from core.analisis.alertas import alertas_por_item_duplicado
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import homologar_concepto
from core.evidencia import borrar_pdf, guardar_pdf, leer_pdf
from core.extraccion.esquema import FacturaExtraida
from core.extraccion.gemini import ExtraccionError, es_error_transitorio, extraer_con_gemini
from core.extraccion.validacion import validar_factura
from core.ingesta.pdf_texto import extraer_texto
from core.operacion import (
    espera_reintento_gemini_segundos,
    intentos_gemini_por_llamada,
    max_llamadas_gemini_por_hora,
    revision_humana_obligatoria,
    tamano_maximo_pdf_bytes,
    zona_horaria,
)


@dataclass
class ResultadoPipeline:
    ruta: Path
    hash_pdf: str
    # "ya_procesada" | "ya_extraida" | "borrador" | "error_extraccion" -- el pipeline ya NO
    # decide si una factura entra al análisis (ver docstring del módulo).
    # "error_extraccion" queda reservado para lo que ni siquiera se llegó a
    # INTENTAR leer (PDF corrupto o evidencia no verificable). Un PDF válido
    # sin texto y un tope de cuota sí dejan borrador para revisión/reintento.
    estado: str
    detalle: str = ""


# Mismo conjunto que acepta core.almacenamiento.registrar_correccion --
# docs/auditoria-2026-09-confirmacion.md, D-4: son los únicos campos de
# cabecera para los que existe una forma de dejar constancia de "esto lo
# corrigió una persona, de esto a esto".
_CAMPOS_CABECERA_CORREGIBLE = (
    "emisor",
    "cuit",
    "servicio",
    "periodo_desde",
    "periodo_hasta",
    "fecha_emision",
    "fecha_vencimiento",
    "numero_comprobante",
    "moneda",
)


def _borrador_vacio(hash_pdf: str, ruta: Path) -> FacturaExtraida:
    """El borrador que se deja cuando ni siquiera Gemini pudo leer la
    factura -- vacío pero con el hash y la ruta, para completarlo a mano en
    la pantalla de confirmación en vez de perder el PDF por completo."""
    return FacturaExtraida(
        emisor=None,
        cuit=None,
        servicio=None,
        periodo_desde=None,
        periodo_hasta=None,
        fecha_emision=None,
        fecha_vencimiento=None,
        numero_comprobante=None,
        moneda="ARS",
        hash_pdf=hash_pdf,
        ruta_pdf=str(ruta),
    )


def _mensaje_operativo_gemini(exc: ExtraccionError | None) -> str:
    """Traduce errores del proveedor sin exponer su payload en la interfaz.

    El detalle completo ya queda en `intentos_gemini.mensaje` y
    `respuesta_cruda`; `motivo_carga` es deliberadamente texto operativo.
    """
    if exc is None:
        return "Gemini no pudo leer el documento. Podés reintentarlo desde este borrador."
    detalle = str(exc).lower()
    if "503" in detalle or "unavailable" in detalle or "high demand" in detalle:
        return "Gemini está temporalmente saturado. Reintentá desde este borrador más tarde."
    if "429" in detalle or "resource_exhausted" in detalle or "quota" in detalle:
        return "Gemini alcanzó temporalmente su cuota. Reintentá desde este borrador más tarde."
    if "api key" in detalle or "unauthorized" in detalle or "permission" in detalle:
        return "Gemini no está disponible por un problema de configuración."
    if "json" in detalle or "esquema" in detalle:
        return "Gemini respondió, pero no se pudo interpretar la extracción."
    return "Gemini no pudo leer el documento. Podés reintentarlo desde este borrador."


def _mensaje_tope_gemini(con: duckdb.DuckDBPyConnection, *, tope: int) -> str:
    destrabe = proxima_ventana_libre(con, tope=tope)
    detalle = f"Se alcanzó el tope de {tope} llamadas a Gemini por hora"
    if destrabe is not None:
        hora_local = datetime.now(ZoneInfo(zona_horaria())) + destrabe
        return detalle + f". Reintentá después de las {hora_local.strftime('%H:%M')}."
    return detalle + ". Reintentá más tarde."


def _extraer_con_reintentos(
    con: duckdb.DuckDBPyConnection,
    *,
    hash_pdf: str,
    ruta_pdf: str,
    contenido_pdf: bytes,
    texto_extraido: str,
    api_key: str | None,
) -> tuple[FacturaExtraida | None, str | None]:
    """Ejecuta Gemini respetando reintentos y cuota antes de cada llamada.

    Usa `reservar_intento_gemini` (no `llamadas_ultima_hora` + un insert
    aparte) para que el chequeo de cuota y el registro del intento sean
    atómicos: dos cargas concurrentes no pueden pasar juntas el tope por
    hora -- ver el advisory lock de Postgres en esa función."""
    intentos_max = intentos_gemini_por_llamada()
    espera_base = espera_reintento_gemini_segundos()
    tope = max_llamadas_gemini_por_hora()
    ultimo_error: ExtraccionError | None = None

    for intento in range(1, intentos_max + 1):
        reserva = reservar_intento_gemini(con, hash_pdf=hash_pdf, ruta_pdf=ruta_pdf, tope=tope)
        if reserva is None:
            return None, _mensaje_tope_gemini(con, tope=tope)
        try:
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo B-3: se le pasa también
            # el texto que `extraer_texto` ya sacó del mismo PDF -- una segunda
            # vista, además del PDF nativo, para las facturas con layout a dos
            # columnas o líneas de impuesto con dos montos.
            factura = extraer_con_gemini(
                contenido_pdf, api_key=api_key, texto_extraido=texto_extraido
            )
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo C-10: en un
            # intento EXITOSO no se manda respuesta_cruda -- esa misma cadena
            # ya va a facturas.respuesta_extraida vía guardar_factura más abajo.
            finalizar_intento_gemini(con, reserva, exito=True)
            return factura, None
        except ExtraccionError as exc:
            ultimo_error = exc
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo B-5/C-2: el
            # intento queda registrado igual, con el JSON crudo si Gemini llegó
            # a responder -- CADA intento, no solo el último, así el tope por
            # hora (max_llamadas_gemini_por_hora) sigue contando llamadas
            # reales (docs/auditoria-2026-09-web.md, E-6).
            finalizar_intento_gemini(
                con,
                reserva,
                exito=False,
                mensaje=str(exc),
                respuesta_cruda=getattr(exc, "respuesta_cruda", None),
            )
            if intento < intentos_max and es_error_transitorio(exc):
                time.sleep(espera_base * intento)
                continue
            break
    return None, _mensaje_operativo_gemini(ultimo_error)


def procesar_pdf(
    ruta: Path,
    con: duckdb.DuckDBPyConnection,
    *,
    api_key: str | None = None,
    apto_gemini: bool | None = None,
    actor: str = "sistema",
) -> ResultadoPipeline:
    """Procesa un único PDF: ingesta + extracción con Gemini, y deja un
    BORRADOR. No lanza excepciones para errores esperables del pipeline
    (PDF sin texto, extracción fallida) -- se reportan en
    `ResultadoPipeline`, no cortan el procesamiento de los demás PDFs de un
    lote."""
    try:
        documento = extraer_texto(ruta, permitir_sin_texto=True)
    except Exception as exc:
        # docs/auditoria-2026-09.md, hallazgo A-18: antes solo se atrapaba
        # PdfSinTextoError -- un PDF corrupto o mal formado (no "sin texto",
        # sino directamente ilegible para pdfplumber/pdfminer) lanzaba una
        # excepción distinta que no se atrapaba acá y tumbaba el lote entero
        # en cargar.py. Un archivo roto es un caso esperable de este pipeline
        # (viene de un upload de usuario), no un bug -- se reporta como
        # cualquier otro error_extraccion en vez de propagar.
        return ResultadoPipeline(
            ruta, hash_pdf="", estado="error_extraccion", detalle=f"PDF ilegible: {exc}"
        )

    if factura_ya_procesada(con, documento.hash_sha256):
        return ResultadoPipeline(ruta, documento.hash_sha256, estado="ya_procesada")

    if os.environ.get("SEGURPLUS_PRODUCTION") == "1":
        if apto_gemini is not True or actor == "sistema":
            return ResultadoPipeline(
                ruta,
                documento.hash_sha256,
                estado="error_extraccion",
                detalle="No se envió a Gemini: falta clasificación expresa del documento.",
            )
        registrar_clasificacion_documento(
            con,
            hash_pdf=documento.hash_sha256,
            apto_gemini=True,
            actor=actor,
            via="gemini",
        )

    contenido_pdf = ruta.read_bytes()
    factura, detalle_fallo = _extraer_con_reintentos(
        con,
        hash_pdf=documento.hash_sha256,
        ruta_pdf=str(ruta),
        contenido_pdf=contenido_pdf,
        texto_extraido=documento.texto,
        api_key=api_key,
    )

    if factura is None:
        # E-6: 503/429/timeout ya se reintentaron y siguieron fallando, o
        # fue un error permanente (sin API key, JSON mal formado) que nunca
        # se reintenta. A diferencia de antes, la factura NO se pierde: se
        # deja un borrador vacío para completar a mano, con el PDF a la
        # vista -- Gemini no pudo leerla, pero el usuario sí puede.
        factura = _borrador_vacio(documento.hash_sha256, ruta)
        if os.environ.get("SEGURPLUS_PRODUCTION") == "1":
            try:
                _guardar_borrador_con_evidencia(
                    con,
                    factura,
                    contenido_pdf=contenido_pdf,
                    actor=actor,
                    texto_extraido=documento.texto,
                    motivo_carga=detalle_fallo or _mensaje_operativo_gemini(None),
                )
            except Exception as fallo:
                return ResultadoPipeline(
                    ruta, factura.hash_pdf, estado="error_extraccion", detalle=str(fallo)
                )
            return ResultadoPipeline(
                ruta, factura.hash_pdf, estado="borrador", detalle=detalle_fallo or ""
            )
        try:
            factura.ruta_evidencia = guardar_pdf(documento.hash_sha256, contenido_pdf, con=con)
        except Exception:  # noqa: BLE001 -- un borrador vacío sin PDF sigue siendo mejor que nada
            factura.ruta_evidencia = None
        guardar_factura(
            con,
            factura,
            estado="borrador",
            actor=actor,
            motivo_carga=detalle_fallo,
            texto_extraido=documento.texto,
        )
        return ResultadoPipeline(
            ruta, factura.hash_pdf, estado="borrador", detalle=detalle_fallo or ""
        )

    factura.hash_pdf = documento.hash_sha256
    factura.ruta_pdf = str(ruta)
    if os.environ.get("SEGURPLUS_PRODUCTION") == "1":
        try:
            _guardar_borrador_con_evidencia(
                con,
                factura,
                contenido_pdf=contenido_pdf,
                actor=actor,
                texto_extraido=documento.texto,
            )
        except Exception as fallo:
            return ResultadoPipeline(
                ruta, factura.hash_pdf, estado="error_extraccion", detalle=str(fallo)
            )
        return ResultadoPipeline(ruta, factura.hash_pdf, estado="borrador")
    try:
        factura.ruta_evidencia = guardar_pdf(documento.hash_sha256, contenido_pdf, con=con)
    except Exception as exc:  # noqa: BLE001 -- no se pierde una lectura exitosa por esto
        # Gemini SÍ pudo leer la factura -- que el PDF no se haya podido
        # guardar como evidencia (disco lleno, S3 caído) es un problema de
        # infraestructura aparte, y no vale perder la lectura por eso: se
        # guarda igual como borrador, con el texto extraído como respaldo
        # visual (ver apps/segurplus/paginas/confirmar.py) en vez del PDF.
        factura.ruta_evidencia = None
        motivo = f"No se pudo guardar el PDF como evidencia: {exc}"
        guardar_factura(
            con,
            factura,
            estado="borrador",
            actor=actor,
            motivo_carga=motivo,
            texto_extraido=documento.texto,
        )
        # D-8: se propaga el detalle -- este borrador SÍ tiene los datos que
        # leyó Gemini (no está vacío), pero cargar.py necesita poder avisar
        # igual que el PDF de evidencia no quedó guardado.
        return ResultadoPipeline(ruta, factura.hash_pdf, estado="borrador", detalle=motivo)

    guardar_factura(con, factura, estado="borrador", actor=actor, texto_extraido=documento.texto)
    return ResultadoPipeline(ruta, factura.hash_pdf, estado="borrador")


def procesar_pdf_manual(
    ruta: Path, con: duckdb.DuckDBPyConnection, *, actor: str
) -> ResultadoPipeline:
    """Deja un borrador editable sin enviar contenido a ningún modelo."""
    limite_pdf = tamano_maximo_pdf_bytes()
    if ruta.stat().st_size > limite_pdf:
        return ResultadoPipeline(
            ruta,
            hash_pdf="",
            estado="error_extraccion",
            detalle=f"El PDF supera el máximo permitido de {limite_pdf // (1024 * 1024)} MB.",
        )
    contenido = ruta.read_bytes()
    hash_pdf = hashlib.sha256(contenido).hexdigest()
    if factura_ya_procesada(con, hash_pdf):
        return ResultadoPipeline(ruta, hash_pdf, estado="ya_procesada")
    try:
        texto = extraer_texto(ruta).texto
    except Exception:  # noqa: BLE001 -- un escaneo puede completarse mirando el PDF
        texto = ""
    factura = _borrador_vacio(hash_pdf, ruta)
    limite_pdf = tamano_maximo_pdf_bytes()
    if ruta.stat().st_size > limite_pdf:
        return ResultadoPipeline(
            ruta,
            hash_pdf="",
            estado="error_extraccion",
            detalle=f"El PDF supera el máximo permitido de {limite_pdf // (1024 * 1024)} MB.",
        )
    try:
        _guardar_borrador_con_evidencia(
            con,
            factura,
            contenido_pdf=contenido,
            actor=actor,
            texto_extraido=texto,
            motivo_carga="Carga manual, sin envío a Gemini",
            clasificacion=(False, "manual"),
        )
    except Exception as exc:
        return ResultadoPipeline(ruta, hash_pdf, estado="error_extraccion", detalle=str(exc))
    return ResultadoPipeline(ruta, hash_pdf, estado="borrador")


def reintentar_extraccion_borrador(
    con: duckdb.DuckDBPyConnection,
    hash_pdf: str,
    *,
    actor: str,
    api_key: str | None = None,
) -> ResultadoPipeline:
    """Reintenta Gemini sobre la evidencia inmutable del mismo borrador.

    Un fallo actualiza solamente el diagnóstico. Un éxito reemplaza la
    extracción del borrador, pero conserva su hash y PDF; `guardar_factura`
    deja la nueva versión y la decisión en el historial.
    """
    datos = leer_borrador(con, hash_pdf)
    ruta = Path(datos["ruta_pdf"] or f"{hash_pdf}.pdf")
    if datos["extraccion_gemini_exitosa"]:
        return ResultadoPipeline(ruta, hash_pdf, estado="ya_extraida")

    clasificacion = ultima_clasificacion_documento(con, hash_pdf)
    if (
        clasificacion is None
        or clasificacion["via"] != "gemini"
        or clasificacion["apto_gemini"] is not True
    ):
        raise ValueError("Este documento no fue autorizado para enviarse a Gemini.")

    contenido_pdf = leer_pdf(datos["ruta_evidencia"], con=con)
    if contenido_pdf is None or hashlib.sha256(contenido_pdf).hexdigest() != hash_pdf:
        raise ValueError("No se puede reintentar sin el PDF original verificable.")

    factura, detalle_fallo = _extraer_con_reintentos(
        con,
        hash_pdf=hash_pdf,
        ruta_pdf=str(ruta),
        contenido_pdf=contenido_pdf,
        texto_extraido=datos["texto_extraido"] or "",
        api_key=api_key,
    )
    if factura is None:
        try:
            actualizar_fallo_extraccion_borrador(
                con,
                hash_pdf,
                mensaje=detalle_fallo or _mensaje_operativo_gemini(None),
                actor=actor,
            )
        except ValueError:
            return ResultadoPipeline(ruta, hash_pdf, estado="ya_procesada")
        return ResultadoPipeline(
            ruta, hash_pdf, estado="borrador", detalle=detalle_fallo or ""
        )

    # Gemini corre fuera de una transacción. Antes de aplicar el resultado se
    # vuelve a cerrar la ventana de una confirmación/descartado concurrente.
    try:
        datos_vigentes = leer_borrador(con, hash_pdf)
    except ValueError:
        return ResultadoPipeline(ruta, hash_pdf, estado="ya_procesada")
    factura.hash_pdf = hash_pdf
    factura.ruta_pdf = datos_vigentes["ruta_pdf"]
    factura.ruta_evidencia = datos_vigentes["ruta_evidencia"]
    guardar_factura(
        con,
        factura,
        estado="borrador",
        actor=actor,
        texto_extraido=datos_vigentes["texto_extraido"],
        motivo_decision="reintento Gemini exitoso",
    )
    return ResultadoPipeline(ruta, hash_pdf, estado="borrador")


@_transaccional
def confirmar_factura(
    con: duckdb.DuckDBPyConnection,
    factura: FacturaExtraida,
    *,
    diccionario: dict[str, list[str]] | None = None,
    total_impreso: float | None = None,
    fuente_total_impreso: str = "automatica",
    actor: str = "sistema",
    permitir_duplicado: bool = False,
    motivo_duplicado: str | None = None,
) -> str:
    """Guarda como DEFINITIVA una factura editada en la pantalla de
    confirmación (`apps/segurplus/paginas/confirmar.py`) -- re-homologa con
    las descripciones YA CORREGIDAS, no con lo que devolvió Gemini (si el
    usuario arregló "Cargo Fijo (100,00 / 30 x 60)" a "Cargo Fijo",
    homologa mejor que el original). Devuelve el estado final ("aprobada"
    o "requiere_revision", según `data/operacion.yaml::revision_humana_obligatoria`).

    Dos controles, redundantes a propósito con lo que la pantalla ya
    bloquea en el botón "Confirmar" -- CLAUDE.md, "nunca mostrarle a un
    usuario un número no verificado": esta función es la última puerta
    antes de escribir, no solo la pantalla.

    - `periodo_desde` y `servicio` tienen que estar completos (docs/
      auditoria-2026-09.md, A-3; docs/auditoria-2026-09-facturas-reales.md,
      B-1): toda consulta del análisis filtra por los dos.
    - La aritmética tiene que cerrar (`validar_factura`, con la misma
      doble lectura del total que usaba el pipeline viejo -- pasar
      `total_impreso` si se tiene, calculado por la pantalla sobre
      `facturas.texto_extraido`).

    docs/auditoria-2026-09-confirmacion.md, D-5/D-6/D-7: esta función es la
    AUTORIDAD sobre el estado y la procedencia de la factura, no un
    receptor confiado de lo que arme la pantalla. `leer_borrador` (que ya
    lanza `ValueError` si el hash no existe o no está en `'borrador'`)
    cierra la ventana de doble confirmación -- sin esto, confirmar dos
    veces la misma factura (una pestaña vieja, una carrera con `revision.py`
    editando la cabecera) pisaba en silencio lo ya confirmado. Los campos de
    procedencia (`ruta_evidencia`, `respuesta_extraida`, `modelo_extraccion`,
    `version_prompt`, `version_esquema`) se toman de ahí, NUNCA de `factura`
    -- si el llamador arma el objeto sin ellos (como ya hace algún test con
    `dataclasses.replace`), antes se perdía en silencio el vínculo con el
    PDF de evidencia.

    D-4: lo que corrigió la persona al confirmar queda registrado -- los
    campos de cabecera que cambiaron respecto del borrador, vía
    `core.almacenamiento.registrar_correccion` (uno por campo, con su valor
    anterior); las líneas de conceptos/impuestos/recargos/créditos no
    tienen tabla de corrección propia, así que si cambiaron se deja
    constancia en el propio motivo del evento "confirmacion"."""
    if fuente_total_impreso not in {"automatica", "manual"}:
        raise ValueError("Fuente de verificación del total inválida.")
    if factura.periodo_desde is None or factura.servicio is None:
        raise ValueError("No se puede confirmar sin período y servicio.")
    try:
        inicio = date.fromisoformat(factura.periodo_desde)
        fin = date.fromisoformat(factura.periodo_hasta) if factura.periodo_hasta else None
    except ValueError as exc:
        raise ValueError("El período debe tener fechas válidas AAAA-MM-DD.") from exc
    if fin is not None and fin < inicio:
        raise ValueError("El período hasta no puede ser anterior al período desde.")
    if factura.moneda != "ARS":
        raise ValueError("Solo se pueden confirmar facturas en ARS; revisá la moneda.")
    if factura.cuit and factura.numero_comprobante:
        identidad = (
            f"{''.join(c for c in factura.cuit if c.isdigit())}:"
            f"{''.join(c for c in factura.numero_comprobante.upper() if c.isalnum())}:"
            f"{factura.periodo_desde}"
        )
        if isinstance(con, ConexionPostgres):
            clave_lock = int.from_bytes(
                hashlib.sha256(identidad.encode()).digest()[:8], "big", signed=True
            )
            con.execute("SELECT pg_advisory_xact_lock(?)", [clave_lock])
    coincidente = duplicado_de_negocio(con, factura)
    if coincidente and (not permitir_duplicado or not (motivo_duplicado or "").strip()):
        raise ValueError(
            "Ya hay una factura aprobada con igual CUIT, comprobante y período "
            f"(PDF {coincidente[:10]}). Revisá el duplicado; para aprobar una "
            "excepción, marcala y explicá el motivo."
        )

    datos_borrador = leer_borrador(con, factura.hash_pdf)
    if os.environ.get("SEGURPLUS_PRODUCTION") == "1":
        evidencia = leer_pdf(datos_borrador["ruta_evidencia"], con=con)
        if evidencia is None or hashlib.sha256(evidencia).hexdigest() != factura.hash_pdf:
            raise ValueError("No se puede aprobar sin el PDF original verificable.")

    resultado_validacion = validar_factura(factura, total_impreso=total_impreso)
    if not resultado_validacion.factura_valida:
        raise ValueError(
            "La factura no cierra aritméticamente: "
            + "; ".join(resultado_validacion.motivos_de_falla())
        )

    factura = replace(
        factura,
        ruta_evidencia=datos_borrador["ruta_evidencia"],
        respuesta_extraida=datos_borrador["respuesta_extraida"],
        modelo_extraccion=datos_borrador["modelo_extraccion"],
        version_prompt=datos_borrador["version_prompt"],
        version_esquema=datos_borrador["version_esquema"],
    )

    # D-4: se compara ANTES de guardar -- después, `datos_borrador` seguiría
    # disponible en memoria, pero es más claro dejarlo junto a la lectura.
    # `registrar_correccion` exige que el estado en la base ya sea
    # aprobada/requiere_revision (docs/auditoria-2026-09-facturas-reales.md,
    # A-50), así que las llamadas van DESPUÉS de `guardar_factura`.
    campos_corregidos = [
        campo
        for campo in _CAMPOS_CABECERA_CORREGIBLE
        if getattr(factura, campo) != datos_borrador[campo]
    ]
    # docs/auditoria-2026-09-web.md, E-19: `datos_borrador["conceptos"]`
    # ahora trae `concepto_sugerido` como sexto campo -- se separa acá
    # (`conceptos_viejos` se queda con los primeros 5, para que esta
    # comparación siga siendo la misma de siempre) y se usa más abajo,
    # de respaldo cuando Dice no homologa nada.
    conceptos_viejos_completos = datos_borrador["conceptos"]
    conceptos_viejos = [fila[:5] for fila in conceptos_viejos_completos]
    sugeridos_por_indice = {
        i: fila[5] for i, fila in enumerate(conceptos_viejos_completos) if fila[5]
    }
    conceptos_nuevos = [
        (c.descripcion, c.cantidad, c.unidad, c.precio_unitario, c.importe)
        for c in factura.conceptos
    ]
    montos_viejos = (
        datos_borrador["impuestos"] + datos_borrador["recargos"] + datos_borrador["creditos"]
    )
    montos_nuevos = [
        (m.nombre, m.importe) for m in (*factura.impuestos, *factura.recargos, *factura.creditos)
    ]
    lineas_corregidas = conceptos_nuevos != conceptos_viejos or montos_nuevos != montos_viejos

    diccionario_a_usar = (
        diccionario if diccionario is not None else cargar_diccionario(factura.servicio)
    )
    conceptos_normalizados = {}
    scores_homologacion = {}
    motivos_homologacion = {}
    candidatos_empatados = {}
    for i, c in enumerate(factura.conceptos):
        resultado_homologacion = homologar_concepto(c.descripcion, diccionario_a_usar)
        # El score se guarda SIEMPRE, haya homologado o no -- el de las que
        # no homologaron es el dato que permite calibrar (ver
        # core/almacenamiento.py::guardar_factura).
        scores_homologacion[i] = resultado_homologacion.score
        if resultado_homologacion.candidatos_empatados:
            motivos_homologacion[i] = "empate"
            candidatos_empatados[i] = ", ".join(resultado_homologacion.candidatos_empatados)
        if resultado_homologacion.concepto:
            conceptos_normalizados[i] = resultado_homologacion.concepto
        elif not resultado_homologacion.candidatos_empatados:
            # docs/auditoria-2026-09-web.md, E-19: Dice no encontró nada,
            # pero el modelo ya había sugerido un concepto al extraer
            # (`c.concepto_sugerido`, o el que quedó guardado en el
            # borrador si la línea no se tocó al confirmar). Se usa SOLO
            # si esa sugerencia pertenece al diccionario DEL SERVICIO de
            # esta factura -- una sugerencia de otro servicio (o de un
            # slug que ya no existe en `data/conceptos/*.yaml`) se
            # descarta, no se homologa a ciegas.
            sugerido = c.concepto_sugerido or sugeridos_por_indice.get(i)
            if sugerido and sugerido in diccionario_a_usar:
                conceptos_normalizados[i] = sugerido
                motivos_homologacion[i] = "sugerido_por_modelo"

    estado = "requiere_revision" if revision_humana_obligatoria() else "aprobada"
    motivo_decision = f"confirmado, doble lectura {fuente_total_impreso} del total"
    if lineas_corregidas:
        motivo_decision += ", con líneas de conceptos o montos corregidas"
    guardar_factura(
        con,
        factura,
        conceptos_normalizados=conceptos_normalizados,
        scores_homologacion=scores_homologacion,
        motivos_homologacion=motivos_homologacion,
        candidatos_empatados=candidatos_empatados,
        estado=estado,
        actor=actor,
        # D-5: se preservan -- texto_extraido sigue siendo el respaldo
        # visual/de doble lectura, y motivo_carga documenta que esta
        # factura llegó rota, justo cuando más importa saberlo (una vez
        # que pasa a ser un dato bueno). Sin esto, confirmar los borraba.
        texto_extraido=datos_borrador["texto_extraido"],
        motivo_carga=datos_borrador["motivo_carga"],
        motivo_decision=motivo_decision,
    )
    if coincidente:
        con.execute(
            """INSERT INTO excepciones_duplicado
               (hash_pdf, hash_coincidente, actor, motivo) VALUES (?, ?, ?, ?)
               ON CONFLICT (hash_pdf) DO NOTHING""",
            [factura.hash_pdf, coincidente, actor, motivo_duplicado.strip()],
        )
    # El valor anterior se leyó antes de guardar la versión confirmada.
    for campo in campos_corregidos:
        registrar_correccion_conocida(
            con,
            hash_pdf=factura.hash_pdf,
            campo=campo,
            valor_anterior=datos_borrador[campo],
            valor_nuevo=getattr(factura, campo),
            motivo="corrección al confirmar la carga",
            actor=actor,
        )
    guardar_alertas(con, factura.hash_pdf, alertas_por_item_duplicado(factura))
    if estado == "aprobada":
        sincronizar_casos_de_factura(con, factura.hash_pdf)
        encolar_comparaciones(con, factura.servicio)
    return estado


def _guardar_borrador_con_evidencia(
    con,
    factura: FacturaExtraida,
    *,
    contenido_pdf: bytes,
    actor: str,
    texto_extraido: str,
    motivo_carga: str | None = None,
    clasificacion: tuple[bool, str] | None = None,
) -> None:
    """Guarda DB y evidencia de forma recuperable ante un rollback SQL."""
    try:
        with transaccion(con):
            if clasificacion is not None:
                apto_gemini, via = clasificacion
                registrar_clasificacion_documento(
                    con,
                    hash_pdf=factura.hash_pdf,
                    apto_gemini=apto_gemini,
                    actor=actor,
                    via=via,
                )
            factura.ruta_evidencia = guardar_pdf(factura.hash_pdf, contenido_pdf, con=con)
            evidencia = leer_pdf(factura.ruta_evidencia, con=con)
            if evidencia is None or hashlib.sha256(evidencia).hexdigest() != factura.hash_pdf:
                raise RuntimeError("No se pudo verificar el PDF guardado.")
            guardar_factura(
                con,
                factura,
                estado="borrador",
                actor=actor,
                motivo_carga=motivo_carga,
                texto_extraido=texto_extraido,
            )
    except Exception:
        # S3 no participa de la transacción: se compensa solamente si el
        # commit SQL no llegó a ocurrir.
        if factura.ruta_evidencia:
            borrar_pdf(factura.ruta_evidencia, con=con)
        raise
