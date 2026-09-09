"""Test de la regla de oro: valor calculado a mano para el promedio
ponderado de precio unitario al agregar dos facturas del mismo servicio."""

import pytest

from core.analisis.agregacion import (
    FilaConcepto,
    agregar_conceptos,
    conceptos_con_cantidad_neta_cero,
)


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


def test_cantidad_y_importe_cero_da_cero():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=0, importe=0.0)]
    resultado = agregar_conceptos(filas)
    assert resultado["cargo_fijo"] == (0.0, 0.0)


def test_cantidad_neta_cero_con_importe_neto_tambien_cero_no_es_el_caso_anomalo():
    # Caso simétrico: una nota de crédito que cancela exactamente el cargo
    # original -- cantidad Y el importe neto dan cero. Acá SÍ corresponde
    # (0.0, 0.0): no hay plata que preservar porque el importe neto
    # realmente es cero (la nota de crédito canceló el cargo por completo).
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-4, importe=-10000.0),
    ]
    cantidad, precio = agregar_conceptos(filas)["cargo_fijo"]
    assert (cantidad, precio) == (0.0, 0.0)
    assert conceptos_con_cantidad_neta_cero(filas) == []  # no es una anomalía: no hay plata perdida


def test_cantidad_neta_cero_con_importe_neto_distinto_de_cero_preserva_la_plata():
    # Caso asimétrico: la nota de crédito no cancela exactamente el cargo
    # original -- quedan $2.000 de diferencia. Con el bug viejo esos
    # $2.000 desaparecían del total sin ningún aviso.
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-4, importe=-8000.0),
    ]
    cantidad, precio = agregar_conceptos(filas)["cargo_fijo"]
    assert cantidad == 1.0
    assert precio == pytest.approx(2000.0)
    # La identidad cantidad × precio == importe se mantiene:
    assert cantidad * precio == pytest.approx(2000.0)


def test_conceptos_con_cantidad_neta_cero_detecta_la_anomalia():
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-4, importe=-8000.0),
        FilaConcepto("abono_movil", "Abono normal", cantidad=4, importe=10000.0),
    ]
    anomalos = conceptos_con_cantidad_neta_cero(filas)
    assert anomalos == ["cargo_fijo"]


def test_conceptos_con_cantidad_neta_cero_vacio_si_no_hay_anomalias():
    filas = [FilaConcepto("abono_movil", "Abono", cantidad=4, importe=10000.0)]
    assert conceptos_con_cantidad_neta_cero(filas) == []


def test_no_mezcla_unidades_distintas_bajo_el_mismo_concepto():
    # docs/auditoria-2026-09.md, hallazgo A-16: antes, dos filas homologadas
    # al mismo concepto_normalizado pero con unidades distintas (kWh vs. GB)
    # se sumaban igual, produciendo una "cantidad total" y un "precio
    # unitario promedio" sin sentido (una unidad que no existe).
    filas = [
        FilaConcepto("consumo", "Consumo energía", cantidad=500, importe=50000.0, unidad="kWh"),
        FilaConcepto("consumo", "Consumo datos", cantidad=20, importe=4000.0, unidad="GB"),
    ]
    resultado = agregar_conceptos(filas)
    # Dos entradas separadas, una por unidad -- nunca se suman entre sí.
    assert resultado == {
        "consumo [kWh]": (500.0, 100.0),
        "consumo [GB]": (20.0, 200.0),
    }


def test_misma_unidad_si_se_agrega_normalmente():
    # Control: dos facturas del mismo concepto CON la misma unidad sí se
    # agregan juntas, como antes.
    filas = [
        FilaConcepto("consumo", "Consumo energía", cantidad=500, importe=50000.0, unidad="kWh"),
        FilaConcepto(
            "consumo", "Consumo energía adicional", cantidad=100, importe=11000.0, unidad="kWh"
        ),
    ]
    resultado = agregar_conceptos(filas)
    assert resultado == {"consumo [kWh]": (600.0, pytest.approx(61000.0 / 600.0))}


def test_unidad_none_no_lleva_sufijo_en_la_etiqueta():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=1, importe=3200.0, unidad=None)]
    resultado = agregar_conceptos(filas)
    assert "cargo_fijo" in resultado
    assert "cargo_fijo [" not in str(resultado.keys())
