"""Test del pipeline de dos pasos (`procesar_pdf` deja un borrador,
`confirmar_factura` lo guarda como definitivo) contra las fixtures
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
from core.pipeline import confirmar_factura, procesar_pdf

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


# --- procesar_pdf: SIEMPRE deja un borrador, nunca decide sola --------------
# docs/auditoria-2026-09-facturas-reales-2.md (plan de confirmación de
# carga): antes, una factura que no cerraba terminaba en uno de tres
# callejones sin salida (cuarentena, "necesita_datos", o un mensaje que se
# perdía). Ahora el pipeline SIEMPRE deja un borrador -- la calidad de la
# extracción (válida, con la aritmética rota, o sin período/servicio) no
# cambia el resultado de procesar_pdf, solo lo que hay para confirmar
# después en apps/segurplus/paginas/confirmar.py.


def test_factura_valida_queda_como_borrador(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    fila = con.execute(
        "SELECT emisor, total, estado FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila == ("Comunicaciones Sur S.A.", 12584.0, "borrador")
    con.close()


def test_factura_sin_periodo_tambien_queda_como_borrador(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo B-1, reproducido
    con facturas reales de la Usina Popular de Tandil: el modelo puede leer
    perfectamente el período impreso y aun así no devolverlo en un formato
    interpretable. Antes de la pantalla de confirmación, esto se resolvía
    con un estado intermedio ("necesita_datos") -- ahora es un borrador
    más: falta período, así que no se va a poder confirmar todavía, pero
    procesar_pdf no tiene por qué saberlo."""
    factura_sin_periodo = replace(_factura_telefonia_julio(), periodo_desde=None)
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura_sin_periodo)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    fila_estado = con.execute(
        "SELECT estado, periodo_desde FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila_estado == ("borrador", None)
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
        # IVA 27% (docs/auditoria-2026-09-facturas-reales.md, hallazgo C-15:
        # gas real lleva 27%, no el 21% genérico de las otras fixtures) --
        # 5.352,00 * 0,27 = 1.445,04.
        "impuestos": [{"nombre": "IVA 27%", "importe": 1445.04}],
        "subtotal": 5352.0,
        "total": 6797.04,
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
    assert resultado.estado == "borrador"
    fila = con.execute(
        "SELECT periodo_desde, periodo_hasta, estado FROM facturas WHERE hash_pdf = ?",
        [resultado.hash_pdf],
    ).fetchone()
    assert fila == ("2026-07-01", "2026-08-31", "borrador")
    con.close()


def test_factura_sin_servicio_tambien_queda_como_borrador(tmp_path, monkeypatch):
    factura_sin_servicio = replace(_factura_telefonia_julio(), servicio=None)
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura_sin_servicio)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    con.close()


def test_factura_rota_tambien_queda_como_borrador_no_en_cuarentena(tmp_path, monkeypatch):
    """La aritmética rota YA NO manda la factura a la tabla `cuarentena`
    (que dejó de recibir escrituras nuevas, ver
    apps/segurplus/paginas/cuarentena.py) -- queda como cualquier otro
    borrador, con el importe que no cierra tal cual lo devolvió Gemini,
    para corregir con el PDF a la vista en la pantalla de confirmación."""
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_rota())
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "rota_importe_no_cierra.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    en_facturas = con.execute(
        "SELECT estado FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert en_facturas == ("borrador",)
    en_cuarentena = con.execute(
        "SELECT 1 FROM cuarentena WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert en_cuarentena is None
    con.close()


def test_reprocesar_el_mismo_pdf_no_duplica(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    r1 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    r2 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert r1.estado == "borrador"
    assert r2.estado == "ya_procesada"  # el borrador ya existe, no se pisa
    con.close()


def test_pdf_corrupto_no_tumba_el_procesamiento(tmp_path, monkeypatch):
    # docs/auditoria-2026-09.md, hallazgo A-18: antes solo se atrapaba
    # PdfSinTextoError -- cualquier otra excepción al leer el PDF (acá
    # simulada) tumbaba todo el pipeline en vez de reportarse como
    # error_extraccion, como cualquier otro PDF ilegible. Este caso SIGUE
    # dando error_extraccion (no hay ni texto para mostrar en un borrador).
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
    # nunca se usaba. docs/auditoria-2026-09-facturas-reales.md, B-4: ahora
    # vive en data/operacion.yaml (core.operacion.max_llamadas_gemini_por_hora),
    # no hardcodeado. Sin llegar a llamar a Gemini, no hay nada que dejar
    # como borrador -- sigue siendo error_extraccion.
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
    """docs/auditoria-2026-09-facturas-reales.md, B-4: el mensaje dice cuándo
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


def test_intento_fallido_de_extraccion_deja_un_borrador_vacio(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo B-5, extendido
    por el plan de confirmación: antes un fallo de extracción no dejaba
    NINGÚN rastro en la base. Ahora deja DOS cosas -- el registro en
    intentos_gemini de siempre, Y un borrador vacío (con el PDF, si se pudo
    guardar) para completar a mano en vez de perder la factura por
    completo."""
    from core.extraccion.gemini import ExtraccionError

    def _falla(*a, **k):
        raise ExtraccionError("Error llamando a Gemini: 503 Service Unavailable")

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falla)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    fila = con.execute(
        "SELECT estado, emisor, motivo_carga FROM facturas WHERE hash_pdf = ?",
        [resultado.hash_pdf],
    ).fetchone()
    assert fila[0] == "borrador"
    assert fila[1] is None  # nada que extraer -- vacío para completar a mano
    assert "503" in fila[2]

    from core.almacenamiento import intentos_gemini_fallidos_recientes

    fallidos = intentos_gemini_fallidos_recientes(con)
    assert len(fallidos) == 1
    ruta_pdf, mensaje, respuesta_cruda, _creado_en = fallidos[0]
    assert "503" in mensaje
    con.close()


def test_json_valido_pero_incompleto_deja_borrador_y_cuenta_para_el_tope(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-2: un JSON
    sintácticamente válido pero con un campo faltante (el caso real:
    Gemini omite "descripcion" en un concepto) tiene que comportarse igual
    que cualquier otro ExtraccionError -- contar para el tope (B-4) y
    quedar registrado con el JSON crudo (B-5), y ahora además dejar un
    borrador vacío en vez de perderse."""
    from core.extraccion.gemini import ExtraccionError

    json_crudo = '{"conceptos": [{"cantidad": 1, "precio_unitario": 10.0, "importe": 10.0}]}'

    def _json_incompleto(*a, **k):
        raise ExtraccionError(
            "El JSON de Gemini no tiene la forma esperada: 'descripcion'",
            respuesta_cruda=json_crudo,
        )

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _json_incompleto)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    from core.almacenamiento import intentos_gemini_fallidos_recientes, llamadas_ultima_hora

    assert llamadas_ultima_hora(con) == 1
    fallidos = intentos_gemini_fallidos_recientes(con)
    assert len(fallidos) == 1
    _ruta_pdf, mensaje, respuesta_cruda, _creado_en = fallidos[0]
    assert "descripcion" in mensaje
    assert respuesta_cruda == json_crudo
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


def test_intento_exitoso_no_duplica_la_respuesta_cruda(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-facturas-reales.md, hallazgo C-10: esa misma
    cadena ya va a facturas.respuesta_extraida -- guardarla también en
    intentos_gemini duplica los datos completos de cada factura sin
    ninguna necesidad (el diagnóstico de B-5 solo usa los fallidos)."""
    factura_con_respuesta = replace(
        _factura_telefonia_julio(), respuesta_extraida='{"total": 12584.0}'
    )
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura_con_respuesta)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    respuesta_cruda = con.execute(
        "SELECT respuesta_cruda FROM intentos_gemini WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()[0]
    assert respuesta_cruda is None
    con.close()


def test_borrador_guarda_el_texto_extraido(tmp_path, monkeypatch):
    """El texto que ya sacó `core.ingesta.pdf_texto.extraer_texto` se
    persiste en `facturas.texto_extraido` -- la pantalla de confirmación lo
    reusa (doble lectura del total, respaldo visual si no hay PDF) sin
    tener que volver a leer el archivo."""
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    texto = con.execute(
        "SELECT texto_extraido FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()[0]
    assert "Comunicaciones Sur" in texto
    con.close()


# --- confirmar_factura: la última puerta antes de que algo impacte --------


def test_confirmar_guarda_aprobada_por_defecto(tmp_path, monkeypatch):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    estado = confirmar_factura(con, factura)

    assert estado == "aprobada"
    fila = con.execute("SELECT estado FROM facturas WHERE hash_pdf = 'h1'").fetchone()
    assert fila == ("aprobada",)
    con.close()


def test_confirmar_con_revision_obligatoria_queda_pendiente(tmp_path, monkeypatch):
    monkeypatch.setattr("core.pipeline.revision_humana_obligatoria", lambda: True)
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    estado = confirmar_factura(con, factura)

    assert estado == "requiere_revision"
    con.close()


def test_confirmar_sin_periodo_no_se_puede(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = replace(_factura_telefonia_julio(), periodo_desde=None)
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    with pytest.raises(ValueError, match="período y servicio"):
        confirmar_factura(con, factura)
    con.close()


def test_confirmar_sin_servicio_no_se_puede(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = replace(_factura_telefonia_julio(), servicio=None)
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    with pytest.raises(ValueError, match="período y servicio"):
        confirmar_factura(con, factura)
    con.close()


def test_confirmar_factura_que_no_cierra_no_se_puede(tmp_path):
    """La aritmética rota que antes mandaba a cuarentena ahora bloquea la
    CONFIRMACIÓN, no la carga -- el borrador se puede seguir editando en
    pantalla hasta que cierre."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_rota()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    with pytest.raises(ValueError, match="no cierra aritméticamente"):
        confirmar_factura(con, factura)
    con.close()


def test_confirmar_re_homologa_con_la_descripcion_corregida(tmp_path):
    """El detalle de cálculo pegado a la descripción ("Cargo Fijo (414,4500
    / 30.5 x 8)") hunde el score de homologación -- si el usuario lo
    corrige a mano en la pantalla de confirmación, la homologación tiene
    que correr sobre lo CORREGIDO, no sobre lo que devolvió Gemini."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.conceptos = [
        Concepto("Cargo Fijo", 1, "mes", 10400.0, 10400.0),  # ya corregido a mano
    ]
    factura.impuestos = [Impuesto("IVA 21%", importe=2184.0)]
    factura.subtotal = 10400.0
    factura.total = 12584.0

    confirmar_factura(con, factura)

    normalizado = con.execute(
        "SELECT concepto_normalizado FROM conceptos WHERE hash_pdf = 'h1' AND orden = 0"
    ).fetchone()[0]
    assert normalizado == "cargo_fijo"
    con.close()


def test_confirmar_persiste_score_de_homologacion(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura)

    filas = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos "
        "WHERE hash_pdf = 'h1' ORDER BY orden"
    ).fetchall()
    assert filas[0][0] == "abono_movil"
    assert filas[0][1] is not None
    con.close()


def test_confirmar_acota_la_homologacion_al_servicio_de_la_factura(tmp_path):
    # docs/auditoria-2026-09.md, hallazgo A-3: un concepto de gas no debe
    # homologar aunque la factura (por error del modelo) diga "telefonia",
    # porque cargar_diccionario("telefonia") ni siquiera trae consumo_gas.
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.conceptos = [Concepto("Consumo de gas natural m3", 50, "m3", 100.0, 5000.0)]
    factura.subtotal = 5000.0
    factura.total = 6050.0
    factura.impuestos = [Impuesto("IVA 21%", importe=1050.0)]

    confirmar_factura(con, factura)

    fila = con.execute(
        "SELECT concepto_normalizado, score_homologacion FROM conceptos WHERE hash_pdf = 'h1'"
    ).fetchone()
    assert fila[0] is None  # sin clasificar -- correcto, "consumo_gas" no está en telefonia
    assert fila[1] is not None  # el score se persiste igual, para calibrar
    con.close()


def test_confirmar_guarda_item_duplicado_como_alerta_y_caso(tmp_path):
    # docs/auditoria-2026-09.md, hallazgo A-6: alertas_por_item_duplicado
    # tiene que correr sobre la factura individual con sus conceptos, y
    # convertirse en un caso operativo aunque la factura quede aprobada
    # directo (sin pasar nunca por decision_factura).
    from core.almacenamiento import listar_casos_alerta

    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.conceptos = [
        Concepto("Abono", 1, None, 1000.0, 1000.0),
        Concepto("Abono", 1, None, 1000.0, 1000.0),
    ]
    factura.subtotal = 2000.0
    factura.total = 2420.0
    factura.impuestos = [Impuesto("IVA 21%", importe=420.0)]

    confirmar_factura(con, factura)

    fila = con.execute("SELECT tipo, severidad FROM alertas WHERE hash_pdf = 'h1'").fetchone()
    assert fila == ("item_duplicado", "media")
    casos = listar_casos_alerta(con)
    assert len(casos) == 1
    assert casos[0][1] == "item_duplicado"
    con.close()


def test_confirmar_sin_item_duplicado_no_guarda_alertas(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura)

    filas = con.execute("SELECT * FROM alertas WHERE hash_pdf = 'h1'").fetchall()
    assert filas == []
    con.close()


# --- De punta a punta: procesar_pdf (borrador) -> confirmar_factura -------


def test_de_punta_a_punta_borrador_a_aprobada(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert resultado.estado == "borrador"

    factura = replace(
        _factura_telefonia_julio(), hash_pdf=resultado.hash_pdf, ruta_pdf=str(resultado.ruta)
    )
    estado = confirmar_factura(con, factura)

    assert estado == "aprobada"
    from core.almacenamiento import totales_por_periodo

    assert totales_por_periodo(con, servicio="telefonia") != {}
    con.close()


def test_rechazar_una_factura_permite_volver_a_subir_el_mismo_pdf(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-piloto.md, A-56, de punta a punta: cargar un
    PDF, confirmarlo, rechazar la factura resultante, y volver a subir el
    MISMO PDF -- antes de esta corrección, el segundo intento devolvía
    "ya_procesada" para siempre, sin ninguna forma de reprocesarlo."""
    from core.almacenamiento import decision_factura

    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    r1 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert r1.estado == "borrador"
    factura = replace(_factura_telefonia_julio(), hash_pdf=r1.hash_pdf, ruta_pdf=str(r1.ruta))
    confirmar_factura(con, factura)

    decision_factura(
        con, hash_pdf=r1.hash_pdf, estado="rechazada", actor="ana", motivo="emisor equivocado"
    )

    r2 = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert r2.estado == "borrador"  # se reprocesó, no "ya_procesada"
    con.close()
