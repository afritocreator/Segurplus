"""Homologación de conceptos: mapea la descripción literal de una línea de
factura (que cada proveedor escribe distinto — "ABONO LINEA MOVIL", "Abono
Plan Control", "Cargo fijo móvil") a un concepto normalizado ("abono_movil"),
para poder comparar el mismo concepto entre facturas y entre períodos.

Port de `lib/invoice/match.ts` de Kleric- (similitud de bigramas, coeficiente
de Dice) — ahí se usa para emparejar una línea de factura contra un catálogo
de productos; acá contra un diccionario de conceptos normalizados por
servicio, cargado desde `data/conceptos/*.yaml` (nunca hardcodeado, ver
CLAUDE.md).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

RUTA_HOMOLOGACION = Path(__file__).resolve().parents[2] / "data" / "homologacion.yaml"


def _umbral_coincidencia() -> float:
    """Sin cache y sin lectura a nivel de módulo, a propósito -- un YAML
    corrupto no debe tumbar el import ni la app Streamlit (mismo patrón que
    `core/analisis/alertas.py::_leer_umbrales`). Ver `data/homologacion.yaml`
    para el valor y por qué es provisorio (docs/auditoria-2026-09.md, A-3)."""
    datos = yaml.safe_load(RUTA_HOMOLOGACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "umbral_coincidencia" not in datos:
        raise ValueError(
            f"{RUTA_HOMOLOGACION} no tiene la forma esperada (falta 'umbral_coincidencia')"
        )
    return float(datos["umbral_coincidencia"])


def normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")  # saca acentos
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


_MESES = (
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
    r"octubre|noviembre|diciembre|ene|feb|mar|abr|jun|jul|ago|sep|set|oct|nov|dic"
)


def quitar_periodo(texto: str) -> str:
    """Saca de `texto` (ya pasado por `normalizar`, o texto crudo) lo que sea
    inequívocamente un PERÍODO de facturación -- nombre de mes (completo o
    abreviado) y año de 4 dígitos, sueltos o pegados ("agosto 2026", "08/2026",
    "ago-2026") -- y nada más.

    Por qué existe: varios proveedores (Movistar, verificado) facturan el
    mismo concepto con el período pegado a la descripción ("Servicio de
    telefonía Agosto 2026", al mes siguiente "...Septiembre 2026"). Sin esto,
    `normalizar` conserva los dígitos y el mes, dos descripciones del MISMO
    concepto en meses distintos bajan de score 0,588 a 0,444 contra el
    diccionario -- por debajo del umbral (`data/homologacion.yaml`, 0,60) --
    y terminan homologando como "None" cada una por su lado, lo que en
    `core/analisis/agregacion.py` las agrupa bajo CLAVES DISTINTAS (una por
    período). El resultado que ve el usuario: la descomposición precio/
    cantidad interpreta un aumento de PRECIO real como si el concepto de
    agosto hubiera desaparecido y uno nuevo hubiera aparecido en septiembre
    -- efecto_precio queda en 0 y toda la variación cae, falsamente, en
    efecto_cantidad. Justo lo que la herramienta existe para distinguir.

    Deliberadamente CONSERVADORA -- NO saca cualquier número suelto. Sacar
    todo dígito fusionaría "Línea 1"/"Línea 2" o "Medidor 1"/"Medidor 2" en
    un solo concepto, perdiendo la granularidad que una empresa con varias
    líneas o medidores facturados por separado necesita (verificado: la
    versión agresiva colapsa esos casos). "Plan 5GB" vs "Plan 20GB" tampoco
    se tocan: el dígito pegado a una letra (sin espacio) no matchea ningún
    patrón de acá.

    Contrapartida aceptada: un mes abreviado de 3 letras ("mar", "may", "ago")
    puede coincidir con una palabra real dentro de una descripción (ej. "mar"
    en "Mar del Plata"). Se prefiere este costo a no reconocer las
    abreviaturas, que son comunes en facturas reales.

    Guarda: si la descripción es SOLO un período (ej. una descripción rota
    que dijera literalmente "Agosto 2026"), quitar todo dejaría un string
    vacío -- eso colapsaría cualquier par de descripciones así bajo la misma
    clave "(sin_homologar) ", que es peor que no limpiar nada. En ese caso
    se devuelve el texto normalizado sin tocar.
    """
    limpio = normalizar(texto)
    sin_periodo = re.sub(rf"\b({_MESES})\b", " ", limpio)
    sin_periodo = re.sub(r"\b\d{1,2}\s+(19|20)\d{2}\b", " ", sin_periodo)  # "08 2026"
    sin_periodo = re.sub(r"\b(19|20)\d{2}\b", " ", sin_periodo)  # año suelto
    sin_periodo = re.sub(r"\s+", " ", sin_periodo).strip()
    return sin_periodo or limpio


def _bigramas(texto: str) -> set[str]:
    limpio = normalizar(texto)
    return {limpio[i : i + 2] for i in range(len(limpio) - 1)}


def similitud(a: str, b: str) -> float:
    """Coeficiente de Dice entre 0 (nada en común) y 1 (idéntico)."""
    bigramas_a = _bigramas(a)
    bigramas_b = _bigramas(b)
    if not bigramas_a or not bigramas_b:
        return 0.0
    interseccion = len(bigramas_a & bigramas_b)
    return (2 * interseccion) / (len(bigramas_a) + len(bigramas_b))


def homologar_concepto(
    descripcion: str, diccionario: dict[str, list[str]], *, umbral: float | None = None
) -> tuple[str | None, float]:
    """Busca el concepto normalizado más parecido a `descripcion` dentro de
    `diccionario` ({concepto_normalizado: [alias, alias, ...]}).

    `umbral`: por defecto se lee de `data/homologacion.yaml`
    (`umbral_coincidencia`) -- se puede pasar explícito para tests, sin
    depender del archivo.

    Devuelve `(None, score)` si el mejor score queda por debajo del umbral —
    eso NO se descarta silenciosamente: en `core/analisis/alertas.py` se
    convierte en la alerta "concepto nuevo sin clasificar", que suele ser
    justo el cargo que se coló.

    La comparación corre sobre `quitar_periodo(descripcion)` contra
    `quitar_periodo(candidato)` de cada lado -- ver esa función para el
    porqué (un mes/año pegado a la descripción no debe impedir el match).
    """
    umbral = umbral if umbral is not None else _umbral_coincidencia()
    descripcion_sin_periodo = quitar_periodo(descripcion)
    mejor_concepto = None
    mejor_score = 0.0
    for concepto, alias in diccionario.items():
        candidatos = [concepto, *alias]
        score = max(similitud(descripcion_sin_periodo, quitar_periodo(c)) for c in candidatos)
        if score > mejor_score:
            mejor_score = score
            mejor_concepto = concepto

    if mejor_score < umbral:
        return None, mejor_score
    return mejor_concepto, mejor_score
