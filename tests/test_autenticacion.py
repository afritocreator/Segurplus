"""Tests de la comparación de contraseña (core.autenticacion)."""

from core.autenticacion import verificar_contrasena


def test_contrasena_correcta_pasa():
    assert verificar_contrasena("miclave123", "miclave123")


def test_contrasena_incorrecta_no_pasa():
    assert not verificar_contrasena("otraclave", "miclave123")


def test_contrasena_vacia_no_pasa_si_la_correcta_no_lo_es():
    assert not verificar_contrasena("", "miclave123")


def test_es_sensible_a_mayusculas():
    assert not verificar_contrasena("MiClave123", "miclave123")
