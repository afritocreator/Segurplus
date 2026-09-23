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

import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from core.almacenamiento import (
    factura_ya_procesada,
    guardar_alertas,
    guardar_factura,
    leer_borrador,
    llamadas_ultima_hora,
    proxima_ventana_libre,
    registrar_correccion_conocida,
    registrar_intento_gemini,
    sincronizar_casos_de_factura,
)
from core.analisis.alertas import alertas_por_item_duplicado
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import homologar_concepto
from core.evidencia import guardar_pdf
from core.extraccion.esquema import FacturaExtraida
from core.extraccion.gemini import ExtraccionError, es_error_transitorio, extraer_con_gemini
from core.extraccion.validacion import validar_factura
from core.ingesta.pdf_texto import PdfSinTextoError, extraer_texto
from core.operacion import (
    espera_reintento_gemini_segundos,
    intentos_gemini_por_llamada,
    max_llamadas_gemini_por_hora,
    revision_humana_obligatoria,
    zona_horaria,
)


@dataclass
class ResultadoPipeline:
    ruta: Path
    hash_pdf: str
    # "ya_procesada" | "borrador" | "error_extraccion" -- el pipeline ya NO
    # decide si una factura entra al análisis (ver docstring del módulo).
    # "error_extraccion" queda reservado para lo que ni siquiera se llegó a
    # INTENTAR leer (tope de llamadas por hora, PDF ilegible o sin texto):
    # ahí no hay nada que mostrar en la pantalla de confirmación.
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


def procesar_pdf(
    ruta: Path, con: duckdb.DuckDBPyConnection, *, api_key: str | None = None
) -> ResultadoPipeline:
    """Procesa un único PDF: ingesta + extracción con Gemini, y deja un
    BORRADOR. No lanza excepciones para errores esperables del pipeline
    (PDF sin texto, extracción fallida) -- se reportan en
    `ResultadoPipeline`, no cortan el procesamiento de los demás PDFs de un
    lote."""
    try:
        documento = extraer_texto(ruta)
    except PdfSinTextoError as exc:
        return ResultadoPipeline(ruta, hash_pdf="", estado="error_extraccion", detalle=str(exc))
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

    tope = max_llamadas_gemini_por_hora()
    if llamadas_ultima_hora(con) >= tope:
        # docs/auditoria-2026-09.md, hallazgo A-7 -- y docs/auditoria-2026-
        # 09-facturas-reales.md, B-4/C-8/C-11: el tope se hace cumplir
        # contra llamadas REALES, y el mensaje dice a qué hora reintentar,
        # en la zona horaria del usuario.
        destrabe = proxima_ventana_libre(con, tope=tope)
        detalle = f"Se alcanzó el tope de {tope} llamadas a Gemini por hora"
        if destrabe is not None:
            hora_local = datetime.now(ZoneInfo(zona_horaria())) + destrabe
            detalle += f" -- probá de nuevo después de las {hora_local.strftime('%H:%M')}."
        else:
            detalle += " -- probá de nuevo más tarde."
        return ResultadoPipeline(
            ruta, documento.hash_sha256, estado="error_extraccion", detalle=detalle
        )

    contenido_pdf = ruta.read_bytes()
    factura: FacturaExtraida | None = None
    ultimo_error: ExtraccionError | None = None
    intentos_max = intentos_gemini_por_llamada()
    espera_base = espera_reintento_gemini_segundos()
    for intento in range(1, intentos_max + 1):
        try:
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo B-3: se le pasa también
            # el texto que `extraer_texto` ya sacó del mismo PDF -- una segunda
            # vista, además del PDF nativo, para las facturas con layout a dos
            # columnas o líneas de impuesto con dos montos.
            factura = extraer_con_gemini(
                contenido_pdf, api_key=api_key, texto_extraido=documento.texto
            )
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo C-10: en un
            # intento EXITOSO no se manda respuesta_cruda -- esa misma cadena
            # ya va a facturas.respuesta_extraida vía guardar_factura más abajo.
            registrar_intento_gemini(
                con, hash_pdf=documento.hash_sha256, ruta_pdf=str(ruta), exito=True
            )
            break
        except ExtraccionError as exc:
            ultimo_error = exc
            # docs/auditoria-2026-09-facturas-reales.md, hallazgo B-5/C-2: el
            # intento queda registrado igual, con el JSON crudo si Gemini llegó
            # a responder -- CADA intento, no solo el último, así el tope por
            # hora (max_llamadas_gemini_por_hora) sigue contando llamadas
            # reales (docs/auditoria-2026-09-web.md, E-6).
            registrar_intento_gemini(
                con,
                hash_pdf=documento.hash_sha256,
                ruta_pdf=str(ruta),
                exito=False,
                mensaje=str(exc),
                respuesta_cruda=getattr(exc, "respuesta_cruda", None),
            )
            if intento < intentos_max and es_error_transitorio(exc):
                time.sleep(espera_base * intento)
                continue
            break

    if factura is None:
        # E-6: 503/429/timeout ya se reintentaron y siguieron fallando, o
        # fue un error permanente (sin API key, JSON mal formado) que nunca
        # se reintenta. A diferencia de antes, la factura NO se pierde: se
        # deja un borrador vacío para completar a mano, con el PDF a la
        # vista -- Gemini no pudo leerla, pero el usuario sí puede.
        exc = ultimo_error
        factura = _borrador_vacio(documento.hash_sha256, ruta)
        try:
            factura.ruta_evidencia = guardar_pdf(documento.hash_sha256, contenido_pdf, con=con)
        except Exception:  # noqa: BLE001 -- un borrador vacío sin PDF sigue siendo mejor que nada
            factura.ruta_evidencia = None
        guardar_factura(
            con,
            factura,
            estado="borrador",
            motivo_carga=f"No se pudo leer con Gemini: {exc}",
            texto_extraido=documento.texto,
        )
        return ResultadoPipeline(ruta, factura.hash_pdf, estado="borrador", detalle=str(exc))

    factura.hash_pdf = documento.hash_sha256
    factura.ruta_pdf = str(ruta)
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
            motivo_carga=motivo,
            texto_extraido=documento.texto,
        )
        # D-8: se propaga el detalle -- este borrador SÍ tiene los datos que
        # leyó Gemini (no está vacío), pero cargar.py necesita poder avisar
        # igual que el PDF de evidencia no quedó guardado.
        return ResultadoPipeline(ruta, factura.hash_pdf, estado="borrador", detalle=motivo)

    guardar_factura(con, factura, estado="borrador", texto_extraido=documento.texto)
    return ResultadoPipeline(ruta, factura.hash_pdf, estado="borrador")


def confirmar_factura(
    con: duckdb.DuckDBPyConnection,
    factura: FacturaExtraida,
    *,
    diccionario: dict[str, list[str]] | None = None,
    total_impreso: float | None = None,
    actor: str = "sistema",
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
    if factura.periodo_desde is None or factura.servicio is None:
        raise ValueError("No se puede confirmar sin período y servicio.")

    datos_borrador = leer_borrador(con, factura.hash_pdf)

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
    conceptos_viejos = datos_borrador["conceptos"]
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

    estado = "requiere_revision" if revision_humana_obligatoria() else "aprobada"
    motivo_decision = "confirmado"
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
    # D-4: valor_anterior viene de `datos_borrador` (leído ANTES de guardar)
    # -- `registrar_correccion` releería la columna de `facturas`, que para
    # este punto ya tiene el valor NUEVO (la pisó `guardar_factura` arriba),
    # y daría `valor_anterior == valor_nuevo` en cada fila.
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
    # Ítem duplicado se calcula sobre la factura YA CORREGIDA -- si el
    # usuario arregló una descripción que coincidía con otra por error de
    # lectura, esta alerta no debe seguir disparando sobre el dato viejo.
    guardar_alertas(con, factura.hash_pdf, alertas_por_item_duplicado(factura))
    if estado == "aprobada":
        sincronizar_casos_de_factura(con, factura.hash_pdf)
    return estado
