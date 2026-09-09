"""Test de la regla de oro: valor calculado a mano para la similitud de
Dice, más los casos de uso reales de homologación de conceptos."""

import pytest

from core.analisis.homologacion import homologar_concepto, similitud


def test_similitud_identica_da_uno():
    assert similitud("Abono Línea Móvil", "Abono Línea Móvil") == pytest.approx(1.0)


def test_similitud_sin_nada_en_comun_da_cero():
    assert similitud("abc", "xyz") == 0.0


def test_similitud_calculada_a_mano():
    # normalizar("ab") -> bigramas {"ab"} (1 bigrama)
    # normalizar("ac") -> bigramas {"ac"} (1 bigrama)
    # intersección = 0 -> Dice = 0
    assert similitud("ab", "ac") == 0.0
    # "abc" -> bigramas {"ab", "bc"} (2)
    # "abd" -> bigramas {"ab", "bd"} (2)
    # intersección = {"ab"} = 1 -> Dice = 2*1 / (2+2) = 0.5
    assert similitud("abc", "abd") == pytest.approx(0.5)


DICCIONARIO = {
    "abono_movil": ["abono linea movil", "cargo fijo movil", "abono plan control"],
    "consumo_datos": ["consumo de datos", "internet movil"],
}


def test_homologa_variantes_del_mismo_proveedor():
    concepto, score = homologar_concepto("ABONO LINEA MOVIL", DICCIONARIO)
    assert concepto == "abono_movil"
    assert score > 0.45


def test_homologa_variantes_de_otro_proveedor_distinto_texto():
    concepto, score = homologar_concepto("Cargo fijo móvil", DICCIONARIO)
    assert concepto == "abono_movil"


def test_concepto_desconocido_no_se_fuerza():
    concepto, score = homologar_concepto("Recargo por reconexión de gas natural", DICCIONARIO)
    assert concepto is None
    assert score < 0.45
