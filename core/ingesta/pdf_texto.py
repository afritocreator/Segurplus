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

# Total en pesos argentinos: "TOTAL" (con o sin acento, con o sin "A PAGAR")
# seguido de un monto en formato argentino ($ 1.234.567,89 o 1234567,89).
_PATRON_TOTAL = re.compile(
    r"(?:TOTAL(?:\s+A\s+PAGAR)?)\s*:?\s*\$?\s*([\d.]+,\d{2})",
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


def _a_float_formato_argentino(monto: str) -> float:
    """'1.234.567,89' -> 1234567.89"""
    return float(monto.replace(".", "").replace(",", "."))


def total_impreso(texto: str) -> float | None:
    """Lee el total directamente del texto del PDF con una regex, como
    segunda lectura independiente de la que hace el modelo (doble lectura
    del total, ver `core/extraccion/validacion.py`). Si aparece más de una
    vez (ej. "Total" del período anterior y "Total a pagar" del actual), se
    queda con la ÚLTIMA ocurrencia -- en las facturas de servicio argentinas
    revisadas, el total a pagar real es el que aparece más abajo.

    Devuelve `None` si no matchea nada -- ese control se omite en vez de
    fallar (ver `validar_factura`), es una capa extra, no la única."""
    coincidencias = _PATRON_TOTAL.findall(texto)
    if not coincidencias:
        return None
    return _a_float_formato_argentino(coincidencias[-1])
