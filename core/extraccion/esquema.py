"""Esquema canónico al que se convierte toda factura, sea cual sea el
proveedor o el motor de extracción que la leyó (Gemini o, en el futuro,
reglas por proveedor — ver docs/decisiones/ADR-001-lectura-de-facturas.md).

Extiende el esquema de `lib/invoice/schema.ts` de Kleric- (que solo pedía
descripción/cantidad/precio/importe, pensado para mercadería) con lo que una
factura de SERVICIOS necesita y un almacén no: período de facturación,
consumos medidos (kWh, m³, GB), impuestos discriminados y recargos —
justamente los campos que hacen falta para separar "aumentó por cantidad" de
"aumentó por precio" y para las alertas de mora/recargo.

Se usan dataclasses simples (no Pydantic/Zod: no está en el stack cerrado de
CLAUDE.md) — la validación de estructura la hace `core/extraccion/gemini.py`
al pedirle al modelo un `responseJsonSchema`, y la validación ARITMÉTICA
(la que realmente importa) vive en `core/extraccion/validacion.py`.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

from core.analisis.diccionario import cargar_diccionario

# Mismos nombres que los archivos data/conceptos/*.yaml (docs/auditoria-2026-09.md,
# hallazgo A-3) -- "otro" es el catch-all deliberado sin YAML propio: una factura
# de un servicio no contemplado todavía homologa solo contra comunes.yaml, en vez
# de perder la homologación entera por un valor de texto libre no reconocido.
SERVICIOS_CONOCIDOS = ("telefonia", "energia", "gas", "agua", "seguro", "alquiler", "otro")


@dataclass
class Concepto:
    """Una línea de la factura: un abono, un consumo, un cargo."""

    descripcion: str
    cantidad: float
    unidad: str | None  # "chip", "línea", "kWh", "m³", None si no aplica
    precio_unitario: float
    importe: float
    # Bloque 3 del plan de rediseño de septiembre 2026 (ver docs/estado.md):
    # el modelo propone directamente un concepto_normalizado conocido, en
    # vez de que la homologación posterior por similitud de texto
    # (core/analisis/homologacion.py) sea la ÚNICA fuente. Nunca decide por
    # sí solo -- core/pipeline.py::confirmar_factura sigue corriendo la
    # homologación por Dice como siempre; esto queda como dato adicional
    # para medir con el banco (docs/banco_extraccion.md) si el slug del
    # modelo es mejor, antes de usarlo para algo más que medir. `None` si el
    # modelo no propuso nada o propuso algo fuera del enum conocido (ver
    # `esquema_json_para_modelo`).
    concepto_sugerido: str | None = None


@dataclass
class Impuesto:
    nombre: str  # "IVA 21%", "Ingresos Brutos", "Tasa municipal", ...
    importe: float


@dataclass
class Recargo:
    """Cargos que NO deberían existir en una factura sana — ver alertas.py."""

    nombre: str  # "Interés por mora", "Refacturación", ...
    importe: float


@dataclass
class Credito:
    """Bonificación, nota de crédito o descuento que reduce el total a pagar."""

    nombre: str
    importe: float


@dataclass
class FacturaExtraida:
    """Resultado de extraer una factura, ya en el esquema canónico.

    `hash_pdf` identifica el archivo de origen (SHA-256) para que reprocesar
    la misma carpeta no duplique nada en la base — ver `core/ingesta/pdf_texto.py`.
    """

    emisor: str | None
    cuit: str | None
    servicio: str | None  # "telefonia", "energia", "gas", "agua", "seguro", "alquiler", ...
    periodo_desde: str | None  # ISO "YYYY-MM-DD"
    periodo_hasta: str | None
    fecha_emision: str | None
    fecha_vencimiento: str | None
    numero_comprobante: str | None
    moneda: str
    conceptos: list[Concepto] = field(default_factory=list)
    impuestos: list[Impuesto] = field(default_factory=list)
    recargos: list[Recargo] = field(default_factory=list)
    creditos: list[Credito] = field(default_factory=list)
    subtotal: float | None = None
    total: float | None = None
    hash_pdf: str | None = None
    ruta_pdf: str | None = None
    ruta_evidencia: str | None = None
    modelo_extraccion: str | None = None
    version_prompt: str | None = None
    version_esquema: str | None = None
    respuesta_extraida: str | None = None


def esquema_json_para_modelo() -> dict:
    """Describe el esquema en JSON Schema, para pasárselo a Gemini como
    `responseJsonSchema` (mismo mecanismo que `z.toJSONSchema(...)` en
    Kleric-, pero escrito a mano porque acá no hay Zod)."""
    desc_periodo_desde = "Inicio del período facturado, YYYY-MM-DD"
    desc_periodo_hasta = "Fin del período facturado, YYYY-MM-DD"
    desc_cantidad = (
        "Cantidad facturada (líneas, kWh, m³, minutos...). "
        "1 si el concepto no tiene cantidad explícita"
    )
    desc_recargos = "Intereses por mora, refacturaciones u otros cargos que no son consumo normal"
    # Bloque 3 del plan de rediseño de septiembre 2026: mismo mecanismo que
    # ya usa "servicio" -- un `enum` con TODOS los conceptos normalizados
    # conocidos (de todos los servicios, no solo el de esta factura: acá
    # todavía no se sabe con certeza qué servicio es) le da al modelo la
    # oportunidad de clasificar directamente, en vez de que la homologación
    # por similitud de texto sea la única fuente. `cargar_diccionario()` sin
    # argumento combina TODOS los `data/conceptos/*.yaml` -- no hardcodeado.
    slugs_conocidos = sorted(cargar_diccionario())

    return {
        "type": "object",
        "properties": {
            "emisor": {"type": ["string", "null"], "description": "Nombre del proveedor/emisor"},
            "cuit": {
                "type": ["string", "null"],
                "description": "CUIT del emisor, formato XX-XXXXXXXX-X",
            },
            "servicio": {
                "type": ["string", "null"],
                # Antes era texto libre (solo una descripción, sin `enum`), y el
                # diccionario de homologación ahora se acota por `servicio`
                # (docs/auditoria-2026-09.md, hallazgo A-3) -- un valor fuera de
                # esta lista exacta (ej. "internet" en vez de "telefonia") hace
                # que se pierda TODA la homologación de esa factura salvo
                # comunes.yaml. `enum` restringe al modelo a devolver exactamente
                # uno de estos valores (o null), que son los mismos nombres que
                # los archivos de data/conceptos/*.yaml -- si se agrega un
                # servicio nuevo, hay que agregarlo acá Y crear su YAML.
                "enum": list(SERVICIOS_CONOCIDOS) + [None],
                "description": "Tipo de servicio -- exactamente uno de los valores permitidos",
            },
            "periodo_desde": {"type": ["string", "null"], "description": desc_periodo_desde},
            "periodo_hasta": {"type": ["string", "null"], "description": desc_periodo_hasta},
            "fecha_emision": {
                "type": ["string", "null"],
                "description": "Fecha de emisión, YYYY-MM-DD",
            },
            "fecha_vencimiento": {
                "type": ["string", "null"],
                "description": "Fecha de vencimiento, YYYY-MM-DD",
            },
            "numero_comprobante": {"type": ["string", "null"]},
            "moneda": {"type": "string", "description": "ARS salvo que la factura diga otra cosa"},
            "conceptos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "descripcion": {"type": "string", "description": "Tal como figura impresa"},
                        "cantidad": {"type": "number", "description": desc_cantidad},
                        "unidad": {
                            "type": ["string", "null"],
                            "description": "Unidad de la cantidad, o null",
                        },
                        "precio_unitario": {"type": "number", "description": "Importe / cantidad"},
                        "importe": {"type": "number", "description": "Importe total de la línea"},
                        "concepto_sugerido": {
                            "type": ["string", "null"],
                            "enum": slugs_conocidos + [None],
                            "description": (
                                "Si esta línea corresponde claramente a uno de estos "
                                "conceptos normalizados conocidos, cuál -- null si no "
                                "estás seguro o es un concepto nuevo que no está en "
                                "la lista. No inventes un valor fuera de esta lista."
                            ),
                        },
                    },
                    "required": ["descripcion", "cantidad", "precio_unitario", "importe"],
                },
            },
            "impuestos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nombre": {"type": "string"}, "importe": {"type": "number"}},
                    "required": ["nombre", "importe"],
                },
            },
            "recargos": {
                "type": "array",
                "description": desc_recargos,
                "items": {
                    "type": "object",
                    "properties": {"nombre": {"type": "string"}, "importe": {"type": "number"}},
                    "required": ["nombre", "importe"],
                },
            },
            "creditos": {
                "type": "array",
                "description": "Bonificaciones, descuentos o notas de crédito que reducen el total",
                "items": {
                    "type": "object",
                    "properties": {"nombre": {"type": "string"}, "importe": {"type": "number"}},
                    "required": ["nombre", "importe"],
                },
            },
            "subtotal": {"type": ["number", "null"]},
            "total": {"type": ["number", "null"]},
        },
        "required": ["conceptos", "moneda"],
    }


_PATRON_FECHA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PATRON_FECHA_ARGENTINA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_PATRON_FECHA_ARGENTINA_GUION = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
# Solo mes/año -- lo único que imprimen muchas facturas de servicios
# argentinas ("Período: 07/2022"), sin día (docs/auditoria-2026-09-piloto.md,
# hallazgo B-1: dos facturas reales de luz de la Usina Popular de Tandil
# solo traen esto, así que `periodo_desde` quedaba en `None` -- una factura
# que VALIDA BIEN, se guarda, y desaparece de todo el análisis porque las
# consultas filtran `periodo_desde IS NOT NULL`, sin ningún aviso).
_PATRON_MES_ANIO = re.compile(r"^(\d{1,2})/(\d{4})$")
_PATRON_ANIO_MES = re.compile(r"^(\d{4})-(\d{1,2})$")


def _normalizar_fecha(valor: object, *, fin_de_mes: bool = False) -> str | None:
    """Normaliza una fecha devuelta por el modelo a ISO `YYYY-MM-DD`, o
    `None` si no se puede interpretar (docs/auditoria-2026-09.md, hallazgo
    A-11).

    El prompt de extracción pide ISO, pero un LLM puede devolver el formato
    que ve impreso en la factura -- en Argentina, típicamente `DD/MM/YYYY`
    o `DD-MM-YYYY` (supuesto explícito: SIEMPRE día/mes/año, nunca
    mes/día/año). También acepta `MM/YYYY` y `YYYY-MM` -- solo mes y año,
    sin día (docs/auditoria-2026-09-facturas-reales.md, hallazgo B-1): se
    normaliza al primer o al último día de ese mes según `fin_de_mes`, una
    simplificación deliberada (no se intenta adivinar si el dato real era
    otro día del mes) que alcanza para que la factura entre al análisis por
    período en vez de quedar invisible. Sin este normalizador, un valor sin
    interpretar llega intacto hasta `evolucion.py`, que hace
    `date.fromisoformat(...)` y lanza `ValueError` -- capturado ahí por un
    `except` genérico que muestra un mensaje de error que no dice que el
    problema es el formato de la fecha (ver A-12). Mejor evitarlo en el
    origen: si no se puede interpretar, `None` (que el resto del código ya
    maneja como "dato no disponible") en vez de un string inválido.

    `fin_de_mes`: solo afecta a las dos ramas MM/AAAA y AAAA-MM (una fecha
    completa nunca se toca). `factura_desde_json` lo pasa en `True` SOLO
    para `periodo_hasta` (docs/auditoria-2026-09-facturas-reales.md,
    hallazgo C-1): "Período: 07/2022" cubre julio ENTERO, así que su fin de
    cobertura semánticamente correcto es el último día de julio, no el
    primero. Normalizar `periodo_hasta` al primer día (como se hacía antes,
    igual que `periodo_desde`) dejaba `desde == hasta`, y
    `alertas_por_periodo_faltante` -- que calcula el próximo período
    esperado como `hasta + 1 día` -- terminaba esperando el día 2 del MISMO
    mes: una alerta de "puede faltar un período" en cada par de meses
    consecutivos, aunque no faltara nada."""
    if not isinstance(valor, str) or not valor:
        return None
    if _PATRON_FECHA_ISO.match(valor):
        try:
            date.fromisoformat(valor)
            return valor
        except ValueError:
            return None
    for patron in (_PATRON_FECHA_ARGENTINA, _PATRON_FECHA_ARGENTINA_GUION):
        coincidencia = patron.match(valor)
        if coincidencia:
            dia, mes, anio = (int(x) for x in coincidencia.groups())
            try:
                return date(anio, mes, dia).isoformat()
            except ValueError:
                return None
    coincidencia = _PATRON_MES_ANIO.match(valor)
    if coincidencia:
        mes, anio = (int(x) for x in coincidencia.groups())
        return _fecha_mes_anio(anio, mes, fin_de_mes=fin_de_mes)
    coincidencia = _PATRON_ANIO_MES.match(valor)
    if coincidencia:
        anio, mes = (int(x) for x in coincidencia.groups())
        return _fecha_mes_anio(anio, mes, fin_de_mes=fin_de_mes)
    return None


def _fecha_mes_anio(anio: int, mes: int, *, fin_de_mes: bool) -> str | None:
    try:
        if fin_de_mes:
            ultimo_dia = calendar.monthrange(anio, mes)[1]
            return date(anio, mes, ultimo_dia).isoformat()
        return date(anio, mes, 1).isoformat()
    except ValueError:
        return None


def factura_desde_json(
    datos: dict, *, hash_pdf: str | None = None, ruta_pdf: str | None = None
) -> FacturaExtraida:
    """Convierte el JSON que devuelve el modelo (o un motor por reglas, a
    futuro) en un `FacturaExtraida`. No valida nada aritméticamente — eso es
    trabajo de `core/extraccion/validacion.py`, a propósito separado."""
    slugs_conocidos = cargar_diccionario()
    conceptos = [
        Concepto(
            descripcion=c["descripcion"],
            # Un cero explícito puede indicar una lectura defectuosa y debe
            # llegar intacto a validación/revisión, nunca convertirse en 1.
            cantidad=float(1 if c.get("cantidad") is None else c["cantidad"]),
            unidad=c.get("unidad"),
            precio_unitario=float(c["precio_unitario"]),
            importe=float(c["importe"]),
            # Defensivo: el `enum` del JSON Schema ya restringe al modelo,
            # pero no todos los proveedores de `core/extraccion/proveedores/`
            # hacen cumplir el schema tan estrictamente como Gemini -- un
            # valor fuera de la lista conocida se descarta a None en vez de
            # dejar pasar un slug inventado que después rompería la
            # homologación en silencio.
            concepto_sugerido=(
                c.get("concepto_sugerido")
                if c.get("concepto_sugerido") in slugs_conocidos
                else None
            ),
        )
        for c in datos.get("conceptos", [])
    ]
    impuestos = [
        Impuesto(nombre=i["nombre"], importe=float(i["importe"]))
        for i in datos.get("impuestos", [])
    ]
    recargos = [
        Recargo(nombre=r["nombre"], importe=float(r["importe"])) for r in datos.get("recargos", [])
    ]
    creditos = [
        Credito(nombre=c["nombre"], importe=float(c["importe"])) for c in datos.get("creditos", [])
    ]

    return FacturaExtraida(
        emisor=datos.get("emisor"),
        cuit=datos.get("cuit"),
        servicio=datos.get("servicio"),
        periodo_desde=_normalizar_fecha(datos.get("periodo_desde")),
        periodo_hasta=_normalizar_fecha(datos.get("periodo_hasta"), fin_de_mes=True),
        fecha_emision=_normalizar_fecha(datos.get("fecha_emision")),
        fecha_vencimiento=_normalizar_fecha(datos.get("fecha_vencimiento")),
        numero_comprobante=datos.get("numero_comprobante"),
        moneda=datos.get("moneda", "ARS"),
        conceptos=conceptos,
        impuestos=impuestos,
        recargos=recargos,
        creditos=creditos,
        subtotal=datos.get("subtotal"),
        total=datos.get("total"),
        hash_pdf=hash_pdf,
        ruta_pdf=ruta_pdf,
    )


def _texto_o_none(valor: object) -> str | None:
    """`None`/`NaN`/blanco -> `None`; el resto, recortado. `or ""` no
    alcanza para esto: `NaN` es *truthy* en Python, así que una celda vacía
    que `st.data_editor` deja como `float("nan")` (una fila nueva del
    editor sin completar) pasaba de largo -- `str(float("nan"))` da la
    cadena `"nan"`, no una cadena vacía, y esa fila entraba al análisis
    como un concepto real llamado "nan" (ver docs/auditoria-2026-09-
    confirmacion.md, D-1)."""
    if valor is None or (isinstance(valor, float) and valor != valor):  # NaN != NaN
        return None
    texto = str(valor).strip()
    return texto or None


def _numero(valor: object, *, default: float = 0.0) -> float:
    """Igual criterio que `_texto_o_none` para campos numéricos: `None`/`NaN`
    da `default`, nunca `float("nan")` propagado a `Concepto`/`Impuesto`
    (que después haría que el control aritmético mostrara "nan × $nan" en
    vez de bloquear con un mensaje claro -- D-17)."""
    if valor is None or (isinstance(valor, float) and valor != valor):
        return default
    return float(valor)


def conceptos_desde_filas(filas: list[dict]) -> list[Concepto]:
    """Convierte filas editadas a mano (ej. en un `st.data_editor` de
    `apps/segurplus/paginas/confirmar.py`) a `Concepto` -- descarta las
    filas sin descripción (vacías, agregadas de más en un editor
    dinámico). Sin depender de pandas: recibe `list[dict]`
    (`DataFrame.to_dict("records")`), no el DataFrame en sí -- `core/` no
    tiene por qué saber que la UI usa pandas."""
    conceptos = []
    for fila in filas:
        descripcion = _texto_o_none(fila.get("descripcion"))
        if descripcion is None:
            continue
        conceptos.append(
            Concepto(
                descripcion=descripcion,
                cantidad=_numero(fila.get("cantidad")),
                unidad=_texto_o_none(fila.get("unidad")),
                precio_unitario=_numero(fila.get("precio_unitario")),
                importe=_numero(fila.get("importe")),
            )
        )
    return conceptos


def montos_desde_filas(filas: list[dict], clase: type) -> list:
    """Igual que `conceptos_desde_filas`, para `Impuesto`/`Recargo`/
    `Credito` -- las tres comparten la misma forma (`nombre` + `importe`).
    Descarta las filas sin nombre."""
    resultado = []
    for fila in filas:
        nombre = _texto_o_none(fila.get("nombre"))
        if nombre is None:
            continue
        resultado.append(clase(nombre=nombre, importe=_numero(fila.get("importe"))))
    return resultado
