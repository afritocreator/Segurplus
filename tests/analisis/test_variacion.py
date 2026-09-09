"""Test de la regla de oro (CLAUDE.md): valor calculado a mano.

Caso 1 -- el ejemplo de Movistar del pedido original: abono móvil pasa de
4 líneas a $2.500 c/u (total $10.000) a 6 líneas a $2.800 c/u (total $16.800).

A mano:
  efecto_cantidad = (6 - 4) * 2500        = 5.000
  efecto_precio   = (2800 - 2500) * 4     = 1.200
  efecto_cruzado  = (6 - 4) * (2800-2500) =   600
  suma                                    = 6.800
  variación total = 16.800 - 10.000       = 6.800   (coincide exacto)
"""

import pytest

from core.analisis.variacion import descomponer_conceptos, descomponer_variacion


def test_descomposicion_ejemplo_movistar_calculado_a_mano():
    d = descomponer_variacion(
        "abono_movil", cantidad_0=4, precio_0=2500, cantidad_1=6, precio_1=2800
    )
    assert d.efecto_cantidad == pytest.approx(5000.0)
    assert d.efecto_precio == pytest.approx(1200.0)
    assert d.efecto_cruzado == pytest.approx(600.0)
    assert d.total_0 == pytest.approx(10000.0)
    assert d.total_1 == pytest.approx(16800.0)
    assert d.variacion_total == pytest.approx(6800.0)


def test_identidad_precio_cantidad_cierra_exacto():
    """La identidad algebraica tiene que cerrar SIEMPRE, no solo en el
    ejemplo de arriba -- si no cierra hay un error de signo (CLAUDE.md:
    "el peor error posible")."""
    casos = [
        (4, 2500, 6, 2800),
        (10, 100, 10, 100),  # sin variación
        (10, 100, 5, 100),  # solo baja cantidad
        (10, 100, 10, 150),  # solo sube precio
        (10, 100, 0, 100),  # desaparece el concepto
        (0, 100, 10, 100),  # aparece el concepto
    ]
    for c0, p0, c1, p1 in casos:
        d = descomponer_variacion("x", cantidad_0=c0, precio_0=p0, cantidad_1=c1, precio_1=p1)
        suma = d.efecto_cantidad + d.efecto_precio + d.efecto_cruzado
        assert suma == pytest.approx(d.variacion_total, abs=1e-9), (c0, p0, c1, p1)


def test_solo_cantidad_cambia_efecto_precio_es_cero():
    d = descomponer_variacion("x", cantidad_0=10, precio_0=100, cantidad_1=15, precio_1=100)
    assert d.efecto_precio == pytest.approx(0.0)
    assert d.efecto_cantidad == pytest.approx(500.0)  # (15-10)*100


def test_solo_precio_cambia_efecto_cantidad_es_cero():
    d = descomponer_variacion("x", cantidad_0=10, precio_0=100, cantidad_1=10, precio_1=120)
    assert d.efecto_cantidad == pytest.approx(0.0)
    assert d.efecto_precio == pytest.approx(200.0)  # (120-100)*10


def test_cargo_fijo_sin_cantidad_toda_la_variacion_es_precio():
    # q=1 constante (un alquiler, un cargo fijo)
    d = descomponer_variacion(
        "alquiler", cantidad_0=1, precio_0=50000, cantidad_1=1, precio_1=55000
    )
    assert d.efecto_cantidad == pytest.approx(0.0)
    assert d.efecto_cruzado == pytest.approx(0.0)
    assert d.efecto_precio == pytest.approx(5000.0)
    assert d.variacion_total == pytest.approx(5000.0)


def test_variacion_pct_calculada_a_mano():
    d = descomponer_variacion("x", cantidad_0=4, precio_0=2500, cantidad_1=6, precio_1=2800)
    # 6800 / 10000 = 0.68 (68%)
    assert d.variacion_pct == pytest.approx(0.68)


def test_descomponer_conceptos_concepto_nuevo_cae_en_efecto_cantidad():
    d0 = {"abono_movil": (4, 2500)}
    d1 = {"abono_movil": (4, 2500), "consumo_datos": (10, 50)}  # aparece en período 1
    resultados = {r.concepto: r for r in descomponer_conceptos(d0, d1)}
    nuevo = resultados["consumo_datos"]
    assert nuevo.efecto_precio == pytest.approx(0.0)
    assert nuevo.variacion_total == pytest.approx(500.0)  # 10*50 - 0


def test_descomponer_conceptos_concepto_que_desaparece():
    d0 = {"abono_movil": (4, 2500), "roaming": (5, 100)}
    d1 = {"abono_movil": (4, 2500)}
    resultados = {r.concepto: r for r in descomponer_conceptos(d0, d1)}
    desaparecido = resultados["roaming"]
    assert desaparecido.total_1 == 0.0
    assert desaparecido.variacion_total == pytest.approx(-500.0)  # 0 - 5*100
