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
    sincronizar_casos_de_factura,
)
from core.analisis.alertas import alertas_por_item_duplicado
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import homologar_concepto
from core.evidencia import guardar_pdf
from core.extraccion.gemini import MAX_LLAMADAS_POR_HORA, ExtraccionError, extraer_con_gemini
from core.extraccion.validacion import validar_factura
from core.ingesta.pdf_texto import PdfSinTextoError, extraer_texto, total_impreso
from core.operacion import revision_humana_obligatoria


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

    if llamadas_ultima_hora(con) >= MAX_LLAMADAS_POR_HORA:
        # docs/auditoria-2026-09.md, hallazgo A-7: MAX_LLAMADAS_POR_HORA
        # estaba declarada en core/extraccion/gemini.py y nunca se hacía
        # cumplir -- nada frenaba un loop o un mal uso del tablero de agotar
        # la cuota gratuita de Gemini.
        return ResultadoPipeline(
            ruta,
            documento.hash_sha256,
            estado="error_extraccion",
            detalle=(
                f"Se alcanzó el tope de {MAX_LLAMADAS_POR_HORA} llamadas a Gemini "
                "por hora -- probá de nuevo más tarde."
            ),
        )

    contenido_pdf = ruta.read_bytes()
    try:
        factura = extraer_con_gemini(contenido_pdf, api_key=api_key)
    except ExtraccionError as exc:
        return ResultadoPipeline(
            ruta, documento.hash_sha256, estado="error_extraccion", detalle=str(exc)
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
