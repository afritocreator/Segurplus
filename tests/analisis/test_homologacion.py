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
    # umbral explícito (0.45, el valor viejo) para no depender de
    # data/homologacion.yaml en este test puntual -- ver test de abajo
    # que sí ejercita el umbral real leído del archivo.
    concepto, score = homologar_concepto("ABONO LINEA MOVIL", DICCIONARIO, umbral=0.45)
    assert concepto == "abono_movil"
    assert score > 0.45


def test_homologa_variantes_de_otro_proveedor_distinto_texto():
    concepto, score = homologar_concepto("Cargo fijo móvil", DICCIONARIO, umbral=0.45)
    assert concepto == "abono_movil"


def test_concepto_desconocido_no_se_fuerza():
    concepto, score = homologar_concepto(
        "Recargo por reconexión de gas natural", DICCIONARIO, umbral=0.45
    )
    assert concepto is None
    assert score < 0.45


# --- A-3: umbral leído de data/homologacion.yaml, no hardcodeado -------


def test_umbral_por_defecto_se_lee_del_yaml_real():
    # Sin pasar `umbral`, usa data/homologacion.yaml (hoy 0.60, ver el
    # archivo para el porqué). Este es el caso EXACTO que motivó A-3:
    # "Recargo por reconexion" contra el diccionario real de telefonia
    # (comunes.yaml + telefonia.yaml) da score 0.571 -- con el umbral viejo
    # de Kleric- (0.45) se absorbía en silencio como "cargo_fijo"; con el
    # umbral actual (0.60) queda sin clasificar y dispara la alerta de
    # concepto nuevo, que es el comportamiento correcto.
    from core.analisis.diccionario import cargar_diccionario

    diccionario_real = cargar_diccionario("telefonia")
    concepto, score = homologar_concepto("Recargo por reconexion", diccionario_real)
    assert concepto is None
    assert score == pytest.approx(0.5714285714285714)
    assert 0.45 < score < 0.60  # justo el rango que 0.45 dejaba pasar y 0.60 no


def test_umbral_explicito_pisa_al_del_yaml():
    concepto, _score = homologar_concepto(
        "Cargo por gestion administrativa", DICCIONARIO, umbral=0.01
    )
    assert concepto is not None  # con un umbral irrisorio, homologa cualquier cosa
