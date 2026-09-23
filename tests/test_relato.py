"""Tests de core/relato.py -- Bloque 5 del plan de rediseño de septiembre
2026, corregido en docs/auditoria-2026-09-web.md (E-1, E-2, E-3). Regla de
oro de CLAUDE.md: cada caso lleva un valor calculado a mano, no solo "no
explota"."""

from unittest.mock import patch

from core.analisis.variacion import DescomposicionVariacion
from core.relato import DatosRelato, generar_relato_determinista


def _datos(**overrides) -> DatosRelato:
    # Por default: todo el total pagable es consumo (sin impuestos, recargos
    # ni créditos) -- así la frase de composición no aparece salvo que un
    # test la pida explícitamente.
    base = dict(
        servicio="energia",
        periodo_0="2026-07-01",
        periodo_1="2026-08-01",
        total_0=200.0,
        total_1=230.0,
        consumos_0=200.0,
        consumos_1=230.0,
        impuestos_0=0.0,
        impuestos_1=0.0,
        recargos_0=0.0,
        recargos_1=0.0,
        creditos_0=0.0,
        creditos_1=0.0,
        tipo_dominante="precio",
        proporcion_dominante=0.73,
        efecto_precio_total=30.0,
        efecto_cantidad_total=0.0,
        variacion_real_pct=0.05,
        inflacion_pct=0.10,
        concepto_destacado=DescomposicionVariacion(
            concepto="cargo_fijo",
            cantidad_0=1.0,
            precio_0=50.0,
            cantidad_1=1.0,
            precio_1=80.0,
            efecto_cantidad=0.0,
            efecto_precio=30.0,
            efecto_cruzado=0.0,
        ),
    )
    base.update(overrides)
    return DatosRelato(**base)


def test_caso_precio_dominante_calculado_a_mano():
    # variacion_pesos = 230 - 200 = 30; variacion_pct = 30 / 200 = 0.15 -> +15%
    # no hay impuestos/recargos/creditos -> sin frase de composición
    # efecto_precio_total = 30.0 >= 0 -> "subió"
    # variacion_real_pct 0.05 -> +5%; inflacion 0.10 -> +10%
    # concepto_destacado.variacion_total = (80-50)*1 = 30 -> "$30,00 más"
    texto = generar_relato_determinista(_datos())
    assert texto == (
        "En agosto de 2026 pagaste $230,00 de luz, $30,00 más que en julio de 2026 "
        "(+15%). Dentro de los consumos, el cambio fue mayormente por PRECIO (73% del "
        "movimiento): el precio unitario subió. Descontada la inflación del período "
        '(+10%), tu gasto real subió un 5%. El que más cambió fue "Cargo fijo": '
        "$30,00 más."
    )


def test_precio_que_baja_dice_bajo_no_subio():
    """docs/auditoria-2026-09-web.md, E-2: antes la frase de precio decía
    siempre "salió más caro", sin importar la dirección real."""
    texto = generar_relato_determinista(
        _datos(
            total_1=170.0,
            consumos_1=170.0,
            efecto_precio_total=-30.0,
            variacion_real_pct=-0.23,
        )
    )
    assert "el precio unitario bajó." in texto
    # E-2: sin signo duplicado -- "bajó un 23%", no "bajó un +23%"
    assert "tu gasto real bajó un 23%." in texto
    assert "+23" not in texto


def test_cantidad_dominante_con_direccion_real():
    texto = generar_relato_determinista(
        _datos(
            tipo_dominante="cantidad",
            proporcion_dominante=0.9,
            efecto_cantidad_total=-15.0,
        )
    )
    assert "CANTIDAD (90%" in texto
    assert "lo que consumiste disminuyó." in texto


def test_caso_mixto_no_menciona_un_porcentaje_de_un_solo_efecto():
    texto = generar_relato_determinista(_datos(tipo_dominante="mixto"))
    assert "mezcla de cantidad y precio" in texto


def test_suba_solo_de_impuestos_dice_que_los_consumos_no_cambiaron():
    """docs/auditoria-2026-09-web.md, E-3: el total pagable subió $250
    ($200 -> $450) pero los CONSUMOS no cambiaron (100 -> 100) -- el
    aumento entero es un impuesto nuevo. El relato tiene que decir que la
    causa es de impuestos, no atribuirla a precio/cantidad de consumo.

    no_consumo_0 = 0 + 0 - 0 = 0; no_consumo_1 = 250 + 0 - 0 = 250
    delta_no_consumo = 250; variacion_pesos = 450 - 200 = 250
    250 / 250 = 1.0 >= 0.15 -> sí se menciona la composición.
    delta_consumo = 100 - 100 = 0 -> "$0,00 son consumos"."""
    texto = generar_relato_determinista(
        _datos(
            total_0=200.0,
            total_1=450.0,
            consumos_0=100.0,
            consumos_1=100.0,
            impuestos_0=0.0,
            impuestos_1=250.0,
            tipo_dominante="sin_variacion",
            proporcion_dominante=0.0,
        )
    )
    assert (
        "De esa diferencia, $250,00 son impuestos, recargos y créditos, y $0,00 son "
        "consumos." in texto
    )
    assert "Los consumos no cambiaron: toda la diferencia es de impuestos" in texto
    # No se le atribuye el cambio a precio o cantidad de consumo.
    assert "PRECIO" not in texto
    assert "CANTIDAD" not in texto


def test_composicion_no_se_menciona_si_pesa_poco():
    """Un impuesto que representa menos del umbral (15%) del movimiento
    total no amerita la frase de composición -- sería ruido cuando casi
    todo el cambio ya es de consumo.

    total_0=200, total_1=230 -> variacion_pesos=30. impuestos suben 3 (de 0
    a 3): delta_no_consumo=3; 3/30 = 0.10 < 0.15 -> no se menciona."""
    texto = generar_relato_determinista(
        _datos(total_1=230.0, consumos_1=227.0, impuestos_0=0.0, impuestos_1=3.0)
    )
    assert "De esa diferencia" not in texto


def test_caso_sin_variacion_nominal():
    texto = generar_relato_determinista(
        _datos(
            total_1=200.0,
            consumos_1=200.0,
            tipo_dominante="sin_variacion",
            proporcion_dominante=0.0,
            efecto_precio_total=0.0,
            efecto_cantidad_total=0.0,
        )
    )
    assert "$0,00 más que" in texto or "$0,00 menos que" in texto
    assert "El gasto no cambió entre los dos meses." in texto


def test_gasto_disminuyo_usa_menos_no_mas():
    texto = generar_relato_determinista(_datos(total_1=170.0, consumos_1=170.0))
    # 170 - 200 = -30 -> "$30,00 menos que" y "-15%"
    assert "$30,00 menos que" in texto
    assert "-15%" in texto


def test_total_0_en_cero_no_calcula_porcentaje():
    """Sin período base, dividir por cero rompería -- el relato tiene que
    decirlo en vez de intentar calcular un porcentaje sin sentido."""
    texto = generar_relato_determinista(_datos(total_0=0.0))
    assert "No hay un julio de 2026 con gasto para comparar" in texto
    assert "%" not in texto


def test_sin_variacion_real_calculable_lo_dice_en_vez_de_inventar_un_numero():
    texto = generar_relato_determinista(_datos(variacion_real_pct=None))
    assert "No se pudo calcular cuánto de eso es inflación" in texto


def test_sin_inflacion_calculable_tampoco_muestra_variacion_real():
    """Si no se pudo descargar el IPC, tampoco tiene sentido mostrar una
    variación real (que se calcula a partir de esa misma inflación)."""
    texto = generar_relato_determinista(_datos(inflacion_pct=None))
    assert "No se pudo calcular cuánto de eso es inflación" in texto


def test_variacion_real_practicamente_nula_no_dice_subio_ni_bajo():
    texto = generar_relato_determinista(_datos(variacion_real_pct=0.001))
    assert "prácticamente el mismo" in texto


def test_sin_concepto_destacado_no_agrega_esa_frase():
    texto = generar_relato_determinista(_datos(concepto_destacado=None))
    assert "El que más cambió" not in texto


def test_concepto_destacado_con_variacion_cero_no_agrega_la_frase():
    """Un concepto "destacado" que en realidad no varió no aporta nada al
    relato -- sería raro decir "lo que más cambió fue X: $0,00 más"."""
    concepto_sin_cambio = DescomposicionVariacion(
        concepto="cargo_fijo",
        cantidad_0=1.0,
        precio_0=50.0,
        cantidad_1=1.0,
        precio_1=50.0,
        efecto_cantidad=0.0,
        efecto_precio=0.0,
        efecto_cruzado=0.0,
    )
    texto = generar_relato_determinista(_datos(concepto_destacado=concepto_sin_cambio))
    assert "El que más cambió" not in texto


def test_variacion_nominal_exactamente_cero_no_muestra_signo_de_mas():
    """`_porcentaje` con signo forzaba un "+0%" delante de un valor que
    redondea a cero -- sugiere una suba mínima en vez de "sin cambio"."""
    texto = generar_relato_determinista(
        _datos(
            total_1=200.0,
            consumos_1=200.0,
            tipo_dominante="sin_variacion",
            proporcion_dominante=0.0,
            efecto_precio_total=0.0,
            efecto_cantidad_total=0.0,
        )
    )
    assert "(0%)" in texto
    assert "(+0%)" not in texto


def test_compensacion_interna_sin_variacion_del_total_no_rompe():
    """Caso borde señalado por revisor-financiero: el total pagable no
    cambió (consumos +$100, impuestos -$100 -- se compensan exactamente),
    pero SÍ hubo movimiento interno de precio en los consumos.
    `variacion_pesos == 0` hace que la frase de composición no aparezca
    (no hay con qué dividir), así que el relato no distingue este caso de
    "nada cambió" -- documentado, no es un número falso: la frase de monto
    (sobre el total) y la de causa (sobre los consumos) son cada una
    correcta por separado."""
    texto = generar_relato_determinista(
        _datos(
            total_0=200.0,
            total_1=200.0,
            consumos_0=100.0,
            consumos_1=200.0,
            impuestos_0=100.0,
            impuestos_1=0.0,
            tipo_dominante="precio",
            proporcion_dominante=1.0,
            efecto_precio_total=100.0,
        )
    )
    assert "$0,00 más que" in texto
    assert "De esa diferencia" not in texto
    assert "el precio unitario subió" in texto


def test_generar_relato_nunca_llama_a_la_red():
    """docs/auditoria-2026-09-web.md, E-1: el relato ya no pasa por ningún
    modelo de lenguaje -- no debe haber ninguna llamada de red al armarlo."""
    with patch("requests.post") as mock_post:
        generar_relato_determinista(_datos())
    mock_post.assert_not_called()
