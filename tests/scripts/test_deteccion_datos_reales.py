"""Tests de la lógica compartida de protección de data/reales/ (usada tanto
por el hook de Claude Code como por el pre-commit real de git)."""

from scripts.deteccion_datos_reales import (
    contiene_cuit_valido,
    digito_verificador_cuit,
    es_comando_git_de_solo_lectura,
    referencia_carpeta_reales,
)


def test_digito_verificador_cuit_calculado_a_mano():
    # CUIT 30-71234567-8, primeros 10 dígitos 3071234567.
    # pesos: 5,4,3,2,7,6,5,4,3,2 sobre 3,0,7,1,2,3,4,5,6,7:
    # 3*5+0*4+7*3+1*2+2*7+3*6+4*5+5*4+6*3+7*2
    # = 15+0+21+2+14+18+20+20+18+14 = 142
    # 142 % 11 = 10 -> dv = 11-10 = 1
    assert digito_verificador_cuit("3071234567") == 1


def test_referencia_carpeta_reales_detecta_git_add():
    assert referencia_carpeta_reales("git add data/reales/factura.pdf")


def test_referencia_carpeta_reales_no_dispara_en_mensaje_de_commit():
    assert not referencia_carpeta_reales('git commit -m "arreglo el guard de data/reales/"')


def test_referencia_carpeta_reales_permite_readme():
    assert not referencia_carpeta_reales("git add data/reales/README.md")


def test_es_comando_git_de_solo_lectura():
    assert es_comando_git_de_solo_lectura("git status")
    assert es_comando_git_de_solo_lectura("cd x && git diff")
    assert not es_comando_git_de_solo_lectura("git commit -m x")


def test_contiene_cuit_valido_rechaza_numero_de_11_digitos_al_azar():
    # 11 dígitos que no pasan el dígito verificador -> no es CUIT
    assert contiene_cuit_valido("el monto fue 12345678901 pesos") is None
