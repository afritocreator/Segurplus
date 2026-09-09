"""
Genera facturas de servicios ficticias en PDF para los tests de Segurplus,
sin depender de facturas reales -- misma idea que
`docs/fixtures/generate-fixtures.py` de Kleric- (mismo generador con
ReportLab), adaptado a facturas de SERVICIOS: con período facturado,
consumo medido, IVA discriminado, y en algunos casos un cargo por mora.

Genera pares de meses (para poder probar la descomposición precio/cantidad
entre dos períodos) para dos proveedores:

- Telefonía (Comunicaciones Sur S.A.): abono por línea + consumo de datos.
  Entre el mes 1 y el mes 2 aumenta la cantidad de líneas Y el precio del
  abono -- para poder testear que la descomposición separa ambos efectos.
- Energía (Edea Tandil S.A.): cargo fijo + consumo en kWh.

Además genera UNA factura rota a propósito (el importe de una línea no
coincide con cantidad × precio) para probar que el pipeline la manda a
cuarentena en vez de aceptarla.

Requiere: pip install reportlab
Uso: python docs/fixtures/generar_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
SALIDA = HERE / "sintetico"
SALIDA.mkdir(exist_ok=True)


def _money(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def generar_factura_pdf(
    ruta: Path,
    *,
    emisor: str,
    cuit_emisor: str,
    numero: str,
    fecha_emision: str,
    periodo: str,
    items: list[tuple[str, float, str, float, float]],  # (desc, cant, unidad, precio, importe)
    recargo: tuple[str, float] | None = None,
    total_impreso_incorrecto: float | None = None,
) -> dict:
    """Dibuja el PDF y devuelve un dict con los totales calculados a mano,
    para que el test los use como referencia (`emisor`, `subtotal`, `iva`,
    `total`)."""
    c = canvas.Canvas(str(ruta), pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, height - 25 * mm, emisor)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, height - 31 * mm, f"CUIT: {cuit_emisor}")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, height - 42 * mm, f"FACTURA N° {numero}")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, height - 48 * mm, f"Fecha de emisión: {fecha_emision}")
    c.drawString(20 * mm, height - 53 * mm, f"Período facturado: {periodo}")

    y = height - 65 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Cant.")
    c.drawString(35 * mm, y, "Concepto")
    c.drawString(130 * mm, y, "P. Unit.")
    c.drawString(160 * mm, y, "Importe")
    c.line(20 * mm, y - 2 * mm, 190 * mm, y - 2 * mm)

    c.setFont("Helvetica", 9)
    y -= 8 * mm
    subtotal = 0.0
    for desc, cant, unidad, precio, importe in items:
        subtotal += importe
        c.drawString(20 * mm, y, f"{cant:g} {unidad}")
        c.drawString(35 * mm, y, desc)
        c.drawRightString(150 * mm, y, _money(precio))
        c.drawRightString(188 * mm, y, _money(importe))
        y -= 6 * mm

    iva = round(subtotal * 0.21, 2)
    total = subtotal + iva

    if recargo:
        nombre_recargo, importe_recargo = recargo
        c.drawString(35 * mm, y, nombre_recargo)
        c.drawRightString(188 * mm, y, _money(importe_recargo))
        y -= 6 * mm
        total += importe_recargo

    y -= 4 * mm
    c.line(120 * mm, y, 190 * mm, y)
    y -= 6 * mm
    c.drawString(130 * mm, y, "Subtotal:")
    c.drawRightString(188 * mm, y, _money(subtotal))
    y -= 6 * mm
    c.drawString(130 * mm, y, "IVA 21%:")
    c.drawRightString(188 * mm, y, _money(iva))
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 10)
    total_a_imprimir = total_impreso_incorrecto if total_impreso_incorrecto is not None else total
    c.drawString(130 * mm, y, "TOTAL A PAGAR:")
    c.drawRightString(188 * mm, y, _money(total_a_imprimir))

    c.showPage()
    c.save()

    return {"emisor": emisor, "subtotal": subtotal, "iva": iva, "total": total}


def generar_todas() -> None:
    # --- Telefonía: mes 1 (4 líneas a $2.500) y mes 2 (6 líneas a $2.800) ---
    generar_factura_pdf(
        SALIDA / "telefonia_2026-07.pdf",
        emisor="Comunicaciones Sur S.A.",
        cuit_emisor="30-71234567-8",
        numero="0001-00045501",
        fecha_emision="05/07/2026",
        periodo="01/07/2026 al 31/07/2026",
        items=[
            ("Abono 4 líneas móviles", 4, "línea", 2500.0, 10000.0),
            ("Consumo de datos adicional", 8, "GB", 50.0, 400.0),
        ],
    )
    generar_factura_pdf(
        SALIDA / "telefonia_2026-08.pdf",
        emisor="Comunicaciones Sur S.A.",
        cuit_emisor="30-71234567-8",
        numero="0001-00046102",
        fecha_emision="05/08/2026",
        periodo="01/08/2026 al 31/08/2026",
        items=[
            ("Abono 6 líneas móviles", 6, "línea", 2800.0, 16800.0),
            ("Consumo de datos adicional", 8, "GB", 50.0, 400.0),
        ],
        recargo=("Interés por mora factura anterior", 350.0),
    )

    # --- Energía: cargo fijo + consumo en kWh ---
    generar_factura_pdf(
        SALIDA / "energia_2026-07.pdf",
        emisor="Edea Tandil S.A.",
        cuit_emisor="30-98765432-1",
        numero="0002-00089012",
        fecha_emision="10/07/2026",
        periodo="01/07/2026 al 31/07/2026",
        items=[
            ("Cargo fijo", 1, "mes", 3200.0, 3200.0),
            ("Consumo de energía", 450, "kWh", 45.0, 20250.0),
        ],
    )
    generar_factura_pdf(
        SALIDA / "energia_2026-08.pdf",
        emisor="Edea Tandil S.A.",
        cuit_emisor="30-98765432-1",
        numero="0002-00089877",
        fecha_emision="10/08/2026",
        periodo="01/08/2026 al 31/08/2026",
        items=[
            ("Cargo fijo", 1, "mes", 3200.0, 3200.0),
            ("Consumo de energía", 470, "kWh", 52.0, 24440.0),
        ],
    )

    # --- Factura rota a propósito: 5 * 100 != 800 (debería ser 500) ---
    generar_factura_pdf(
        SALIDA / "rota_importe_no_cierra.pdf",
        emisor="Comunicaciones Sur S.A.",
        cuit_emisor="30-71234567-8",
        numero="0001-00099999",
        fecha_emision="05/09/2026",
        periodo="01/09/2026 al 30/09/2026",
        items=[("Abono 5 líneas móviles", 5, "línea", 100.0, 800.0)],
    )

    print(f"Fixtures generadas en {SALIDA}")


if __name__ == "__main__":
    generar_todas()
