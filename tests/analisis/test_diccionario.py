"""Test del diccionario de homologación real de data/conceptos/*.yaml
(no hardcodeado -- CLAUDE.md), su combinación cuando no se pasa servicio, y
el acotado por servicio (ver docs/auditoria-2026-09.md, hallazgo A-3)."""

import pytest

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

    with pytest.raises(ValueError, match="no tiene la forma esperada"):
        cargar_diccionario(directorio=tmp_path)


# --- A-3: acotado por servicio -----------------------------------------


def test_servicio_solo_trae_comunes_mas_su_propio_archivo(tmp_path):
    (tmp_path / "comunes.yaml").write_text("cargo_fijo:\n  - cargo fijo\n", encoding="utf-8")
    (tmp_path / "telefonia.yaml").write_text("abono_movil:\n  - abono\n", encoding="utf-8")
    (tmp_path / "gas.yaml").write_text("consumo_gas:\n  - consumo de gas\n", encoding="utf-8")

    diccionario = cargar_diccionario("telefonia", directorio=tmp_path)
    assert set(diccionario) == {"cargo_fijo", "abono_movil"}  # NO consumo_gas


def test_servicio_sin_yaml_propio_solo_trae_comunes(tmp_path):
    (tmp_path / "comunes.yaml").write_text("cargo_fijo:\n  - cargo fijo\n", encoding="utf-8")
    (tmp_path / "telefonia.yaml").write_text("abono_movil:\n  - abono\n", encoding="utf-8")

    # "seguro" no tiene YAML propio todavía -- solo trae comunes.yaml.
    diccionario = cargar_diccionario("seguro", directorio=tmp_path)
    assert diccionario == {"cargo_fijo": ["cargo fijo"]}


def test_servicio_none_sigue_combinando_todo(tmp_path):
    (tmp_path / "comunes.yaml").write_text("cargo_fijo:\n  - cargo fijo\n", encoding="utf-8")
    (tmp_path / "telefonia.yaml").write_text("abono_movil:\n  - abono\n", encoding="utf-8")
    (tmp_path / "gas.yaml").write_text("consumo_gas:\n  - consumo de gas\n", encoding="utf-8")

    diccionario = cargar_diccionario(directorio=tmp_path)  # sin servicio
    assert set(diccionario) == {"cargo_fijo", "abono_movil", "consumo_gas"}


def test_agua_y_gas_no_compiten_entre_si_en_el_repo_real():
    # docs/auditoria-2026-09.md, hallazgo A-3: antes, "Consumo de gas
    # natural" contra el diccionario COMPLETO (todos los servicios
    # combinados) podía homologar por error a consumo_agua (bigramas
    # parecidos: "consumo de"). Acotado por servicio, ya no compiten.
    from core.analisis.homologacion import homologar_concepto

    d_agua = cargar_diccionario("agua")
    concepto, _score = homologar_concepto("Consumo de gas natural m3", d_agua)
    assert concepto is None
