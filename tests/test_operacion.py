"""Test de core/operacion.py contra el data/operacion.yaml real del repo."""

from core.operacion import revision_humana_obligatoria


def test_revision_humana_obligatoria_lee_el_yaml_real():
    # data/operacion.yaml del repo trae el default del piloto: false.
    assert revision_humana_obligatoria() is False
