"""Test del diccionario de homologación real de data/conceptos/general.yaml
(no hardcodeado -- CLAUDE.md) y de la combinación de varios YAML."""

from core.analisis.diccionario import cargar_diccionario


def test_carga_el_diccionario_real_del_repo():
    diccionario = cargar_diccionario()
    assert "abono_movil" in diccionario
    assert "abono linea movil" in diccionario["abono_movil"]


def test_combina_varios_yaml_de_la_carpeta(tmp_path):
    (tmp_path / "a.yaml").write_text("concepto_a:\n  - alias 1\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("concepto_b:\n  - alias 2\n", encoding="utf-8")
    diccionario = cargar_diccionario(directorio=tmp_path)
    assert diccionario == {"concepto_a": ["alias 1"], "concepto_b": ["alias 2"]}


def test_directorio_inexistente_devuelve_vacio(tmp_path):
    assert cargar_diccionario(directorio=tmp_path / "no_existe") == {}


def test_yaml_mal_formado_lanza_error_explicito(tmp_path):
    (tmp_path / "roto.yaml").write_text("- no es un mapeo\n- es una lista\n", encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="no tiene la forma esperada"):
        cargar_diccionario(directorio=tmp_path)
