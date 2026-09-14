"""Orquesta el pipeline completo para UN PDF: ingesta -> extracción con
Gemini -> validación aritmética -> homologación -> guardado (en `facturas`
si es válida, en `cuarentena` si no). Es la única función que la app
Streamlit (o un script de línea de comandos, a futuro) necesita llamar por
factura -- así la UI queda como cáscara fina (CLAUDE.md) y este módulo es
testeable sin Streamlit.

No hace nada nuevo: compone funciones ya escritas y probadas en
`core/ingesta/`, `core/extraccion/` y `core/analisis/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from core.almacenamiento import (
    factura_ya_procesada,
    guardar_alertas,
    guardar_en_cuarentena,
    guardar_factura,
    llamadas_ultima_hora,
    proxima_ventana_libre,
    registrar_intento_gemini,
    sincronizar_casos_de_factura,
)
from core.analisis.alertas import alertas_por_item_duplicado
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import homologar_concepto
from core.evidencia import guardar_pdf
from core.extraccion.gemini import ExtraccionError, extraer_con_gemini
from core.extraccion.validacion import validar_factura
from core.ingesta.pdf_texto import PdfSinTextoError, extraer_texto, total_impreso
from core.operacion import max_llamadas_gemini_por_hora, revision_humana_obligatoria


@dataclass
class ResultadoPipeline:
    ruta: Path
    hash_pdf: str
    # "ya_procesada" | "cuarentena" | "guardada" | "necesita_datos" | "error_extraccion"
    # -- "necesita_datos" (docs/auditoria-2026-09-piloto.md, hallazgo B-1): la
    # factura se guardó y validó aritméticamente, pero le falta `periodo_desde`
    # o `servicio`, los dos campos que el análisis usa para filtrar -- sin
    # completarlos queda invisible en todo el tablero. Deliberadamente
    # DISTINTO de "guardada": la UI no debe mostrarlo como éxito.
    estado: str
    detalle: str = ""


def procesar_pdf(
    ruta: Path,
    con: duckdb.DuckDBPyConnection,
    *,
    api_key: str | None = None,
    diccionario: dict[str, list[str]] | None = None,
) -> ResultadoPipeline:
    """Procesa un único PDF de punta a punta. No lanza excepciones para
    errores esperables del pipeline (PDF sin texto, extracción fallida,
    factura que no valida) -- esos casos se reportan en `ResultadoPipeline`,
    no cortan el procesamiento de los demás PDFs de un lote.

    `diccionario`: si se pasa explícito, se usa tal cual (útil para tests).
    Si no, se carga DESPUÉS de la extracción, acotado al `servicio` de la
    factura (ver `core.analisis.diccionario.cargar_diccionario`, hallazgo
    A-3) -- antes de este fix se cargaba upfront, combinando TODOS los
    servicios, porque en ese punto del pipeline todavía no se sabía de qué
    servicio era la factura."""
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
        # docs/auditoria-2026-09.md, hallazgo A-7 -- y docs/auditoria-2026-09-
        # piloto.md, B-4: el tope ahora se hace cumplir contra llamadas
        # REALES (tabla intentos_gemini), no contra un proxy que subestimaba
        # el uso real cuando la extracción fallaba. El mensaje dice A QUÉ
        # HORA reintentar, no solo que se alcanzó el tope.
        destrabe = proxima_ventana_libre(con)
        detalle = f"Se alcanzó el tope de {tope} llamadas a Gemini por hora"
        detalle += (
            f" -- probá de nuevo después de las {destrabe.strftime('%H:%M')}."
            if destrabe is not None
            else " -- probá de nuevo más tarde."
        )
        return ResultadoPipeline(
            ruta, documento.hash_sha256, estado="error_extraccion", detalle=detalle
        )

    contenido_pdf = ruta.read_bytes()
    try:
        # docs/auditoria-2026-09-piloto.md, hallazgo B-3: se le pasa también
        # el texto que `extraer_texto` ya sacó del mismo PDF -- una segunda
        # vista, además del PDF nativo, para las facturas con layout a dos
        # columnas o líneas de impuesto con dos montos.
        factura = extraer_con_gemini(contenido_pdf, api_key=api_key, texto_extraido=documento.texto)
    except ExtraccionError as exc:
        # docs/auditoria-2026-09-facturas-reales.md, hallazgo B-5: antes esto
        # no dejaba NINGÚN rastro en la base -- se perdía al recargar la
        # página, y el usuario no tenía forma de contar qué pasó.
        # respuesta_cruda (hallazgo C-2): si Gemini SÍ llegó a responder
        # (un JSON válido pero incompleto, ej. sin "descripcion" en un
        # concepto), queda igual disponible acá para diagnosticar -- antes
        # ese caso ni siquiera llegaba a este except (ver ExtraccionError).
        registrar_intento_gemini(
            con,
            hash_pdf=documento.hash_sha256,
            ruta_pdf=str(ruta),
            exito=False,
            mensaje=str(exc),
            respuesta_cruda=getattr(exc, "respuesta_cruda", None),
        )
        return ResultadoPipeline(
            ruta, documento.hash_sha256, estado="error_extraccion", detalle=str(exc)
        )
    registrar_intento_gemini(
        con,
        hash_pdf=documento.hash_sha256,
        ruta_pdf=str(ruta),
        exito=True,
        respuesta_cruda=factura.respuesta_extraida,
    )

    factura.hash_pdf = documento.hash_sha256
    factura.ruta_pdf = str(ruta)
    try:
        factura.ruta_evidencia = guardar_pdf(documento.hash_sha256, contenido_pdf)
    except Exception as exc:  # noqa: BLE001 -- no se acepta evidencia silenciosamente rota
        return ResultadoPipeline(
            ruta,
            documento.hash_sha256,
            estado="error_extraccion",
            detalle=f"No se pudo guardar evidencia: {exc}",
        )

    resultado = validar_factura(factura, total_impreso=total_impreso(documento.texto))

    if not resultado.factura_valida:
        guardar_en_cuarentena(
            con,
            hash_pdf=factura.hash_pdf,
            ruta_pdf=factura.ruta_pdf,
            resultado=resultado,
            ruta_evidencia=factura.ruta_evidencia,
            emisor=factura.emisor,
            servicio=factura.servicio,
        )
        return ResultadoPipeline(
            ruta,
            factura.hash_pdf,
            estado="cuarentena",
            detalle="; ".join(resultado.motivos_de_falla()),
        )

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

    # docs/auditoria-2026-09-piloto.md, hallazgo B-1: una factura sin
    # `periodo_desde` o sin `servicio` valida bien aritméticamente y antes
    # quedaba "aprobada" (con el default) o "requiere_revision" -- pero
    # TODAS las consultas del análisis (totales por período, calibración,
    # re-homologación) filtran por esos dos campos, así que quedaba
    # invisible en todo el tablero sin un solo aviso: la UI decía en verde
    # "guardada y validada" y no había forma de encontrarla ni siquiera en
    # "Sin clasificar" (que también filtra por período). Si falta
    # cualquiera de los dos, la factura NUNCA queda aprobada directo
    # -- pase lo que pase con `revision_humana_obligatoria` -- para que el
    # usuario la vea y la complete desde "Revisar facturas" (`registrar_correccion`
    # ya acepta corregir `periodo_desde` y `servicio` de una pendiente).
    datos_faltantes = []
    if factura.periodo_desde is None:
        datos_faltantes.append("periodo_desde")
    if factura.servicio is None:
        datos_faltantes.append("servicio")

    # Si `data/operacion.yaml::revision_humana_obligatoria` está en true, no
    # alcanza para impactar el análisis sin que alguien la apruebe -- ver
    # apps/segurplus/paginas/revision.py. Si está en false (el default de
    # este piloto), queda aprobada directo; la auditoría de quién cargó qué
    # se escribe igual en los dos casos (ver guardar_factura).
    factura_queda_aprobada = not datos_faltantes and not revision_humana_obligatoria()
    guardar_factura(
        con,
        factura,
        conceptos_normalizados=conceptos_normalizados,
        scores_homologacion=scores_homologacion,
        motivos_homologacion=motivos_homologacion,
        candidatos_empatados=candidatos_empatados,
        estado="aprobada" if factura_queda_aprobada else "requiere_revision",
    )

    # Ítem duplicado se calcula UNA VEZ acá, sobre la factura individual --
    # no en la página de evolución (que ve una factura agregada sin
    # conceptos propios, ver docs/auditoria-2026-09.md, hallazgo A-6).
    guardar_alertas(con, factura.hash_pdf, alertas_por_item_duplicado(factura))
    if factura_queda_aprobada:
        # decision_factura ya hace esto mismo cuando la aprobación pasa por
        # la revisión humana -- acá hace falta el mismo llamado porque la
        # factura nunca pasa por decision_factura cuando queda aprobada
        # directo (ver el comentario de arriba). Sin esto, las alertas de
        # ítem duplicado de una factura aprobada directo no se convertían
        # nunca en un caso operativo.
        sincronizar_casos_de_factura(con, factura.hash_pdf)
    if datos_faltantes:
        return ResultadoPipeline(
            ruta,
            factura.hash_pdf,
            estado="necesita_datos",
            detalle=(
                f"Se guardó pero falta completar: {', '.join(datos_faltantes)} -- "
                'corregilo en "Revisar facturas" para que entre al análisis.'
            ),
        )
    return ResultadoPipeline(ruta, factura.hash_pdf, estado="guardada")
