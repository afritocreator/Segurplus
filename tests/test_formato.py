"""Test de core/formato.py::pesos_ars -- regla de oro (CLAUDE.md): valor
calculado a mano."""

from core.formato import pesos_ars


def test_formato_basico():
    assert pesos_ars(1234.56) == "$1.234,56"


def test_formato_negativo():
    assert pesos_ars(-1234.56) == "$-1.234,56"


def test_formato_con_signo_positivo():
    assert pesos_ars(1234.56, signo=True) == "$+1.234,56"


def test_formato_con_signo_negativo():
    assert pesos_ars(-1234.56, signo=True) == "$-1.234,56"


def test_formato_cero():
    assert pesos_ars(0.0) == "$0,00"


def test_negativo_que_redondea_a_cero_no_muestra_signo_menos():
    """docs/auditoria-2026-09-piloto.md, A-59: -0.004 redondea a $0,00 --
    antes de esta corrección, el signo se decidía ANTES de redondear, así
    que daba "$-0,00" (un menos delante de un cero, sin sentido)."""
    assert pesos_ars(-0.004) == "$0,00"
    assert pesos_ars(-0.0001) == "$0,00"


def test_positivo_que_redondea_a_cero_con_signo_no_muestra_menos_ni_de_mas():
    assert pesos_ars(-0.004, signo=True) == "$+0,00"
    assert pesos_ars(0.004, signo=True) == "$+0,00"


def test_miles_con_separador_argentino():
    assert pesos_ars(1000000.5) == "$1.000.000,50"
