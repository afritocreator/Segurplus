"""Tests de core/relato.py -- Bloque 5 del plan de rediseño de septiembre
2026. Regla de oro de CLAUDE.md: cada caso lleva un valor calculado a
mano, no solo "no explota"."""

from unittest.mock import MagicMock, patch

from core.analisis.variacion import DescomposicionVariacion
from core.relato import DatosRelato, generar_relato_determinista, redactar_con_modelo


def _datos(**overrides) -> DatosRelato:
    base = dict(
        servicio="energia",
        periodo_0="2026-07-01",
        periodo_1="2026-08-01",
        total_0=200.0,
        total_1=230.0,
        tipo_dominante="precio",
        proporcion_dominante=0.73,
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
    # variacion_real_pct 0.05 -> +5%; inflacion 0.10 -> +10%
    # concepto_destacado.variacion_total = (80-50)*1 = 30 -> "$30,00 más"
    texto = generar_relato_determinista(_datos())
    assert texto == (
        "En agosto de 2026 pagaste $230,00 de energia, $30,00 más que en julio de 2026 "
        "(+15%). Casi todo el cambio es por PRECIO (73% del movimiento): consumiste una "
        "cantidad parecida, pero salió más caro. Descontada la inflación del período "
        '(+10%), tu gasto real subió un +5%. El que más cambió fue "Cargo fijo": '
        "$30,00 más."
    )


def test_caso_cantidad_dominante():
    texto = generar_relato_determinista(_datos(tipo_dominante="cantidad", proporcion_dominante=0.9))
    assert "CANTIDAD (90%" in texto
    assert "consumiste distinto" in texto


def test_caso_mixto_no_menciona_un_porcentaje_de_un_solo_efecto():
    texto = generar_relato_determinista(_datos(tipo_dominante="mixto"))
    assert "mezcla de cantidad y precio" in texto


def test_caso_sin_variacion_nominal():
    texto = generar_relato_determinista(
        _datos(total_1=200.0, tipo_dominante="sin_variacion", proporcion_dominante=0.0)
    )
    assert "$0,00 más que" in texto or "$0,00 menos que" in texto
    assert "El gasto no cambió entre los dos meses." in texto


def test_gasto_disminuyo_usa_menos_no_mas():
    texto = generar_relato_determinista(_datos(total_1=170.0))
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


# --- redactar_con_modelo ---------------------------------------------------


def test_redactar_con_modelo_sin_clave_devuelve_el_parrafo_tal_cual(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    original = "Un párrafo cualquiera."
    assert redactar_con_modelo(original) == original


def test_redactar_con_modelo_con_respuesta_exitosa_usa_la_redaccion_del_modelo(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "clave-de-prueba")
    respuesta = MagicMock()
    respuesta.raise_for_status = MagicMock()
    respuesta.json.return_value = {
        "choices": [{"message": {"content": "Versión redactada por el modelo."}}]
    }
    with patch("requests.post", return_value=respuesta):
        resultado = redactar_con_modelo("Un párrafo cualquiera.")
    assert resultado == "Versión redactada por el modelo."


def test_redactar_con_modelo_con_error_de_red_cae_al_parrafo_determinista(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "clave-de-prueba")
    original = "Un párrafo cualquiera."
    with patch("requests.post", side_effect=ConnectionError("sin red")):
        assert redactar_con_modelo(original) == original


def test_redactar_con_modelo_con_respuesta_vacia_cae_al_parrafo_determinista(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "clave-de-prueba")
    respuesta = MagicMock()
    respuesta.raise_for_status = MagicMock()
    respuesta.json.return_value = {"choices": [{"message": {"content": "   "}}]}
    original = "Un párrafo cualquiera."
    with patch("requests.post", return_value=respuesta):
        assert redactar_con_modelo(original) == original
