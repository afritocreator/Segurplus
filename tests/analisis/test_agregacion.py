"""Test de la regla de oro: valor calculado a mano para el promedio
ponderado de precio unitario al agregar dos facturas del mismo servicio."""

import pytest

from core.analisis.agregacion import FilaConcepto, agregar_conceptos


def test_agrega_una_sola_factura_sin_cambios():
    filas = [FilaConcepto("abono_movil", "Abono 4 líneas", cantidad=4, importe=10000.0)]
    resultado = agregar_conceptos(filas)
    assert resultado == {"abono_movil": (4.0, 2500.0)}


def test_promedio_ponderado_calculado_a_mano():
    # Dos facturas del mismo concepto en el mismo período (poco común pero
    # posible: dos números de cliente del mismo servicio).
    # Factura 1: 4 líneas por $10.000 (precio unitario 2.500)
    # Factura 2: 2 líneas por $6.000 (precio unitario 3.000)
    # Promedio ponderado = (10000+6000) / (4+2) = 16000/6 = 2666.666...
    # (NO es el promedio simple de precios: (2500+3000)/2 = 2750, distinto)
    filas = [
        FilaConcepto("abono_movil", "Abono 4 líneas", cantidad=4, importe=10000.0),
        FilaConcepto("abono_movil", "Abono 2 líneas", cantidad=2, importe=6000.0),
    ]
    resultado = agregar_conceptos(filas)
    cantidad, precio = resultado["abono_movil"]
    assert cantidad == pytest.approx(6.0)
    assert precio == pytest.approx(16000.0 / 6.0)


def test_conceptos_sin_homologar_no_se_pierden():
    filas = [FilaConcepto(None, "Cargo raro nunca visto", cantidad=1, importe=500.0)]
    resultado = agregar_conceptos(filas)
    assert len(resultado) == 1
    clave = next(iter(resultado))
    assert "Cargo raro nunca visto" in clave


def test_cantidad_total_cero_no_divide_por_cero():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=0, importe=0.0)]
    resultado = agregar_conceptos(filas)
    assert resultado["cargo_fijo"] == (0.0, 0.0)
