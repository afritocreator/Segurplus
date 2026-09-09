"""Ingesta de un PDF de factura: extracción de texto plano con pdfplumber,
detección de PDF sin capa de texto (foto escaneada), hash del archivo para
idempotencia, y la doble lectura del total (regex directa sobre el texto,
independiente de lo que diga el modelo -- ver
`core/extraccion/validacion.py::validar_factura`, parámetro `total_impreso`).

No hace OCR: se confirmó que las facturas de origen son PDFs digitales
(texto seleccionable), así que un PDF sin capa de texto es un caso
anómalo -- se manda a cuarentena con un mensaje claro en vez de devolver
texto vacío en silencio (mismo principio que
`core/ingesta/balance.py` de Consultora: nunca fallar en silencio).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

# "TOTAL" (con o sin "A PAGAR") seguido de un monto. Captura el TOKEN
# NUMÉRICO COMPLETO, con todos sus separadores -- decidir cuál es el
# separador decimal es trabajo de `_parsear_monto`, no de la regex. Antes,
# esta regex exigía el formato argentino exacto (coma decimal con 2
# dígitos) y, ante un total en formato estadounidense ("12,584.00"),
# matcheaba solo un PREFIJO del número ("12,58") -- un valor incorrecto en
# silencio, no un `None` (ver docs/auditoria-2026-09.md, hallazgo A-5).
_PATRON_TOTAL = re.compile(
    r"(?:TOTAL(?:\s+A\s+PAGAR)?)\s*:?\s*\$?\s*(\d+(?:[.,]\d+)*)",
    re.IGNORECASE,
)


class PdfSinTextoError(Exception):
    """El PDF no tiene capa de texto extraíble (probablemente una foto
    escaneada). No se intenta OCR -- ver docstring del módulo."""


@dataclass
class DocumentoPdf:
    ruta: Path
    texto: str
    hash_sha256: str


def hash_archivo(ruta: Path) -> str:
    """SHA-256 del contenido del PDF, para idempotencia: reprocesar la misma
    carpeta no debe duplicar facturas en la base."""
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def extraer_texto(ruta: Path) -> DocumentoPdf:
    """Extrae el texto plano de todas las páginas de un PDF. Lanza
    `PdfSinTextoError` si el PDF no tiene ninguna capa de texto (en vez de
    devolver un string vacío que después falle en silencio más adelante en
    el pipeline)."""
    with pdfplumber.open(ruta) as pdf:
        paginas = [pagina.extract_text() or "" for pagina in pdf.pages]
    texto = "\n".join(paginas).strip()

    if not texto:
        raise PdfSinTextoError(
            f"{ruta.name} no tiene texto extraíble -- ¿es una foto/escaneo? "
            "Este pipeline no hace OCR (ver docstring de core/ingesta/pdf_texto.py); "
            "revisar a mano."
        )

    return DocumentoPdf(ruta=ruta, texto=texto, hash_sha256=hash_archivo(ruta))


def _parsear_monto(token: str) -> float | None:
    """Convierte un token numérico COMPLETO (ya extraído por la regex, con
    todos sus separadores) a float -- nunca a partir de una coincidencia
    parcial. Soporta los formatos que puede imprimir un proveedor real:

    - `1.234,56` -- argentino: punto de miles, coma decimal.
    - `1,234.56` -- estadounidense: coma de miles, punto decimal.
    - `12584,00` / `12584.56` -- sin separador de miles, con decimales.
    - `12584` -- sin separador de miles ni decimales.

    Convención para decidir cuál separador es el decimal: si aparecen
    coma Y punto, el que esté MÁS A LA DERECHA es el decimal (el otro es
    de miles). Si aparece un solo tipo de separador, es decimal solo si
    el último grupo después de él tiene exactamente 2 dígitos (el resto
    de los grupos, si hay más de uno, son de miles); si no, es de miles.
    """
    tiene_coma = "," in token
    tiene_punto = "." in token

    if tiene_coma and tiene_punto:
        if token.rfind(",") > token.rfind("."):
            limpio = token.replace(".", "").replace(",", ".")
        else:
            limpio = token.replace(",", "")
    elif tiene_coma:
        # Un solo tipo de separador presente: es decimal solo si el último
        # grupo tiene exactamente 2 dígitos (ej. "12584,00"); si no
        # (ej. "1,234,567", todos de miles), se descarta como separador de
        # miles. No se contempla mezclar grupos de miles y coma decimal sin
        # punto de por medio (ej. "1,234,56") -- no es un formato real.
        ultimo_grupo = token.rsplit(",", 1)[1]
        limpio = token.replace(",", ".") if len(ultimo_grupo) == 2 else token.replace(",", "")
    elif tiene_punto:
        ultimo_grupo = token.rsplit(".", 1)[1]
        limpio = token if len(ultimo_grupo) == 2 else token.replace(".", "")
    else:
        limpio = token

    try:
        return float(limpio)
    except ValueError:
        return None


def total_impreso(texto: str) -> float | None:
    """Lee el total directamente del texto del PDF con una regex, como
    segunda lectura independiente de la que hace el modelo (doble lectura
    del total, ver `core/extraccion/validacion.py`). Si aparece más de una
    vez (ej. "Total" del período anterior y "Total a pagar" del actual), se
    queda con la ÚLTIMA ocurrencia -- en las facturas de servicio argentinas
    revisadas, el total a pagar real es el que aparece más abajo.

    Devuelve `None` si no matchea nada, o si lo que matcheó no se pudo
    interpretar como un número -- ese control se omite en vez de fallar
    (ver `validar_factura`), es una capa extra, no la única. NUNCA devuelve
    un valor construido a partir de una lectura parcial o dudosa."""
    coincidencias = _PATRON_TOTAL.findall(texto)
    if not coincidencias:
        return None
    return _parsear_monto(coincidencias[-1])
