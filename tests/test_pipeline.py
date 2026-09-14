"""Test del pipeline completo de punta a punta contra las fixtures
sintéticas, con la llamada a Gemini reemplazada por monkeypatch (no pega a
la red -- lo que Gemini devolvería ya se conoce de memoria, porque las
fixtures se generaron con esos valores exactos, ver
docs/fixtures/generar_fixtures.py)."""

from dataclasses import replace
from pathlib import Path

import pytest

import core.pipeline as pipeline_mod
from core.almacenamiento import conectar
from core.extraccion.esquema import Concepto, FacturaExtraida, Impuesto
from core.pipeline import procesar_pdf

FIXTURES = Path(__file__).resolve().parent.parent / "docs" / "fixtures" / "sintetico"


def _factura_telefonia_julio() -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio="telefonia",
        periodo_desde="2026-07-01",
        periodo_hasta="2026-07-31",
        fecha_emision="2026-07-05",
        fecha_vencimiento=None,
        numero_comprobante="0001-00045501",
        moneda="ARS",
        conceptos=[
            Concepto("Abono 4 líneas móviles", 4, "línea", 2500.0, 10000.0),
            Concepto("Consumo de datos adicional", 8, "GB", 50.0, 400.0),
        ],
        impuestos=[Impuesto("IVA 21%", importe=2184.0)],
        subtotal=10400.0,
        total=12584.0,
    )


def _factura_rota() -> FacturaExtraida:
    return FacturaExtraida(
        emisor="Comunicaciones Sur S.A.",
        cuit="30-71234567-8",
        servicio="telefonia",
        periodo_desde="2026-09-01",
        periodo_hasta="2026-09-30",
        fecha_emision="2026-09-05",
        fecha_vencimiento=None,
        numero_comprobante="0001-00099999",
        moneda="ARS",
        conceptos=[Concepto("Abono 5 líneas móviles", 5, "línea", 100.0, 800.0)],  # debería ser 500
        impuestos=[Impuesto("IVA 21%", importe=168.0)],
        subtotal=800.0,
        total=968.0,
    )


def test_factura_valida_se_guarda(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "guardada"
    fila = con.execute(
        "SELECT emisor, total FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila == ("Comunicaciones Sur S.A.", 12584.0)
    fila_estado = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    # data/operacion.yaml::revision_humana_obligatoria default es false -- una
    # factura procesada queda aprobada directo, sin paso manual intermedio.
    assert fila_estado == ("aprobada",)
    con.close()


def test_factura_con_revision_obligatoria_queda_pendiente(tmp_path, monkeypatch):
    """Con `revision_humana_obligatoria` en true (data/operacion.yaml), la
    misma factura válida queda `requiere_revision` en vez de `aprobada` --
    no impacta Evolución/alertas/Excel hasta que alguien la apruebe (ver
    apps/segurplus/paginas/revision.py)."""
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    monkeypatch.setattr(pipeline_mod, "revision_humana_obligatoria", lambda: True)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "guardada"
    fila_estado = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila_estado == ("requiere_revision",)
    con.close()


def test_factura_sin_periodo_no_queda_aprobada_ni_dice_guardada(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, hallazgo B-1, reproducido con
    facturas reales de la Usina Popular de Tandil: el modelo puede leer
    perfectamente el período impreso en la factura y aun así no lograr
    devolverlo en un formato que `_normalizar_fecha` interprete (antes de
    esta corrección, "07/2022" -- mes/año, sin día -- ya se arreglaba en
    `_normalizar_fecha`, pero acá se simula el caso general: CUALQUIER
    motivo por el que `periodo_desde` llegue en `None`). Con el pipeline
    viejo, esa factura validaba bien, quedaba `aprobada` (el default) y la
    UI decía "guardada y validada" -- pero `totales_por_periodo` y el resto
    del análisis filtran `periodo_desde IS NOT NULL`, así que desaparecía
    sin ningún aviso. Ahora nunca queda aprobada sin período, sea cual sea
    `revision_humana_obligatoria`, y el estado que ve la UI lo dice."""
    factura_sin_periodo = replace(_factura_telefonia_julio(), periodo_desde=None)
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura_sin_periodo)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "necesita_datos"
    assert "periodo_desde" in resultado.detalle
    fila_estado = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila_estado == ("requiere_revision",)
    con.close()


def test_pipeline_de_punta_a_punta_con_fixture_de_gas_periodo_mes_anio(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgos B-1, B-6 y C-1,
    de punta a punta contra `docs/fixtures/sintetico/gas_2026-07.pdf` --
    reproduce la FORMA real que rompía (período impreso solo "MM/AAAA",
    bimestral, detalle de cálculo pegado a la descripción), sin ser ninguna
    factura real (CLAUDE.md: nunca facturas reales en tests).
    `factura_desde_json` recibe el dict tal como lo devolvería el modelo --
    con "periodo_desde": "07/2026" SIN convertir -- para ejercitar la
    normalización real de `_normalizar_fecha`, no un valor ya normalizado a
    mano."""
    from core.extraccion.esquema import factura_desde_json

    datos_como_los_devolveria_gemini = {
        "emisor": "Gas del Centro S.A.",
        "cuit": "30-65786428-1",
        "servicio": "gas",
        "periodo_desde": "07/2026",  # tal cual lo imprime la factura, sin día
        "periodo_hasta": "08/2026",  # bimestral
        "fecha_emision": "01/09/2026",
        "moneda": "ARS",
        "conceptos": [
            {
                "descripcion": "Cargo Fijo (100,00 / 30 x 60)",
                "cantidad": 1,
                "precio_unitario": 200.0,
                "importe": 200.0,
            },
            {
                "descripcion": "Consumo de Gas",
                "cantidad": 805,
                "unidad": "m3",
                "precio_unitario": 6.4,
                "importe": 5152.0,
            },
        ],
        "impuestos": [{"nombre": "IVA 21%", "importe": 1123.92}],
        "subtotal": 5352.0,
        "total": 6475.92,
    }
    factura = factura_desde_json(datos_como_los_devolveria_gemini)
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "gas_2026-07.pdf", con, api_key="fake")

    # Antes de B-1, "07/2026" quedaba sin interpretar (periodo_desde=None):
    # la factura validaba bien y quedaba "aprobada" e invisible. Ahora se
    # normaliza: periodo_desde al PRIMER día del mes de inicio (2026-07-01),
    # periodo_hasta al ÚLTIMO día del mes de cierre (2026-08-31, hallazgo
    # C-1) -- si los dos fueran el primer día, alertas_por_periodo_faltante
    # esperaría el próximo bimestre al día siguiente de 2026-07-01.
    assert resultado.estado == "guardada"
    fila = con.execute(
        "SELECT periodo_desde, periodo_hasta, estado FROM facturas WHERE hash_pdf = ?",
        [resultado.hash_pdf],
    ).fetchone()
    assert fila == ("2026-07-01", "2026-08-31", "aprobada")
    con.close()


def test_factura_sin_servicio_no_queda_aprobada(tmp_path, monkeypatch):
    factura_sin_servicio = replace(_factura_telefonia_julio(), servicio=None)
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura_sin_servicio)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "necesita_datos"
    assert "servicio" in resultado.detalle
    con.close()


def test_factura_rota_va_a_cuarentena(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_rota())
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "rota_importe_no_cierra.pdf", con, api_key="fake")

    assert resultado.estado == "cuarentena"
    assert "línea" in resultado.detalle
    en_facturas = con.execute(
        "SELECT 1 FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert en_facturas is None
    fila_cuarentena = con.execute(
        "SELECT emisor, servicio FROM cuarentena WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    # Aunque la factura no haya validado aritméticamente, la extracción sí
    # pudo leer emisor y servicio -- se guardan para poder calcular métricas
    # de calidad de lectura por proveedor (core.almacenamiento.metricas_por_proveedor).
    assert fila_cuarentena == ("Comunicaciones Sur S.A.", "telefonia")
    con.close()


def test_reprocesar_el_mismo_pdf_no_duplica(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    r1 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    r2 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert r1.estado == "guardada"
    assert r2.estado == "ya_procesada"
    con.close()


def test_rechazar_una_factura_permite_volver_a_subir_el_mismo_pdf(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, A-56, de punta a punta: cargar un
    PDF, rechazar la factura resultante, y volver a subir el MISMO PDF --
    antes de esta corrección, el segundo intento devolvía "ya_procesada"
    para siempre, sin ninguna forma de reprocesarlo."""
    from core.almacenamiento import decision_factura

    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    r1 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert r1.estado == "guardada"

    decision_factura(
        con, hash_pdf=r1.hash_pdf, estado="rechazada", actor="ana", motivo="emisor equivocado"
    )

    r2 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert r2.estado == "guardada"  # se reprocesó, no "ya_procesada"
    estado_final = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [r1.hash_pdf]
    ).fetchone()[0]
    assert estado_final == "aprobada"  # el pipeline la vuelve a guardar aprobada
    con.close()


def test_homologa_conceptos_al_guardar(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    normalizado = con.execute(
        "SELECT concepto_normalizado FROM conceptos WHERE hash_pdf = ? AND orden = 0",
        [resultado.hash_pdf],
    ).fetchone()[0]
    assert normalizado == "abono_movil"
    con.close()


def test_homologacion_se_acota_al_servicio_de_la_factura(tmp_path, monkeypatch):
    # docs/auditoria-2026-09.md, hallazgo A-3: el diccionario que usa
    # procesar_pdf se carga DESPUÉS de la extracción, acotado al
    # factura.servicio -- confirmamos que un concepto de gas no homologa
    # aunque la factura (por error del modelo) diga "telefonia", porque
    # cargar_diccionario("telefonia") ni siquiera trae consumo_gas.
    factura = _factura_telefonia_julio()
    factura.conceptos = [Concepto("Consumo de gas natural m3", 50, "m3", 100.0, 5000.0)]
    factura.subtotal = 5000.0
    factura.total = 6050.0
    factura.impuestos = [Impuesto("IVA 21%", importe=1050.0)]

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura)
    monkeypatch.setattr(pipeline_mod, "total_impreso", lambda texto: None)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert resultado.estado == "guardada"

    fila = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos "
        "WHERE hash_pdf = ? AND orden = 0",
        [resultado.hash_pdf],
    ).fetchone()
    assert fila[0] is None  # sin clasificar -- correcto, "consumo_gas" no está en telefonia
    # El score se persiste IGUAL, aunque no haya homologado -- es el dato
    # que permite calibrar (¿le faltó poco? ¿es un concepto nuevo de
    # verdad?), ver core/almacenamiento.py::guardar_factura.
    assert fila[1] is not None
    con.close()


def test_score_homologacion_se_persiste_para_concepto_homologado(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    filas = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos "
        "WHERE hash_pdf = ? ORDER BY orden",
        [resultado.hash_pdf],
    ).fetchall()
    # Scores con el diccionario real (Bloque 4 agregó el alias "abono
    # lineas moviles" a telefonia.yaml, que sube este score respecto de
    # antes de esa calibración).
    assert filas[0] == ("abono_movil", pytest.approx(0.95))
    assert filas[1] == ("consumo_datos", pytest.approx(0.7567567567567568))
    con.close()


def test_item_duplicado_se_persiste_al_procesar(tmp_path, monkeypatch):
    # docs/auditoria-2026-09.md, hallazgo A-6: antes, alertas_por_item_duplicado
    # nunca se ejecutaba en el flujo real -- se invocaba en evolucion.py
    # sobre una factura agregada sin conceptos, así que siempre daba [].
    factura = _factura_telefonia_julio()
    factura.conceptos = [
        Concepto("Abono", 1, None, 1000.0, 1000.0),
        Concepto("Abono", 1, None, 1000.0, 1000.0),
    ]
    factura.subtotal = 2000.0
    factura.total = 2420.0  # 2000 + IVA 21% (420)
    factura.impuestos = [Impuesto("IVA 21%", importe=420.0)]

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura)
    # El PDF de la fixture imprime un total distinto al de esta factura
    # sintética (no viene al caso para este test, que prueba item_duplicado
    # -- no la doble lectura, ya cubierta en tests/extraccion/test_validacion.py).
    monkeypatch.setattr(pipeline_mod, "total_impreso", lambda texto: None)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert resultado.estado == "guardada"

    fila = con.execute(
        "SELECT tipo, severidad FROM alertas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila == ("item_duplicado", "media")
    con.close()


def test_item_duplicado_se_convierte_en_caso_sin_pasar_por_revision(tmp_path, monkeypatch):
    """Con revision_humana_obligatoria en false (default), la factura queda
    aprobada directo en guardar_factura, SIN pasar nunca por
    decision_factura -- que es el único lugar donde antes se sincronizaban
    los casos de alerta. Sin el llamado agregado en procesar_pdf, un ítem
    duplicado real nunca se convertía en un caso operativo visible en la
    página Casos."""
    from core.almacenamiento import listar_casos_alerta

    factura = _factura_telefonia_julio()
    factura.conceptos = [
        Concepto("Abono", 1, None, 1000.0, 1000.0),
        Concepto("Abono", 1, None, 1000.0, 1000.0),
    ]
    factura.subtotal = 2000.0
    factura.total = 2420.0
    factura.impuestos = [Impuesto("IVA 21%", importe=420.0)]

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura)
    monkeypatch.setattr(pipeline_mod, "total_impreso", lambda texto: None)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert resultado.estado == "guardada"

    casos = listar_casos_alerta(con)
    assert len(casos) == 1
    assert casos[0][1] == "item_duplicado"  # (clave, tipo, severidad, ...)
    con.close()


def test_pdf_corrupto_no_tumba_el_procesamiento(tmp_path, monkeypatch):
    # docs/auditoria-2026-09.md, hallazgo A-18: antes solo se atrapaba
    # PdfSinTextoError -- cualquier otra excepción al leer el PDF (acá
    # simulada) tumbaba todo el pipeline en vez de reportarse como
    # error_extraccion, como cualquier otro PDF ilegible.
    def _romper(*a, **k):
        raise ValueError("PDF con estructura inválida")

    monkeypatch.setattr(pipeline_mod, "extraer_texto", _romper)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "error_extraccion"
    assert "ilegible" in resultado.detalle
    con.close()


def test_tope_de_llamadas_por_hora_se_hace_cumplir(tmp_path, monkeypatch):
    # docs/auditoria-2026-09.md, hallazgo A-7: el tope estaba declarado y
    # nunca se usaba. docs/auditoria-2026-09-piloto.md, B-4: ahora vive en
    # data/operacion.yaml (core.operacion.max_llamadas_gemini_por_hora),
    # no hardcodeado.
    monkeypatch.setattr(pipeline_mod, "max_llamadas_gemini_por_hora", lambda: 0)
    llamado = False

    def _no_deberia_llamarse(*a, **k):
        nonlocal llamado
        llamado = True
        return _factura_telefonia_julio()

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _no_deberia_llamarse)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "error_extraccion"
    assert "tope" in resultado.detalle
    assert not llamado  # ni siquiera se intentó llamar a Gemini
    con.close()


def test_tope_de_llamadas_dice_a_que_hora_reintentar(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, B-4: el mensaje dice cuándo
    reintentar, no solo que se alcanzó el tope."""
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")
    # Agota el tope real (1) con una llamada real, sin pasar por el pipeline
    # -- para no depender de dos PDFs de fixtures distintos.
    from core.almacenamiento import registrar_intento_gemini

    registrar_intento_gemini(con, hash_pdf="otro", ruta_pdf="otro.pdf", exito=True)
    monkeypatch.setattr(pipeline_mod, "max_llamadas_gemini_por_hora", lambda: 1)

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "error_extraccion"
    assert "tope" in resultado.detalle
    assert "después de las" in resultado.detalle
    con.close()


def test_intento_fallido_de_extraccion_queda_registrado(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, hallazgo B-5: antes un fallo de
    extracción no dejaba NINGÚN rastro en la base -- se perdía al recargar
    la página."""
    from core.extraccion.gemini import ExtraccionError

    def _falla(*a, **k):
        raise ExtraccionError("Error llamando a Gemini: 503 Service Unavailable")

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falla)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "error_extraccion"
    from core.almacenamiento import intentos_gemini_fallidos_recientes

    fallidos = intentos_gemini_fallidos_recientes(con)
    assert len(fallidos) == 1
    ruta_pdf, mensaje, respuesta_cruda, _creado_en = fallidos[0]
    assert "503" in mensaje
    con.close()


def test_intento_exitoso_tambien_se_registra_y_cuenta_para_el_tope(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    from core.almacenamiento import llamadas_ultima_hora

    assert llamadas_ultima_hora(con) == 1
    con.close()


def test_sin_item_duplicado_no_guarda_alertas(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    filas = con.execute("SELECT * FROM alertas WHERE hash_pdf = ?", [resultado.hash_pdf]).fetchall()
    assert filas == []
    con.close()
