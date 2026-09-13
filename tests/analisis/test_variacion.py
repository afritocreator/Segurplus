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

from core.analisis.agregacion import FilaConcepto, agregar_conceptos
from core.analisis.variacion import (
    descomponer_conceptos,
    descomponer_variacion,
    efecto_dominante,
    top_conceptos_por_variacion,
)


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


# --- De punta a punta: el bug real de Movistar (período pegado a la ------
# --- descripción) tiene que imputarse a efecto_precio, no a efecto_cantidad


def test_de_punta_a_punta_concepto_sin_homologar_con_periodo_en_la_descripcion():
    # Caso real (Movistar): "Servicio de telefonía Agosto 2026" y al mes
    # siguiente "...Septiembre 2026", el mismo concepto, sin ningún alias en
    # el diccionario todavía. ANTES de este fix, agregar_conceptos usaba la
    # descripción CRUDA como clave -> dos claves distintas -> descomponer_
    # conceptos las veía como "un concepto que desaparece" + "un concepto
    # que aparece", con efecto_precio == 0 en las dos filas, cuando la causa
    # real es un aumento de PRECIO de $30.000 a $36.000 (+$6.000).
    filas_agosto = [
        FilaConcepto(None, "Servicio de telefonía Agosto 2026", cantidad=1, importe=30000.0)
    ]
    filas_septiembre = [
        FilaConcepto(None, "Servicio de telefonía Septiembre 2026", cantidad=1, importe=36000.0)
    ]
    agregado_0 = agregar_conceptos(filas_agosto)
    agregado_1 = agregar_conceptos(filas_septiembre)
    descomposiciones = descomponer_conceptos(agregado_0, agregado_1)

    assert len(descomposiciones) == 1  # UN solo concepto, no dos
    d = descomposiciones[0]
    assert d.efecto_precio == pytest.approx(6000.0)
    assert d.efecto_cantidad == pytest.approx(0.0)
    assert d.efecto_cruzado == pytest.approx(0.0)
    assert d.variacion_total == pytest.approx(6000.0)


def test_de_punta_a_punta_cantidad_de_lineas_cambia_en_la_descripcion():
    # "Abono 4 líneas móviles" / "Abono 5 líneas móviles": mismo concepto sin
    # homologar (agrupado por descripción sin período), la cantidad de
    # líneas que cambia entre meses queda en la propia `cantidad` de la
    # fila, no en el texto -- acá la fila representa el cargo total del
    # concepto, agrupado por su descripción estable.
    filas_0 = [FilaConcepto(None, "Abono 4 líneas móviles", cantidad=4, importe=10000.0)]
    filas_1 = [FilaConcepto(None, "Abono 5 líneas móviles", cantidad=5, importe=14000.0)]
    agregado_0 = agregar_conceptos(filas_0)
    agregado_1 = agregar_conceptos(filas_1)
    # Sin homologar, las descripciones (sin período) siguen siendo distintas
    # -- "4 líneas" y "5 líneas" no colapsan por diseño (ver quitar_periodo,
    # que NO saca números sueltos). Este caso lo resuelve el diccionario
    # (homologar_concepto), no la clave de agregación: se deja documentado
    # acá como el límite conocido de lo que agregacion.py puede arreglar
    # solo.
    assert set(agregado_0) != set(agregado_1)


# --- efecto_dominante: resumen de TODA la comparación (Bloque 8) ----------


def test_efecto_dominante_solo_precio_cambia():
    d = descomponer_variacion("x", cantidad_0=4, precio_0=2500, cantidad_1=4, precio_1=2800)
    tipo, proporcion = efecto_dominante([d], umbral=0.60)
    assert tipo == "precio"
    assert proporcion == pytest.approx(1.0)


def test_efecto_dominante_solo_cantidad_cambia():
    d = descomponer_variacion("x", cantidad_0=4, precio_0=2500, cantidad_1=6, precio_1=2500)
    tipo, proporcion = efecto_dominante([d], umbral=0.60)
    assert tipo == "cantidad"
    assert proporcion == pytest.approx(1.0)


def test_efecto_dominante_mixto_calculado_a_mano():
    # cantidad 10->11 ($100 c/u), precio 100->115.
    # efecto_cantidad = (11-10)*100 = 100
    # efecto_precio   = (115-100)*10 = 150
    # efecto_cruzado  = (11-10)*(115-100) = 15
    # total = 265 (= 11*115 - 10*100 = 1265-1000)
    # proporcion_cantidad = 100/265 = 0.377 ; proporcion_precio = 150/265 = 0.566
    # -- ninguno llega al umbral 0.60 -> mixto.
    d = descomponer_variacion("x", cantidad_0=10, precio_0=100, cantidad_1=11, precio_1=115)
    tipo, proporcion = efecto_dominante([d], umbral=0.60)
    assert tipo == "mixto"
    assert proporcion == pytest.approx(150.0 / 265.0)


def test_efecto_dominante_sin_variacion():
    d = descomponer_variacion("x", cantidad_0=10, precio_0=100, cantidad_1=10, precio_1=100)
    tipo, proporcion = efecto_dominante([d], umbral=0.60)
    assert tipo == "sin_variacion"
    assert proporcion == 0.0


def test_efecto_dominante_efectos_opuestos_es_compensado():
    # Precio +$1.000 y cantidad -$900: el neto no debe convertirse en 1000%.
    d = descomponer_variacion("x", cantidad_0=10, precio_0=100, cantidad_1=1, precio_1=200)
    tipo, proporcion = efecto_dominante([d], umbral=0.60)
    assert tipo == "compensado"
    assert proporcion == 0.0


def test_efecto_dominante_suma_varios_conceptos():
    # Dos conceptos, cada uno solo con efecto precio -> la suma también es
    # 100% precio.
    d1 = descomponer_variacion("a", cantidad_0=1, precio_0=1000, cantidad_1=1, precio_1=1200)
    d2 = descomponer_variacion("b", cantidad_0=1, precio_0=500, cantidad_1=1, precio_1=600)
    tipo, proporcion = efecto_dominante([d1, d2], umbral=0.60)
    assert tipo == "precio"
    assert proporcion == pytest.approx(1.0)


def test_efecto_dominante_umbral_por_defecto_se_lee_del_yaml_real():
    d = descomponer_variacion("x", cantidad_0=4, precio_0=2500, cantidad_1=4, precio_1=2800)
    tipo, _proporcion = efecto_dominante([d])  # sin pasar umbral -> data/alertas.yaml
    assert tipo == "precio"


# --- top_conceptos_por_variacion: acotar el gráfico (Bloque 8) ------------


def test_top_conceptos_por_variacion_ordena_por_valor_absoluto():
    chico = descomponer_variacion("chico", cantidad_0=1, precio_0=100, cantidad_1=1, precio_1=110)
    grande_baja = descomponer_variacion(
        "grande_baja", cantidad_0=1, precio_0=1000, cantidad_1=1, precio_1=200
    )
    mediano = descomponer_variacion(
        "mediano", cantidad_0=1, precio_0=500, cantidad_1=1, precio_1=700
    )
    top = top_conceptos_por_variacion([chico, grande_baja, mediano], 2)
    assert [d.concepto for d in top] == ["grande_baja", "mediano"]


def test_top_conceptos_por_variacion_n_mayor_a_la_lista_devuelve_todo():
    d = descomponer_variacion("x", cantidad_0=1, precio_0=100, cantidad_1=1, precio_1=110)
    assert len(top_conceptos_por_variacion([d], 10)) == 1
