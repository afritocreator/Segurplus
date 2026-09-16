"""Test de la regla de oro: valor calculado a mano para el promedio
ponderado de precio unitario al agregar dos facturas del mismo servicio."""

import pytest
import yaml

from core.analisis.agregacion import (
    RUTA_ETIQUETAS_CONCEPTO,
    FilaConcepto,
    agregar_conceptos,
    conceptos_con_cantidad_neta_cero,
    conceptos_con_cantidad_neta_negativa,
    etiqueta_legible,
)


def test_agrega_una_sola_factura_sin_cambios():
    filas = [FilaConcepto("abono_movil", "Abono 4 líneas", cantidad=4, importe=10000.0)]
    resultado = agregar_conceptos(filas)
    assert resultado == {"abono_movil": (4.0, 2500.0)}


def test_promedio_ponderado_calculado_a_mano():
    # Dos facturas del mismo concepto en el mismo período (poco común pero
    # posible: dos números de cliente del mismo servicio).
    # Factura 1: 4 líneas por $10.000 (precio unitario 2.500)
    # Factura 2: 2 líneas por $6.000 (precio unitario 3.000)
    # Promedio ponderado = (10000+6000) / (4+2) = 16000/6 = 2666.666...
    # (NO es el promedio simple de precios: (2500+3000)/2 = 2750, distinto)
    filas = [
        FilaConcepto("abono_movil", "Abono 4 líneas", cantidad=4, importe=10000.0),
        FilaConcepto("abono_movil", "Abono 2 líneas", cantidad=2, importe=6000.0),
    ]
    resultado = agregar_conceptos(filas)
    cantidad, precio = resultado["abono_movil"]
    assert cantidad == pytest.approx(6.0)
    assert precio == pytest.approx(16000.0 / 6.0)


def test_conceptos_sin_homologar_no_se_pierden():
    filas = [FilaConcepto(None, "Cargo raro nunca visto", cantidad=1, importe=500.0)]
    resultado = agregar_conceptos(filas)
    assert len(resultado) == 1
    clave = next(iter(resultado))
    # La clave usa el texto NORMALIZADO (minúsculas, sin tildes), no la
    # descripción cruda -- ver docs/auditoria-2026-09.md: la clave es
    # también la identidad entre períodos en descomponer_conceptos, y una
    # descripción cruda cambia de un mes a otro (ej. el período pegado al
    # final) aunque sea el mismo concepto. Reexpresado a propósito: este
    # test antes codificaba el bug (fallback a descripción cruda).
    assert clave == "(sin_homologar) cargo raro nunca visto"


def test_conceptos_sin_homologar_estables_entre_periodos_con_periodo_en_la_descripcion():
    # El caso real que motivó el cambio (Movistar): el mismo concepto sin
    # homologar, en dos períodos distintos, con el mes pegado a la
    # descripción -- tiene que dar la MISMA clave en los dos.
    filas_agosto = [
        FilaConcepto(None, "Servicio de telefonía Agosto 2026", cantidad=1, importe=30000.0)
    ]
    filas_septiembre = [
        FilaConcepto(None, "Servicio de telefonía Septiembre 2026", cantidad=1, importe=36000.0)
    ]
    clave_agosto = next(iter(agregar_conceptos(filas_agosto)))
    clave_septiembre = next(iter(agregar_conceptos(filas_septiembre)))
    assert clave_agosto == clave_septiembre == "(sin_homologar) servicio de telefonia"


def test_conceptos_sin_homologar_estables_entre_periodos_con_detalle_numerico():
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo B-2, caso real (Usina
    Popular y Municipal de Tandil): el mismo concepto sin homologar, con
    el detalle de cálculo pegado a la descripción y DISTINTO cada mes --
    tiene que dar la MISMA clave en los dos, igual que el caso del período
    (arriba). Antes de `quitar_detalle_numerico`, cada mes generaba una
    clave distinta y la descomposición precio/cantidad veía "un concepto
    que desaparece" + "uno que aparece" en vez de una sola serie."""
    filas_julio = [
        FilaConcepto(None, "Cargo Fijo (414,4500 / 30.5 x 8)", cantidad=1, importe=108.71)
    ]
    filas_agosto = [
        FilaConcepto(None, "Cargo Fijo (455,8900 / 30.5 x 21)", cantidad=1, importe=313.89)
    ]
    clave_julio = next(iter(agregar_conceptos(filas_julio)))
    clave_agosto = next(iter(agregar_conceptos(filas_agosto)))
    assert clave_julio == clave_agosto == "(sin_homologar) cargo fijo"


def test_cantidad_y_importe_cero_da_cero():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=0, importe=0.0)]
    resultado = agregar_conceptos(filas)
    assert resultado["cargo_fijo"] == (0.0, 0.0)


def test_cantidad_neta_cero_con_importe_neto_tambien_cero_no_es_el_caso_anomalo():
    # Caso simétrico: una nota de crédito que cancela exactamente el cargo
    # original -- cantidad Y el importe neto dan cero. Acá SÍ corresponde
    # (0.0, 0.0): no hay plata que preservar porque el importe neto
    # realmente es cero (la nota de crédito canceló el cargo por completo).
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-4, importe=-10000.0),
    ]
    cantidad, precio = agregar_conceptos(filas)["cargo_fijo"]
    assert (cantidad, precio) == (0.0, 0.0)
    assert conceptos_con_cantidad_neta_cero(filas) == []  # no es una anomalía: no hay plata perdida


def test_cantidad_neta_cero_con_importe_neto_distinto_de_cero_preserva_la_plata():
    # Caso asimétrico: la nota de crédito no cancela exactamente el cargo
    # original -- quedan $2.000 de diferencia. Con el bug viejo esos
    # $2.000 desaparecían del total sin ningún aviso.
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-4, importe=-8000.0),
    ]
    cantidad, precio = agregar_conceptos(filas)["cargo_fijo"]
    assert cantidad == 1.0
    assert precio == pytest.approx(2000.0)
    # La identidad cantidad × precio == importe se mantiene:
    assert cantidad * precio == pytest.approx(2000.0)


def test_conceptos_con_cantidad_neta_cero_detecta_la_anomalia():
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-4, importe=-8000.0),
        FilaConcepto("abono_movil", "Abono normal", cantidad=4, importe=10000.0),
    ]
    anomalos = conceptos_con_cantidad_neta_cero(filas)
    assert anomalos == ["cargo_fijo"]


def test_conceptos_con_cantidad_neta_cero_vacio_si_no_hay_anomalias():
    filas = [FilaConcepto("abono_movil", "Abono", cantidad=4, importe=10000.0)]
    assert conceptos_con_cantidad_neta_cero(filas) == []


def test_no_mezcla_unidades_distintas_bajo_el_mismo_concepto():
    # docs/auditoria-2026-09.md, hallazgo A-16: antes, dos filas homologadas
    # al mismo concepto_normalizado pero con unidades distintas (kWh vs. GB)
    # se sumaban igual, produciendo una "cantidad total" y un "precio
    # unitario promedio" sin sentido (una unidad que no existe).
    filas = [
        FilaConcepto("consumo", "Consumo energía", cantidad=500, importe=50000.0, unidad="kWh"),
        FilaConcepto("consumo", "Consumo datos", cantidad=20, importe=4000.0, unidad="GB"),
    ]
    resultado = agregar_conceptos(filas)
    # Dos entradas separadas, una por unidad -- nunca se suman entre sí. La
    # unidad se normaliza a minúscula (A-25, ver test dedicado más abajo).
    assert resultado == {
        "consumo [kwh]": (500.0, 100.0),
        "consumo [gb]": (20.0, 200.0),
    }


def test_misma_unidad_si_se_agrega_normalmente():
    # Control: dos facturas del mismo concepto CON la misma unidad sí se
    # agregan juntas, como antes.
    filas = [
        FilaConcepto("consumo", "Consumo energía", cantidad=500, importe=50000.0, unidad="kWh"),
        FilaConcepto(
            "consumo", "Consumo energía adicional", cantidad=100, importe=11000.0, unidad="kWh"
        ),
    ]
    resultado = agregar_conceptos(filas)
    assert resultado == {"consumo [kwh]": (600.0, pytest.approx(61000.0 / 600.0))}


def test_unidad_none_no_lleva_sufijo_en_la_etiqueta():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=1, importe=3200.0, unidad=None)]
    resultado = agregar_conceptos(filas)
    assert "cargo_fijo" in resultado
    assert "cargo_fijo [" not in str(resultado.keys())


# --- A-25: la unidad se normaliza antes de agrupar ------------------------


def test_unidad_con_mayusculas_y_espacios_agrupa_igual_que_normalizada():
    # docs/auditoria-2026-09.md, hallazgo A-25: "kWh", "KWH" y " kWh " no
    # pueden armar tres claves de agrupamiento distintas para la misma
    # unidad -- entre dos períodos, eso se ve exactamente igual que el bug
    # del período pegado a la descripción (concepto nuevo/desaparecido en
    # vez de una sola serie).
    filas = [
        FilaConcepto("consumo", "Consumo energía", cantidad=500, importe=50000.0, unidad="kWh"),
        FilaConcepto("consumo", "Consumo energía", cantidad=100, importe=11000.0, unidad="KWH"),
        FilaConcepto("consumo", "Consumo energía", cantidad=50, importe=5000.0, unidad=" kWh "),
    ]
    resultado = agregar_conceptos(filas)
    assert len(resultado) == 1
    cantidad, precio = resultado["consumo [kwh]"]
    assert cantidad == pytest.approx(650.0)
    assert precio == pytest.approx((50000.0 + 11000.0 + 5000.0) / 650.0)


def test_unidad_solo_espacios_es_sin_unidad():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=1, importe=3200.0, unidad="   ")]
    resultado = agregar_conceptos(filas)
    assert "cargo_fijo" in resultado
    assert "cargo_fijo [" not in str(resultado.keys())


# --- A-24: cantidad neta negativa ------------------------------------------


def test_cantidad_neta_negativa_se_detecta():
    # Nota de crédito MAYOR que el cargo original: +4/$10.000 y -6/-$15.000
    # -> cantidad neta -2, importe neto -$5.000. La identidad cierra
    # (-2 * 2500 == -5000) pero es un resultado raro de mostrar.
    filas = [
        FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=4, importe=10000.0),
        FilaConcepto("cargo_fijo", "Nota de crédito", cantidad=-6, importe=-15000.0),
    ]
    cantidad, precio = agregar_conceptos(filas)["cargo_fijo"]
    assert cantidad == pytest.approx(-2.0)
    assert precio == pytest.approx(2500.0)
    assert cantidad * precio == pytest.approx(-5000.0)
    assert conceptos_con_cantidad_neta_negativa(filas) == ["cargo_fijo"]
    assert conceptos_con_cantidad_neta_cero(filas) == []  # no es el mismo caso que A-20


def test_cantidad_neta_negativa_vacio_si_no_hay_anomalias():
    filas = [FilaConcepto("abono_movil", "Abono", cantidad=4, importe=10000.0)]
    assert conceptos_con_cantidad_neta_negativa(filas) == []


# --- Vocabulario claro: etiqueta_legible traduce conceptos homologados -----


def test_etiqueta_legible_traduce_concepto_homologado():
    assert etiqueta_legible("abono_movil") == "Abono móvil"


def test_etiqueta_legible_sigue_capitalizando_lo_sin_homologar():
    assert etiqueta_legible("(sin_homologar) cargo raro") == "(sin_homologar) Cargo raro"


def test_etiqueta_legible_devuelve_el_slug_si_no_esta_mapeado():
    assert etiqueta_legible("concepto_que_no_existe_todavia") == "concepto_que_no_existe_todavia"


def test_etiqueta_legible_no_rompe_si_el_yaml_no_existe(monkeypatch, tmp_path):
    import core.analisis.agregacion as agregacion_mod

    monkeypatch.setattr(agregacion_mod, "RUTA_ETIQUETAS_CONCEPTO", tmp_path / "no_existe.yaml")
    assert etiqueta_legible("abono_movil") == "abono_movil"


def test_etiquetas_concepto_cubre_todos_los_slugs_reales():
    """Tripwire: si se agrega un concepto_normalizado nuevo en
    data/conceptos/*.yaml y se olvida su etiqueta acá, este test avisa
    (en vez de que el slug crudo se cuele silenciosamente al tablero)."""
    from core.analisis.diccionario import cargar_diccionario

    slugs_reales = set(cargar_diccionario())
    etiquetas = yaml.safe_load(RUTA_ETIQUETAS_CONCEPTO.read_text(encoding="utf-8"))
    faltantes = slugs_reales - set(etiquetas)
    assert not faltantes, f"Faltan etiquetas para: {faltantes}"
