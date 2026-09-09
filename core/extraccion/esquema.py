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

from dataclasses import dataclass, field

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
    subtotal: float | None = None
    total: float | None = None
    hash_pdf: str | None = None
    ruta_pdf: str | None = None


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
            "subtotal": {"type": ["number", "null"]},
            "total": {"type": ["number", "null"]},
        },
        "required": ["conceptos", "moneda"],
    }


def factura_desde_json(
    datos: dict, *, hash_pdf: str | None = None, ruta_pdf: str | None = None
) -> FacturaExtraida:
    """Convierte el JSON que devuelve el modelo (o un motor por reglas, a
    futuro) en un `FacturaExtraida`. No valida nada aritméticamente — eso es
    trabajo de `core/extraccion/validacion.py`, a propósito separado."""
    conceptos = [
        Concepto(
            descripcion=c["descripcion"],
            cantidad=float(c.get("cantidad", 1) or 1),
            unidad=c.get("unidad"),
            precio_unitario=float(c["precio_unitario"]),
            importe=float(c["importe"]),
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

    return FacturaExtraida(
        emisor=datos.get("emisor"),
        cuit=datos.get("cuit"),
        servicio=datos.get("servicio"),
        periodo_desde=datos.get("periodo_desde"),
        periodo_hasta=datos.get("periodo_hasta"),
        fecha_emision=datos.get("fecha_emision"),
        fecha_vencimiento=datos.get("fecha_vencimiento"),
        numero_comprobante=datos.get("numero_comprobante"),
        moneda=datos.get("moneda", "ARS"),
        conceptos=conceptos,
        impuestos=impuestos,
        recargos=recargos,
        subtotal=datos.get("subtotal"),
        total=datos.get("total"),
        hash_pdf=hash_pdf,
        ruta_pdf=ruta_pdf,
    )
