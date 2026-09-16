"""Tests del almacenamiento DuckDB contra un archivo temporal (nunca
data/reales/facturas.duckdb real -- ver CLAUDE.md)."""

from dataclasses import replace
from pathlib import Path

import pytest

from core.almacenamiento import (
    actualizar_caso_alerta,
    alertas_del_periodo,
    aprobar_pendientes,
    borrar_de_cuarentena,
    conceptos_sin_clasificar,
    conectar,
    contar_borradores,
    decision_factura,
    descartar_borrador,
    factura_ya_procesada,
    guardar_alertas,
    guardar_en_cuarentena,
    guardar_factura,
    intentos_gemini_fallidos_recientes,
    leer_borrador,
    listar_borradores,
    listar_casos_alerta,
    listar_facturas_aprobadas,
    listar_facturas_pendientes,
    llamadas_ultima_hora,
    metricas_por_proveedor,
    motivos_cuarentena_por_proveedor,
    proxima_ventana_libre,
    recargos_del_periodo,
    registrar_correccion,
    registrar_intento_gemini,
    resumen_financiero_factura,
    sincronizar_casos_alertas,
)
from core.extraccion.esquema import Concepto, FacturaExtraida, Recargo
from core.extraccion.validacion import validar_factura


def _factura(hash_pdf="abc123") -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Movistar",
        cuit="30-12345678-1",
        servicio="telefonia",
        periodo_desde="2026-08-01",
        periodo_hasta="2026-08-31",
        fecha_emision="2026-08-05",
        fecha_vencimiento="2026-08-20",
        numero_comprobante="0001-1",
        moneda="ARS",
        conceptos=[Concepto("Abono", 4, "línea", 2500.0, 10000.0)],
        subtotal=10000.0,
        total=12100.0,
        hash_pdf=hash_pdf,
        ruta_pdf="/tmp/x.pdf",
    )


def test_guardar_y_leer_factura(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    assert not factura_ya_procesada(con, factura.hash_pdf)

    guardar_factura(con, factura, conceptos_normalizados={0: "abono_movil"})
    assert factura_ya_procesada(con, factura.hash_pdf)

    fila = con.execute(
        "SELECT emisor, total FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()
    assert fila == ("Movistar", 12100.0)

    concepto = con.execute(
        "SELECT concepto_normalizado, importe FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()
    assert concepto == ("abono_movil", 10000.0)
    con.close()


# --- borrador: plan de confirmación de carga -------------------------------
# El pipeline deja SIEMPRE un borrador (docs/auditoria-2026-09-facturas-
# reales-2.md), sea cual sea su calidad -- estas tres funciones son la cola
# que lee/gestiona apps/segurplus/paginas/confirmar.py.


def test_listar_borradores_solo_trae_estado_borrador(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(hash_pdf="b1"), estado="borrador")
    guardar_factura(con, _factura(hash_pdf="a1"), estado="aprobada")

    borradores = listar_borradores(con)

    assert [b[0] for b in borradores] == ["b1"]
    con.close()


def test_listar_borradores_trae_motivo_carga(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(
        con, _factura(hash_pdf="b1"), estado="borrador", motivo_carga="No se pudo leer con Gemini"
    )

    borradores = listar_borradores(con)

    assert borradores[0][-1] == "No se pudo leer con Gemini"
    con.close()


def test_leer_borrador_trae_cabecera_y_lineas(tmp_path):
    from core.extraccion.esquema import Impuesto

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="b1")
    factura.impuestos = [Impuesto("IVA 21%", importe=2100.0)]
    guardar_factura(con, factura, estado="borrador", texto_extraido="TOTAL A PAGAR $ 12.100,00")

    datos = leer_borrador(con, "b1")

    assert datos["emisor"] == "Movistar"
    assert datos["texto_extraido"] == "TOTAL A PAGAR $ 12.100,00"
    assert datos["conceptos"] == [("Abono", 4.0, "línea", 2500.0, 10000.0)]
    assert datos["impuestos"] == [("IVA 21%", 2100.0)]
    assert datos["recargos"] == []
    assert datos["creditos"] == []
    con.close()


def test_leer_borrador_de_algo_que_no_es_borrador_rechaza(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(hash_pdf="a1"), estado="aprobada")

    with pytest.raises(ValueError, match="no es un borrador"):
        leer_borrador(con, "a1")
    con.close()


def test_leer_borrador_inexistente_rechaza(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    with pytest.raises(ValueError, match="no es un borrador"):
        leer_borrador(con, "no-existe")
    con.close()


def test_descartar_borrador_libera_el_hash(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="b1")
    guardar_factura(con, factura, estado="borrador")
    assert factura_ya_procesada(con, "b1")

    descartar_borrador(con, "b1")

    assert not factura_ya_procesada(con, "b1")
    assert con.execute("SELECT count(*) FROM conceptos WHERE hash_pdf = 'b1'").fetchone()[0] == 0
    con.close()


def test_descartar_borrador_borra_el_pdf_de_evidencia(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-confirmacion.md, D-11: antes, descartar un
    borrador borraba las filas de la base pero dejaba el PDF huérfano en
    el disco -- sin ninguna fila que lo referenciara."""
    from core.evidencia import guardar_pdf

    evidencia_dir = tmp_path / "evidencia"
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setenv("EVIDENCIA_DIR", str(evidencia_dir))

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="b1")
    factura.ruta_evidencia = guardar_pdf("b1", b"contenido del pdf")
    assert Path(factura.ruta_evidencia).exists()
    guardar_factura(con, factura, estado="borrador")

    descartar_borrador(con, "b1")

    assert not Path(factura.ruta_evidencia).exists()
    con.close()


def test_recargos_conservan_el_orden_de_guardado(tmp_path):
    """docs/auditoria-2026-09-confirmacion.md, D-12: sin una columna de
    orden, un re-guardado (UPSERT) podía devolver las filas en otro orden
    en PostgreSQL -- st.data_editor aplica las ediciones por POSICIÓN, así
    que una corrección podía terminar aplicada al recargo equivocado."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="b1")
    factura.recargos = [
        Recargo("Interés por mora", importe=100.0),
        Recargo("Refacturación agosto", importe=200.0),
        Recargo("Interés por mora (segunda vez)", importe=300.0),
    ]
    guardar_factura(con, factura, estado="borrador")

    # Re-guardar (UPSERT) con los recargos en OTRO orden en la lista de
    # Python -- lo que importa es que la base devuelva ESE orden, no el que
    # tenían las filas anteriores.
    factura.recargos = list(reversed(factura.recargos))
    guardar_factura(con, factura, estado="borrador")

    nombres = con.execute(
        "SELECT nombre FROM recargos WHERE hash_pdf = 'b1' ORDER BY orden"
    ).fetchall()
    assert [n[0] for n in nombres] == [
        "Interés por mora (segunda vez)",
        "Refacturación agosto",
        "Interés por mora",
    ]
    con.close()


def test_descartar_borrador_sin_evidencia_no_falla(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(hash_pdf="b1"), estado="borrador")

    descartar_borrador(con, "b1")  # no debe lanzar aunque no haya ruta_evidencia
    con.close()


def test_descartar_borrador_de_algo_que_no_es_borrador_rechaza(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(hash_pdf="a1"), estado="aprobada")

    with pytest.raises(ValueError, match="borrador"):
        descartar_borrador(con, "a1")
    # No se borró nada -- rechazar tiene que ser atómico con no hacer nada.
    assert factura_ya_procesada(con, "a1")
    con.close()


# --- score_homologacion: persistido siempre, haya homologado o no ---------


def test_score_homologacion_se_guarda_para_concepto_homologado(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(
        con, factura, conceptos_normalizados={0: "abono_movil"}, scores_homologacion={0: 0.81}
    )
    score = con.execute(
        "SELECT score_homologacion FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert score == pytest.approx(0.81)
    con.close()


def test_score_homologacion_se_guarda_incluso_sin_homologar(tmp_path):
    # El caso que importa para calibrar: un concepto que NO homologó igual
    # guarda su score (0.44, por ejemplo) -- así se puede distinguir "casi
    # homologa, falta un alias" de "concepto genuinamente nuevo".
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, conceptos_normalizados={}, scores_homologacion={0: 0.44})
    fila = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos WHERE hash_pdf = ?",
        [factura.hash_pdf],
    ).fetchone()
    assert fila == (None, pytest.approx(0.44))
    con.close()


def test_score_homologacion_por_defecto_es_null(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)  # sin pasar scores_homologacion
    score = con.execute(
        "SELECT score_homologacion FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert score is None
    con.close()


def test_alter_table_score_homologacion_es_idempotente(tmp_path):
    ruta = tmp_path / "test.duckdb"
    conectar(ruta).close()
    # forzar_ddl=True: sin esto, el DDL se memoiza por destino (A-52) y la
    # segunda llamada no lo correría -- acá se quiere ejercitar de verdad
    # que ALTER TABLE ... ADD COLUMN IF NOT EXISTS no rompe al repetirse.
    conectar(ruta, forzar_ddl=True).close()


def test_reprocesar_no_duplica_conceptos(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)
    guardar_factura(con, factura)  # reprocesar la misma carpeta
    cantidad = con.execute(
        "SELECT COUNT(*) FROM conceptos WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert cantidad == 1
    con.close()


def test_factura_rota_va_a_cuarentena_no_a_facturas(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="rota456")
    factura.conceptos[0].importe = 999999.0  # rompe la validación
    resultado = validar_factura(factura)
    assert not resultado.factura_valida

    guardar_en_cuarentena(
        con, hash_pdf=factura.hash_pdf, ruta_pdf=factura.ruta_pdf, resultado=resultado
    )
    assert factura_ya_procesada(con, factura.hash_pdf)
    en_facturas = con.execute(
        "SELECT 1 FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()
    assert en_facturas is None  # nunca entró a facturas
    motivos = con.execute(
        "SELECT motivos FROM cuarentena WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert "línea" in motivos
    con.close()


def test_recargos_del_periodo(tmp_path):
    from core.extraccion.esquema import Recargo

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    factura.recargos = [Recargo("Interés por mora", importe=350.0)]
    guardar_factura(con, factura)

    recargos = recargos_del_periodo(con, servicio="telefonia", periodo_desde="2026-08-01")
    assert recargos == [("Interés por mora", 350.0)]

    sin_recargos = recargos_del_periodo(con, servicio="telefonia", periodo_desde="2020-01-01")
    assert sin_recargos == []
    con.close()


def test_guardar_y_leer_alertas_del_periodo(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)

    guardar_alertas(
        con,
        factura.hash_pdf,
        [Alerta(tipo="item_duplicado", severidad="media", mensaje="test", concepto="Abono")],
    )

    alertas = alertas_del_periodo(con, servicio="telefonia", periodo_desde="2026-08-01")
    assert len(alertas) == 1
    assert alertas[0].tipo == "item_duplicado"
    assert alertas[0].concepto == "Abono"

    sin_alertas = alertas_del_periodo(con, servicio="telefonia", periodo_desde="2020-01-01")
    assert sin_alertas == []
    con.close()


# --- A-7: tope de llamadas por hora --------------------------------------


def test_llamadas_ultima_hora_cuenta_intentos_reales_no_facturas_guardadas(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo B-4: antes contaba filas
    de `facturas` + `cuarentena` -- un PROXY. Una extracción que fallaba
    (`ExtraccionError`) no dejaba fila en ninguna de las dos, así que no
    contaba, aunque sí gastó una llamada real a la API. Ahora cuenta la
    tabla `intentos_gemini`, que se escribe en CADA llamada real, salga
    bien o mal -- ver `core/pipeline.py`."""
    con = conectar(tmp_path / "test.duckdb")
    assert llamadas_ultima_hora(con) == 0

    registrar_intento_gemini(con, hash_pdf="a1", ruta_pdf="a1.pdf", exito=True)
    assert llamadas_ultima_hora(con) == 1

    # Un intento FALLIDO (nunca llegó a escribir en `facturas` ni en
    # `cuarentena`) también cuenta -- es justo el caso que el esquema viejo
    # se perdía.
    registrar_intento_gemini(
        con, hash_pdf="b2", ruta_pdf="b2.pdf", exito=False, mensaje="503 del lado de Gemini"
    )
    assert llamadas_ultima_hora(con) == 2

    # Guardar una factura o mandarla a cuarentena, sin pasar por
    # registrar_intento_gemini (ej. los tests que arman el escenario a
    # mano), NO debe inflar el contador -- ya no es lo que se cuenta.
    guardar_factura(con, _factura(hash_pdf="c3"))
    assert llamadas_ultima_hora(con) == 2
    con.close()


def test_proxima_ventana_libre_es_la_llamada_mas_vieja_mas_una_hora(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-8: devuelve un
    timedelta (cuánto falta), no un datetime del servidor -- así el
    llamador lo suma a la hora ACTUAL en SU zona horaria, en vez de mostrar
    la hora tal cual la guardó la base (potencialmente en otro huso)."""
    con = conectar(tmp_path / "test.duckdb")
    assert proxima_ventana_libre(con, tope=1) is None  # sin llamadas, nada que esperar

    registrar_intento_gemini(con, hash_pdf="a1", ruta_pdf="a1.pdf", exito=True)
    # Mismo cálculo que hace proxima_ventana_libre, todo en SQL -- ahorra
    # mezclar el datetime naive de creado_en con el now() tz-aware de
    # DuckDB en Python, que directamente no se puede restar.
    esperado = con.execute(
        """SELECT (creado_en + INTERVAL '1 hour') - now()
           FROM intentos_gemini WHERE hash_pdf = 'a1'"""
    ).fetchone()[0]

    destrabe = proxima_ventana_libre(con, tope=1)
    # Comparación con tolerancia: hay microsegundos entre el `now()` de la
    # query de arriba y el de `proxima_ventana_libre` -- no exactamente el
    # mismo instante.
    assert abs((destrabe - esperado).total_seconds()) < 1
    con.close()


def test_proxima_ventana_libre_con_conteo_por_encima_del_tope(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-11: si hay MÁS
    llamadas que el tope (ej. porque se bajó el tope después de haberlas
    hecho), sacar solo la llamada MÁS VIEJA no alcanza -- siguen quedando
    `tope` o más dentro de la ventana. Con 4 llamadas y tope 2, hacen falta
    DOS salidas para volver a tener margen: la ventana libre depende de la
    llamada en la posición `cantidad - tope` (índice 2, la TERCERA más
    vieja de 4), no de la primera."""
    con = conectar(tmp_path / "test.duckdb")
    for i in range(4):
        registrar_intento_gemini(con, hash_pdf=f"a{i}", ruta_pdf=f"a{i}.pdf", exito=True)

    # offset = cantidad(4) - tope(2) = 2 -> la TERCERA más vieja (índice 2).
    esperado = con.execute(
        """SELECT (creado_en + INTERVAL '1 hour') - now() FROM (
               SELECT creado_en FROM intentos_gemini ORDER BY creado_en LIMIT 1 OFFSET 2
           ) t"""
    ).fetchone()[0]
    # Si el bug de C-11 siguiera ahí (mirar siempre la más vieja), el
    # resultado sería DISTINTO y menor -- una hora que sigue bloqueada.
    esperado_bug_viejo = con.execute(
        """SELECT (creado_en + INTERVAL '1 hour') - now() FROM (
               SELECT creado_en FROM intentos_gemini ORDER BY creado_en LIMIT 1
           ) t"""
    ).fetchone()[0]
    assert esperado != esperado_bug_viejo

    destrabe = proxima_ventana_libre(con, tope=2)
    assert abs((destrabe - esperado).total_seconds()) < 1
    con.close()


def test_intentos_gemini_fallidos_recientes_solo_trae_los_fallidos(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    registrar_intento_gemini(con, hash_pdf="ok", ruta_pdf="ok.pdf", exito=True)
    registrar_intento_gemini(
        con,
        hash_pdf="mal",
        ruta_pdf="mal.pdf",
        exito=False,
        mensaje="La respuesta de Gemini no es JSON válido",
        respuesta_cruda="{esto no es json",
    )
    fallidos = intentos_gemini_fallidos_recientes(con)
    assert len(fallidos) == 1
    ruta_pdf, mensaje, respuesta_cruda, _creado_en = fallidos[0]
    assert ruta_pdf == "mal.pdf"
    assert "JSON" in mensaje
    assert respuesta_cruda == "{esto no es json"
    con.close()


def test_registrar_intento_gemini_purga_filas_mas_viejas_que_la_retencion(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-9: la tabla
    crecía sin límite -- cada llamada real ahora purga lo más viejo que la
    retención configurada (30 días en data/operacion.yaml)."""
    con = conectar(tmp_path / "test.duckdb")
    con.execute(
        """INSERT INTO intentos_gemini (id, hash_pdf, ruta_pdf, exito, mensaje, creado_en)
           VALUES ('viejo', 'viejo', 'viejo.pdf', true, '', now() - INTERVAL '31 days')"""
    )
    assert con.execute("SELECT count(*) FROM intentos_gemini").fetchone()[0] == 1

    registrar_intento_gemini(con, hash_pdf="nuevo", ruta_pdf="nuevo.pdf", exito=True)

    filas = con.execute("SELECT hash_pdf FROM intentos_gemini").fetchall()
    assert filas == [("nuevo",)]  # la fila vieja se purgó, la nueva quedó
    con.close()


# --- A-17: reintentar desde cuarentena ------------------------------------


def test_borrar_de_cuarentena_libera_el_hash_para_reprocesar(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura(hash_pdf="rota789")
    factura.conceptos[0].importe = 999999.0
    resultado = validar_factura(factura)

    guardar_en_cuarentena(
        con, hash_pdf=factura.hash_pdf, ruta_pdf=factura.ruta_pdf, resultado=resultado
    )
    assert factura_ya_procesada(con, factura.hash_pdf)

    borrar_de_cuarentena(con, factura.hash_pdf)
    assert not factura_ya_procesada(con, factura.hash_pdf)
    con.close()


def test_guardar_alertas_es_idempotente(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura)

    alerta = [Alerta(tipo="item_duplicado", severidad="media", mensaje="test", concepto="Abono")]
    guardar_alertas(con, factura.hash_pdf, alerta)
    guardar_alertas(con, factura.hash_pdf, alerta)  # reprocesar no duplica

    cantidad = con.execute(
        "SELECT COUNT(*) FROM alertas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert cantidad == 1
    con.close()


# --- conceptos_sin_clasificar: ordenado por plata (Bloque 5) --------------


def test_conceptos_sin_clasificar_ordena_por_importe_descendente(tmp_path):
    con = conectar(tmp_path / "test.duckdb")

    barato = _factura("h1")
    barato.conceptos = [Concepto("Cargo raro chico", 1, None, 500.0, 500.0)]
    guardar_factura(con, barato, scores_homologacion={0: 0.3})

    caro = _factura("h2")
    caro.conceptos = [Concepto("Cargo raro grande", 1, None, 8000.0, 8000.0)]
    guardar_factura(con, caro, scores_homologacion={0: 0.4})

    filas = conceptos_sin_clasificar(con)
    assert [f[1] for f in filas] == ["Cargo raro grande", "Cargo raro chico"]
    assert filas[0][3] == pytest.approx(8000.0)
    con.close()


def test_conceptos_sin_clasificar_agrupa_por_servicio_y_descripcion(tmp_path):
    con = conectar(tmp_path / "test.duckdb")

    f1 = _factura("h1")
    f1.periodo_desde = "2026-08-01"
    f1.conceptos = [Concepto("Servicio de telefonía Agosto 2026", 1, None, 30000.0, 30000.0)]
    guardar_factura(con, f1, scores_homologacion={0: 0.588})

    f2 = _factura("h2")
    f2.periodo_desde = "2026-09-01"
    f2.conceptos = [Concepto("Servicio de telefonía Septiembre 2026", 1, None, 36000.0, 36000.0)]
    guardar_factura(con, f2, scores_homologacion={0: 0.588})

    filas = conceptos_sin_clasificar(con)
    # Dos descripciones DISTINTAS (con el período incluido, tal como se
    # guardaron) -- esta consulta lee la descripción cruda, la agrupación
    # por concepto normalizado ya la hace core.analisis.agregacion.
    assert len(filas) == 2
    assert sum(f[3] for f in filas) == pytest.approx(66000.0)
    con.close()


def test_conceptos_sin_clasificar_excluye_los_ya_homologados(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, conceptos_normalizados={0: "abono_movil"})
    assert conceptos_sin_clasificar(con) == []
    con.close()


def test_conceptos_sin_clasificar_acotado_por_servicio(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura("h1"), scores_homologacion={0: 0.3})
    otro = _factura("h2")
    otro.servicio = "gas"
    otro.conceptos = [Concepto("Consumo de gas raro", 1, None, 1000.0, 1000.0)]
    guardar_factura(con, otro, scores_homologacion={0: 0.3})

    filas = conceptos_sin_clasificar(con, servicio="telefonia")
    assert len(filas) == 1
    assert filas[0][0] == "telefonia"
    con.close()


def test_conceptos_sin_clasificar_excluye_facturas_rechazadas(tmp_path):
    """docs/auditoria-2026-09-piloto.md, A-55: antes de esta corrección, la
    pantalla de calibración mostraba plata y conceptos de facturas
    rechazadas, que el análisis (Evolución, totales) ya ignora."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada", scores_homologacion={0: 0.3})
    decision_factura(
        con, hash_pdf=factura.hash_pdf, estado="rechazada", actor="ana", motivo="mal leída"
    )
    assert conceptos_sin_clasificar(con) == []
    con.close()


# --- totales_por_periodo: la serie temporal (Bloque 7) --------------------


def test_totales_por_periodo_suma_conceptos_no_facturas_total(tmp_path):
    from core.almacenamiento import totales_por_periodo

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()  # total=12100.0 (incluye IVA), conceptos suman 10000.0
    guardar_factura(con, factura)

    totales = totales_por_periodo(con, servicio="telefonia")
    # Suma el importe de los CONCEPTOS (10000.0), no facturas.total
    # (12100.0, que incluye IVA) -- a propósito, ver docstring de la función.
    assert totales == {"2026-08-01": pytest.approx(10000.0)}
    con.close()


def test_totales_por_periodo_junta_varias_facturas_del_mismo_periodo(tmp_path):
    from core.almacenamiento import totales_por_periodo

    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura("h1"))
    otra = _factura("h2")
    otra.conceptos = [Concepto("Otro cargo", 1, None, 2000.0, 2000.0)]
    guardar_factura(con, otra)

    totales = totales_por_periodo(con, servicio="telefonia")
    assert totales == {"2026-08-01": pytest.approx(12000.0)}
    con.close()


def test_totales_por_periodo_acotado_por_servicio(tmp_path):
    from core.almacenamiento import totales_por_periodo

    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura())
    otro_servicio = _factura("h2")
    otro_servicio.servicio = "gas"
    otro_servicio.conceptos = [Concepto("Consumo de gas", 1, None, 3000.0, 3000.0)]
    guardar_factura(con, otro_servicio)

    assert totales_por_periodo(con, servicio="gas") == {"2026-08-01": pytest.approx(3000.0)}
    con.close()


# --- piloto operativo: revisión, evidencia y casos ------------------------


def test_factura_en_revision_no_impacta_serie_hasta_aprobacion(tmp_path):
    from core.almacenamiento import totales_por_periodo

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision", actor="ana@empresa.test")

    assert listar_facturas_pendientes(con)[0][0] == factura.hash_pdf
    assert totales_por_periodo(con, servicio="telefonia") == {}

    decision_factura(
        con,
        hash_pdf=factura.hash_pdf,
        estado="aprobada",
        actor="ana@empresa.test",
        motivo="Conciliada contra el PDF original.",
    )
    assert totales_por_periodo(con, servicio="telefonia") == {"2026-08-01": pytest.approx(10000)}
    decisiones = con.execute(
        "SELECT accion, actor FROM decisiones_factura WHERE hash_pdf = ? ORDER BY creado_en",
        [factura.hash_pdf],
    ).fetchall()
    assert decisiones[-1] == ("aprobada", "ana@empresa.test")
    con.close()


def test_aprobar_pendientes_aprueba_todo_el_lote_con_el_mismo_motivo(tmp_path):
    """Aprobar en lote (apps/segurplus/paginas/revision.py, botón "Aprobar
    todas las pendientes") tiene que dejar el mismo rastro de auditoría que
    aprobar cada factura a mano -- ver core.almacenamiento.aprobar_pendientes."""
    from core.almacenamiento import totales_por_periodo

    con = conectar(tmp_path / "test.duckdb")
    f1 = _factura("h1")
    f2 = _factura("h2")
    f2.periodo_desde = "2026-09-01"
    guardar_factura(con, f1, estado="requiere_revision")
    guardar_factura(con, f2, estado="requiere_revision")

    cantidad = aprobar_pendientes(con, actor="ana@empresa.test", motivo="Carga inicial revisada")

    assert cantidad == 2
    assert listar_facturas_pendientes(con) == []
    assert totales_por_periodo(con, servicio="telefonia") == {
        "2026-08-01": pytest.approx(10000.0),
        "2026-09-01": pytest.approx(10000.0),
    }
    for hash_pdf in (f1.hash_pdf, f2.hash_pdf):
        decisiones = con.execute(
            "SELECT accion, actor, motivo FROM decisiones_factura WHERE hash_pdf = ?",
            [hash_pdf],
        ).fetchall()
        assert ("aprobada", "ana@empresa.test", "Carga inicial revisada") in decisiones
    con.close()


def test_aprobar_pendientes_sin_pendientes_no_hace_nada(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura(), estado="aprobada")
    assert aprobar_pendientes(con, actor="ana", motivo="sin pendientes") == 0
    con.close()


def test_correccion_de_cabecera_conserva_valor_anterior(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    registrar_correccion(
        con,
        hash_pdf=factura.hash_pdf,
        campo="periodo_desde",
        valor_nuevo="2026-07-01",
        motivo="El modelo confundió la fecha de emisión.",
        actor="revisor@empresa.test",
    )
    assert (
        con.execute(
            "SELECT periodo_desde FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == "2026-07-01"
    )
    assert con.execute(
        "SELECT valor_anterior, valor_nuevo, actor FROM correcciones_factura WHERE hash_pdf = ?",
        [factura.hash_pdf],
    ).fetchone() == ("2026-08-01", "2026-07-01", "revisor@empresa.test")
    con.close()


def test_correccion_de_fecha_normaliza_formato_mes_anio(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo B-1: corregir a mano
    escribiendo el mismo formato que trae la factura real ("07/2022", sin
    día) tiene que quedar normalizado a ISO como cualquier otra fecha --
    si no, se reintroduce A-11/A-12 por otra vía."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    registrar_correccion(
        con,
        hash_pdf=factura.hash_pdf,
        campo="periodo_desde",
        valor_nuevo="07/2022",
        motivo="La factura solo trae mes y año.",
        actor="revisor@empresa.test",
    )
    assert (
        con.execute(
            "SELECT periodo_desde FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == "2022-07-01"
    )
    con.close()


def test_correccion_de_fecha_no_interpretable_se_rechaza(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    with pytest.raises(ValueError, match="No se pudo interpretar"):
        registrar_correccion(
            con,
            hash_pdf=factura.hash_pdf,
            campo="periodo_desde",
            valor_nuevo="mediados de julio",
            motivo="prueba",
            actor="revisor@empresa.test",
        )
    # No se escribió nada -- el valor original sigue intacto.
    assert (
        con.execute(
            "SELECT periodo_desde FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == factura.periodo_desde
    )
    con.close()


def test_correccion_de_periodo_hasta_mes_anio_normaliza_a_fin_de_mes(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-1: corregir
    `periodo_hasta` a mano con "MM/AAAA" tiene que ir a FIN de mes, igual
    que la extracción -- si no, corregir a mano reintroduce el mismo
    `desde == hasta` que rompía `alertas_por_periodo_faltante`."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    registrar_correccion(
        con,
        hash_pdf=factura.hash_pdf,
        campo="periodo_hasta",
        valor_nuevo="07/2022",
        motivo="La factura solo trae mes y año.",
        actor="revisor@empresa.test",
    )
    assert (
        con.execute(
            "SELECT periodo_hasta FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == "2022-07-31"
    )
    con.close()


def test_correccion_de_servicio_desconocido_se_rechaza(tmp_path):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-3: antes se
    aceptaba en silencio cualquier texto libre -- escribir "luz" en vez de
    "energia" pasaba, pero la factura perdía TODO el diccionario de
    homologación específico de energía (A-3 por otra vía)."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    with pytest.raises(ValueError, match="no es un servicio conocido"):
        registrar_correccion(
            con,
            hash_pdf=factura.hash_pdf,
            campo="servicio",
            valor_nuevo="luz",
            motivo="prueba",
            actor="revisor@empresa.test",
        )
    assert (
        con.execute(
            "SELECT servicio FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == factura.servicio
    )
    con.close()


def test_correccion_de_servicio_conocido_se_acepta(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    registrar_correccion(
        con,
        hash_pdf=factura.hash_pdf,
        campo="servicio",
        valor_nuevo="gas",
        motivo="El modelo confundió el servicio.",
        actor="revisor@empresa.test",
    )
    assert (
        con.execute(
            "SELECT servicio FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == "gas"
    )
    con.close()


def test_alerta_aprobada_crea_caso_deduplicado_y_asignable(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="requiere_revision")
    guardar_alertas(
        con,
        factura.hash_pdf,
        [
            Alerta(
                tipo="item_duplicado", severidad="media", mensaje="Abono repetido", concepto="Abono"
            )
        ],
    )
    decision_factura(
        con, hash_pdf=factura.hash_pdf, estado="aprobada", actor="ana", motivo="Validada"
    )
    casos = listar_casos_alerta(con)
    assert len(casos) == 1
    actualizar_caso_alerta(
        con,
        clave=casos[0][0],
        estado="en_analisis",
        responsable="compras@empresa.test",
        vencimiento="2026-09-20",
        evidencia="Se pidió nota de crédito al proveedor.",
    )
    actualizado = listar_casos_alerta(con)[0]
    assert actualizado[5:] == (
        "en_analisis",
        "compras@empresa.test",
        "2026-09-20",
        "Se pidió nota de crédito al proveedor.",
    )
    con.close()


def test_alertas_de_comparacion_crean_un_solo_caso_por_referencia(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    alertas = [
        Alerta(tipo="precio_sobre_ipc", severidad="alta", mensaje="Suba real", concepto="Abono")
    ]
    for _ in range(2):
        sincronizar_casos_alertas(
            con,
            referencia="comparacion:telefonia:2026-08-01:2026-09-01",
            alertas=alertas,
        )
    assert len(listar_casos_alerta(con)) == 1
    con.close()


def test_resumen_financiero_separa_impuestos_recargos_y_creditos(tmp_path):
    from core.extraccion.esquema import Credito, Impuesto, Recargo

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    factura.impuestos = [Impuesto("IVA", 2100)]
    factura.recargos = [Recargo("Mora", 100)]
    factura.creditos = [Credito("Bonificación", 200)]
    factura.total = 12000
    guardar_factura(con, factura)
    assert resumen_financiero_factura(con, factura.hash_pdf) == {
        "consumos": 10000.0,
        "impuestos": 2100.0,
        "recargos": 100.0,
        "creditos": 200.0,
        "total_pagable": 12000.0,
    }
    con.close()


def test_componentes_financieros_excluye_facturas_no_aprobadas(tmp_path):
    from core.almacenamiento import componentes_financieros_periodo
    from core.extraccion.esquema import Impuesto

    con = conectar(tmp_path / "test.duckdb")
    aprobada = _factura("ok")
    aprobada.impuestos = [Impuesto("IVA", 2100)]
    aprobada.total = 12100
    guardar_factura(con, aprobada, estado="aprobada")
    pendiente = _factura("pendiente")
    pendiente.conceptos = [Concepto("Otro", 1, None, 5000, 5000)]
    pendiente.subtotal = pendiente.total = 5000
    guardar_factura(con, pendiente, estado="requiere_revision")
    assert componentes_financieros_periodo(
        con, servicio="telefonia", periodo_desde="2026-08-01"
    ) == {
        "consumos": 10000.0,
        "impuestos": 2100.0,
        "recargos": 0.0,
        "creditos": 0.0,
        "total_pagable": 12100.0,
    }
    con.close()


# --- métricas por proveedor (Bloque 5) -------------------------------------


def test_metricas_por_proveedor_combina_cargadas_cuarentena_y_sin_homologar(tmp_path):
    con = conectar(tmp_path / "test.duckdb")

    # Movistar: 1 factura cargada con 1 concepto sin homologar, 1 en cuarentena.
    movistar_ok = _factura("movistar_ok")
    guardar_factura(con, movistar_ok, conceptos_normalizados={})  # no homologó nada
    movistar_rota = _factura("movistar_rota")
    movistar_rota.conceptos[0].importe = 999999.0
    resultado = validar_factura(movistar_rota)
    guardar_en_cuarentena(
        con,
        hash_pdf=movistar_rota.hash_pdf,
        ruta_pdf=movistar_rota.ruta_pdf,
        resultado=resultado,
        emisor=movistar_rota.emisor,
        servicio=movistar_rota.servicio,
    )

    # Edesur: 1 factura cargada, homologada del todo (0 sin homologar).
    edesur = _factura("edesur_ok")
    edesur.emisor = "Edesur"
    guardar_factura(con, edesur, conceptos_normalizados={0: "abono_movil"})

    filas = {fila[0]: fila for fila in metricas_por_proveedor(con)}

    # (emisor, facturas_cargadas, facturas_en_cuarentena, facturas_rechazadas,
    #  conceptos_totales, conceptos_sin_homologar, importe_sin_homologar)
    assert filas["Movistar"] == ("Movistar", 1, 1, 0, 1, 1, 10000.0)
    assert filas["Edesur"] == ("Edesur", 1, 0, 0, 1, 0, 0.0)
    con.close()


def test_metricas_por_proveedor_agrupa_emisor_desconocido(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    sin_emisor = _factura("sin_emisor")
    sin_emisor.emisor = None
    guardar_factura(con, sin_emisor)

    filas = {fila[0]: fila for fila in metricas_por_proveedor(con)}
    assert filas["(sin emisor)"][1] == 1
    con.close()


def test_metricas_por_proveedor_cuenta_rechazadas_y_no_sus_conceptos(tmp_path):
    """docs/auditoria-2026-09-piloto.md, A-55: las columnas de conceptos
    (totales, sin homologar, importe) solo miran facturas aprobadas -- una
    rechazada se cuenta en facturas_rechazadas, pero sus conceptos no
    contaminan las cifras de calibración."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada", conceptos_normalizados={})
    decision_factura(
        con, hash_pdf=factura.hash_pdf, estado="rechazada", actor="ana", motivo="mal leída"
    )

    filas = {fila[0]: fila for fila in metricas_por_proveedor(con)}
    fila = filas["Movistar"]
    assert fila[1] == 1  # facturas_cargadas: sigue contando la rechazada
    assert fila[3] == 1  # facturas_rechazadas
    assert fila[4] == 0  # conceptos_totales: NO cuenta los de la rechazada
    assert fila[5] == 0  # conceptos_sin_homologar
    assert fila[6] == 0.0  # importe_sin_homologar
    con.close()


def test_metricas_por_proveedor_no_cuenta_borradores_como_cargadas(tmp_path):
    """Un borrador todavía no fue confirmado por el usuario (ver
    core.pipeline.confirmar_factura) -- "Facturas cargadas" en la pantalla
    "Conceptos sin clasificar" no debe contarlo como si ya hubiera entrado
    al circuito."""
    con = conectar(tmp_path / "test.duckdb")
    aprobada = _factura("aprobada")
    guardar_factura(con, aprobada, estado="aprobada")
    borrador = _factura("un_borrador")
    guardar_factura(con, borrador, estado="borrador")

    filas = {fila[0]: fila for fila in metricas_por_proveedor(con)}
    assert filas["Movistar"][1] == 1  # facturas_cargadas: solo la aprobada
    con.close()


def test_contar_borradores_cuenta_solo_estado_borrador(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(con, _factura("aprobada"), estado="aprobada")
    guardar_factura(con, _factura("borrador1"), estado="borrador")
    guardar_factura(con, _factura("borrador2"), estado="borrador")

    assert contar_borradores(con) == 2
    con.close()


def test_contar_borradores_respeta_el_filtro_de_servicio(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    borrador_telefonia = _factura("b1")
    guardar_factura(con, borrador_telefonia, estado="borrador")
    borrador_gas = replace(_factura("b2"), servicio="gas")
    guardar_factura(con, borrador_gas, estado="borrador")

    assert contar_borradores(con, servicio="telefonia") == 1
    assert contar_borradores(con, servicio="gas") == 1
    assert contar_borradores(con, servicio="agua") == 0
    con.close()


def test_motivos_cuarentena_por_proveedor_desglosa_cada_motivo(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura("rota_dos_motivos")
    factura.conceptos[0].importe = 999999.0
    factura.total = 1.0  # agrega un segundo motivo de falla
    resultado = validar_factura(factura)
    assert len(resultado.motivos_de_falla()) >= 2
    guardar_en_cuarentena(
        con,
        hash_pdf=factura.hash_pdf,
        ruta_pdf=factura.ruta_pdf,
        resultado=resultado,
        emisor="Movistar",
        servicio="telefonia",
    )
    motivos = motivos_cuarentena_por_proveedor(con)
    assert len(motivos) == len(resultado.motivos_de_falla())
    assert all(emisor == "Movistar" for emisor, _motivo, _veces in motivos)
    assert all(veces == 1 for _emisor, _motivo, veces in motivos)
    con.close()


# --- A-49: conectar(ruta) nunca debe ir a Postgres si se pide un archivo --


def test_conectar_con_ruta_explicita_usa_duckdb_aunque_haya_database_url(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, A-49: antes de esta corrección,
    conectar(ruta) miraba DATABASE_URL primero e ignoraba `ruta` por
    completo -- un pytest de rutina con esa variable exportada terminaba
    escribiendo en la base de producción en vez del archivo temporal que
    el test pedía explícitamente. Con una DATABASE_URL a un host que no
    existe, si conectar() intentara usarla explotaría -- que NO explote
    (y que devuelva una conexión DuckDB funcional) prueba que la ignoró."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host-inexistente.invalid:5432/db")
    con = conectar(tmp_path / "explicita.duckdb")
    con.execute("SELECT 1").fetchone()  # una conexión DuckDB real responde
    assert (tmp_path / "explicita.duckdb").exists()
    con.close()


# --- A-50: corregir y rechazar una factura YA APROBADA --------------------


def test_rechazar_una_factura_ya_aprobada(tmp_path):
    """docs/auditoria-2026-09-piloto.md, A-50: con revision_humana_obligatoria
    en false (el default), toda factura nace aprobada -- antes de esta
    corrección, decision_factura exigía 'requiere_revision' como estado de
    partida, así que una factura mal leída no se podía rechazar nunca."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada")
    assert listar_facturas_aprobadas(con)[0][0] == factura.hash_pdf

    decision_factura(
        con,
        hash_pdf=factura.hash_pdf,
        estado="rechazada",
        actor="ana",
        motivo="Emisor equivocado, era otro proveedor.",
    )
    estado = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert estado == "rechazada"
    assert listar_facturas_aprobadas(con) == []
    decisiones = con.execute(
        "SELECT accion, actor FROM decisiones_factura WHERE hash_pdf = ? ORDER BY creado_en",
        [factura.hash_pdf],
    ).fetchall()
    assert decisiones[-1] == ("rechazada", "ana")
    con.close()


def test_rechazar_una_factura_aprobada_borra_sus_casos(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada")
    alerta = Alerta(
        tipo="item_duplicado", severidad="media", mensaje="Abono repetido", concepto="Abono"
    )
    guardar_alertas(con, factura.hash_pdf, [alerta])
    sincronizar_casos_alertas(con, referencia=factura.hash_pdf, alertas=[alerta])
    assert len(listar_casos_alerta(con)) == 1

    decision_factura(
        con, hash_pdf=factura.hash_pdf, estado="rechazada", actor="ana", motivo="mal leída"
    )
    assert listar_casos_alerta(con) == []
    con.close()


def test_corregir_cabecera_de_una_factura_ya_aprobada(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada")

    registrar_correccion(
        con,
        hash_pdf=factura.hash_pdf,
        campo="servicio",
        valor_nuevo="gas",
        motivo="El modelo confundió el servicio.",
        actor="revisor@empresa.test",
    )
    assert (
        con.execute(
            "SELECT servicio FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
        ).fetchone()[0]
        == "gas"
    )
    con.close()


def test_no_se_puede_corregir_ni_decidir_una_factura_rechazada(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada")
    decision_factura(con, hash_pdf=factura.hash_pdf, estado="rechazada", actor="ana", motivo="mal")

    with pytest.raises(ValueError):
        registrar_correccion(
            con,
            hash_pdf=factura.hash_pdf,
            campo="servicio",
            valor_nuevo="gas",
            motivo="m",
            actor="a",
        )
    with pytest.raises(ValueError):
        decision_factura(con, hash_pdf=factura.hash_pdf, estado="aprobada", actor="a", motivo="m")
    con.close()


# --- A-56: una factura rechazada libera el hash para volver a cargarse ----


def test_factura_ya_procesada_es_false_para_una_rechazada(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada")
    assert factura_ya_procesada(con, factura.hash_pdf)

    decision_factura(
        con, hash_pdf=factura.hash_pdf, estado="rechazada", actor="ana", motivo="mal leída"
    )
    assert not factura_ya_procesada(con, factura.hash_pdf)
    con.close()


def test_factura_ya_procesada_sigue_true_para_una_aprobada_o_pendiente(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    aprobada = _factura("aprobada")
    guardar_factura(con, aprobada, estado="aprobada")
    pendiente = _factura("pendiente")
    guardar_factura(con, pendiente, estado="requiere_revision")

    assert factura_ya_procesada(con, aprobada.hash_pdf)
    assert factura_ya_procesada(con, pendiente.hash_pdf)
    con.close()


def test_reguardar_una_factura_rechazada_la_reprocesa_de_cero(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    guardar_factura(con, factura, estado="aprobada")
    decision_factura(
        con, hash_pdf=factura.hash_pdf, estado="rechazada", actor="ana", motivo="mal leída"
    )

    guardar_factura(con, factura, estado="aprobada")  # se vuelve a "subir" el mismo PDF
    estado = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [factura.hash_pdf]
    ).fetchone()[0]
    assert estado == "aprobada"
    # el rechazo anterior sigue en la auditoría, no se borró ni se editó
    decisiones = [
        accion
        for (accion,) in con.execute(
            "SELECT accion FROM decisiones_factura WHERE hash_pdf = ? ORDER BY creado_en",
            [factura.hash_pdf],
        ).fetchall()
    ]
    assert "rechazada" in decisiones
    con.close()


# --- A-52: el DDL se corre una sola vez por destino, no por conexión ------


def test_ddl_se_ejecuta_una_sola_vez_por_destino(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, A-52: antes de esta corrección,
    conectar() corría las 27 sentencias del DDL en CADA llamada -- contra
    un Postgres remoto, eso son 27 round-trips de red por cada render de
    página de Streamlit."""
    import core.almacenamiento as almacenamiento_mod

    llamadas = []
    original = almacenamiento_mod._ejecutar_ddl

    def _espia(con):
        llamadas.append(1)
        return original(con)

    monkeypatch.setattr(almacenamiento_mod, "_ejecutar_ddl", _espia)

    ruta = tmp_path / "test.duckdb"
    conectar(ruta).close()
    conectar(ruta).close()
    conectar(ruta).close()
    assert len(llamadas) == 1


def test_ddl_se_ejecuta_por_cada_destino_distinto(tmp_path, monkeypatch):
    import core.almacenamiento as almacenamiento_mod

    llamadas = []
    original = almacenamiento_mod._ejecutar_ddl

    def _espia(con):
        llamadas.append(1)
        return original(con)

    monkeypatch.setattr(almacenamiento_mod, "_ejecutar_ddl", _espia)

    conectar(tmp_path / "a.duckdb").close()
    conectar(tmp_path / "b.duckdb").close()
    assert len(llamadas) == 2


def test_forzar_ddl_ignora_la_memoizacion(tmp_path, monkeypatch):
    import core.almacenamiento as almacenamiento_mod

    llamadas = []
    original = almacenamiento_mod._ejecutar_ddl

    def _espia(con):
        llamadas.append(1)
        return original(con)

    monkeypatch.setattr(almacenamiento_mod, "_ejecutar_ddl", _espia)

    ruta = tmp_path / "test.duckdb"
    conectar(ruta).close()
    conectar(ruta, forzar_ddl=True).close()
    assert len(llamadas) == 2


# --- A-53: sincronizar_casos_alertas no reescribe si nada cambió ----------


def test_sincronizar_casos_alertas_no_reescribe_lo_que_no_cambio(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    alerta = Alerta(
        tipo="item_duplicado", severidad="media", mensaje="Abono repetido", concepto="Abono"
    )

    sincronizar_casos_alertas(con, referencia="comparacion:x", alertas=[alerta])
    actualizado_1 = con.execute("SELECT actualizado_en FROM casos_alerta").fetchone()[0]

    # Llamar de nuevo con la MISMA alerta (simula navegar la misma página
    # de Evolución otra vez, sin que nada haya cambiado).
    sincronizar_casos_alertas(con, referencia="comparacion:x", alertas=[alerta])
    actualizado_2 = con.execute("SELECT actualizado_en FROM casos_alerta").fetchone()[0]

    assert actualizado_1 == actualizado_2  # no se reescribió
    assert con.execute("SELECT COUNT(*) FROM casos_alerta").fetchone()[0] == 1
    con.close()


def test_sincronizar_casos_alertas_si_reescribe_cuando_cambia_la_severidad(tmp_path):
    from core.analisis.alertas import Alerta

    con = conectar(tmp_path / "test.duckdb")
    leve = Alerta(
        tipo="item_duplicado", severidad="baja", mensaje="Abono repetido", concepto="Abono"
    )
    sincronizar_casos_alertas(con, referencia="comparacion:x", alertas=[leve])

    # Misma clave (mismo tipo/concepto/mensaje/referencia), pero severidad
    # distinta -- SÍ tiene que reescribir.
    grave = Alerta(
        tipo="item_duplicado", severidad="alta", mensaje="Abono repetido", concepto="Abono"
    )
    sincronizar_casos_alertas(con, referencia="comparacion:x", alertas=[grave])

    severidad = con.execute("SELECT severidad FROM casos_alerta").fetchone()[0]
    assert severidad == "alta"
    con.close()


def test_sincronizar_casos_alertas_con_lista_vacia_no_hace_nada(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    sincronizar_casos_alertas(con, referencia="comparacion:x", alertas=[])  # no debe explotar
    assert listar_casos_alerta(con) == []
    con.close()


# --- A-59: motivos de cuarentena separados por "\n", no "; " -------------


def test_motivo_que_contiene_punto_y_coma_no_se_parte(tmp_path):
    """docs/auditoria-2026-09-piloto.md, A-59: con "; " como separador, un
    motivo que por casualidad contuviera esa secuencia se partía en dos.
    Con "\\n" como separador entre motivos, un motivo individual que
    contenga "; " queda intacto -- a diferencia del test de compatibilidad
    de abajo, acá SÍ hay más de un motivo (y por lo tanto un "\\n" real en
    el string guardado), que es el caso en que la ambigüedad con el
    formato viejo no existe."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    resultado = validar_factura(factura)  # válida -- los motivos son sintéticos
    guardar_en_cuarentena(
        con,
        hash_pdf=factura.hash_pdf,
        ruta_pdf=factura.ruta_pdf,
        resultado=resultado,
        emisor="Movistar",
    )
    # Sobreescribe el campo motivos directo, simulando el caso del hallazgo:
    # dos motivos reales, uno de ellos con "; " adentro.
    con.execute(
        "UPDATE cuarentena SET motivos = ? WHERE hash_pdf = ?",
        [
            "el subtotal (que debería ser X; revisar) no cierra\nel total no cierra",
            factura.hash_pdf,
        ],
    )
    motivos = motivos_cuarentena_por_proveedor(con)
    assert motivos == [
        ("Movistar", "el subtotal (que debería ser X; revisar) no cierra", 1),
        ("Movistar", "el total no cierra", 1),
    ]
    con.close()


def test_motivos_cuarentena_compatible_con_formato_viejo_separado_por_punto_y_coma(tmp_path):
    """Una fila guardada ANTES de este cambio (separador "; ", sin ningún
    "\\n") se sigue partiendo correctamente -- no hace falta migrar datos
    viejos para que la pantalla siga funcionando."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura()
    resultado = validar_factura(factura)
    guardar_en_cuarentena(
        con,
        hash_pdf=factura.hash_pdf,
        ruta_pdf=factura.ruta_pdf,
        resultado=resultado,
        emisor="Movistar",
    )
    con.execute(
        "UPDATE cuarentena SET motivos = ? WHERE hash_pdf = ?",
        ["motivo viejo uno; motivo viejo dos", factura.hash_pdf],
    )
    motivos = motivos_cuarentena_por_proveedor(con)
    assert set(motivos) == {
        ("Movistar", "motivo viejo uno", 1),
        ("Movistar", "motivo viejo dos", 1),
    }
    con.close()
