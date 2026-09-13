"""Test de la regla de oro: valor calculado a mano para la similitud de
Dice, más los casos de uso reales de homologación de conceptos."""

import pytest

from core.analisis.homologacion import homologar_concepto, normalizar, quitar_periodo, similitud


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


# --- quitar_periodo: el período pegado a la descripción (caso real Movistar) ---


def test_normalizar_no_cambio_su_contrato():
    # Candado: normalizar() NO debe tocarse para sacar el período -- eso es
    # trabajo de quitar_periodo(), que corre después. Si este test se rompe,
    # alguien mezcló las dos responsabilidades.
    assert normalizar("Servicio de telefonía Agosto 2026") == "servicio de telefonia agosto 2026"


def test_quitar_periodo_saca_mes_y_anio():
    assert quitar_periodo("Servicio de telefonía Agosto 2026") == "servicio de telefonia"
    assert quitar_periodo("Servicio de telefonía Septiembre 2026") == "servicio de telefonia"


def test_quitar_periodo_formato_barra():
    assert quitar_periodo("Consumo periodo 08/2026") == "consumo periodo"
    assert quitar_periodo("Consumo periodo 09/2026") == "consumo periodo"


def test_quitar_periodo_abreviatura_con_guion():
    assert quitar_periodo("Abono ago-2026") == "abono"
    assert quitar_periodo("Abono sep-2026") == "abono"


def test_quitar_periodo_cubre_todas_las_abreviaturas_de_factura():
    for mes in (
        "ene",
        "feb",
        "mar",
        "abr",
        "may",
        "jun",
        "jul",
        "ago",
        "sep",
        "sept",
        "set",
        "oct",
        "nov",
        "dic",
    ):
        assert quitar_periodo(f"Cargo {mes} 2026") == "cargo"


def test_homologacion_empate_no_depende_del_orden_del_yaml():
    resultado = homologar_concepto("abono", {"zeta": ["abono"], "alfa": ["abono"]}, umbral=0.6)
    assert resultado.concepto is None
    assert resultado.candidatos_empatados == ("alfa", "zeta")


def test_quitar_periodo_no_fusiona_planes_con_numero_pegado():
    # "Plan 5GB" y "Plan 20GB" son conceptos DISTINTOS -- el dígito pegado a
    # la letra (sin espacio) no matchea el patrón de período.
    assert quitar_periodo("Plan 5GB") == "plan 5gb"
    assert quitar_periodo("Plan 20GB") == "plan 20gb"
    assert quitar_periodo("Plan 5GB") != quitar_periodo("Plan 20GB")


def test_quitar_periodo_no_fusiona_lineas_ni_medidores_numerados():
    # A diferencia de una versión "agresiva" (sacar todo dígito suelto), acá
    # "Línea 1" y "Línea 2" tienen que seguir siendo distintos -- una empresa
    # con varias líneas facturadas por separado necesita esa granularidad.
    assert quitar_periodo("Línea 1") != quitar_periodo("Línea 2")
    assert quitar_periodo("Medidor 1 planta") != quitar_periodo("Medidor 2 depósito")


def test_quitar_periodo_conserva_numero_pegado_a_letra():
    assert quitar_periodo("Consumo m3 gas") == "consumo m3 gas"


def test_quitar_periodo_descripcion_solo_periodo_no_rompe():
    # Guarda: si sacar el período dejaría el string vacío, se devuelve el
    # texto normalizado sin tocar -- nunca una clave vacía "(sin_homologar) ".
    assert quitar_periodo("Agosto 2026") == "agosto 2026"


def test_quitar_periodo_par_real_da_similitud_uno():
    a = quitar_periodo("Servicio de telefonía Agosto 2026")
    b = quitar_periodo("Servicio de telefonía Septiembre 2026")
    assert similitud(a, b) == pytest.approx(1.0)


def test_homologar_concepto_ignora_el_periodo_de_la_descripcion():
    # Con el diccionario real de telefonía, agosto y septiembre dan el MISMO
    # score -- eso es lo que garantiza una clave de agrupamiento estable en
    # agregacion.py, independientemente de si el diccionario ya tiene o no
    # un alias para este concepto (docs/auditoria-2026-09.md, A-28: desde
    # que se agregó el alias "servicio de telefonia" -- Bloque 4 -- ambos
    # homologan a servicio_telefonia con score 1.0; antes de ese alias,
    # ambos daban 0.588, sin homologar, pero YA consistentes entre sí).
    from core.analisis.diccionario import cargar_diccionario

    diccionario_real = cargar_diccionario("telefonia")
    c0, score_0 = homologar_concepto("Servicio de telefonía Agosto 2026", diccionario_real)
    c1, score_1 = homologar_concepto("Servicio de telefonía Septiembre 2026", diccionario_real)
    assert c0 == c1 == "servicio_telefonia"
    assert score_0 == pytest.approx(score_1) == pytest.approx(1.0)
