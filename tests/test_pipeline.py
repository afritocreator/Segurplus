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
from core.almacenamiento import conectar, guardar_factura
from core.extraccion.esquema import (
    Concepto,
    FacturaExtraida,
    Impuesto,
    conceptos_desde_filas,
    montos_desde_filas,
)
from core.pipeline import confirmar_factura, procesar_pdf

FIXTURES = Path(__file__).resolve().parent.parent / "docs" / "fixtures" / "sintetico"


def _dejar_como_borrador(con, hash_pdf: str, *, ruta_pdf: str = "/tmp/x.pdf") -> None:
    """Inserta un borrador vacío con este hash -- desde docs/auditoria-2026-
    09-confirmacion.md, D-6, `confirmar_factura` exige que exista un
    borrador en estado 'borrador' con ese hash antes de confirmar (cierra
    la ventana de doble confirmación). El contenido no importa para estos
    tests: lo único que se necesita es que la fila exista."""
    guardar_factura(
        con,
        FacturaExtraida(
            emisor=None,
            cuit=None,
            servicio=None,
            periodo_desde=None,
            periodo_hasta=None,
            fecha_emision=None,
            fecha_vencimiento=None,
            numero_comprobante=None,
            moneda="ARS",
            hash_pdf=hash_pdf,
            ruta_pdf=ruta_pdf,
        ),
        estado="borrador",
    )


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
# Plan de confirmación de carga (docs/estado.md): antes, una factura que
# no cerraba terminaba en uno de tres
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


def test_borrador_es_invisible_para_el_analisis_antes_de_confirmar(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-confirmacion.md, D-19: el invariante central
    que reemplazó a la cuarentena -- "un borrador no aparece en el
    análisis" -- solo estaba probado indirectamente (los tests de punta a
    punta comprueban que aparece DESPUÉS de confirmar, nunca que NO
    aparece ANTES)."""
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert resultado.estado == "borrador"

    from core.almacenamiento import listar_facturas_aprobadas, totales_por_periodo

    assert totales_por_periodo(con, servicio="telefonia") == {}
    assert listar_facturas_aprobadas(con) == []
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
    completo. Error PERMANENTE (no 503/429/timeout) -- un solo intento, sin
    reintentar (ver los tests de reintento más abajo para el caso 503)."""
    from core.extraccion.gemini import ExtraccionError

    def _falla(*a, **k):
        raise ExtraccionError("La respuesta de Gemini no es JSON válido")

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
    assert "JSON" in fila[2]

    from core.almacenamiento import intentos_gemini_fallidos_recientes

    fallidos = intentos_gemini_fallidos_recientes(con)
    assert len(fallidos) == 1
    ruta_pdf, mensaje, respuesta_cruda, _creado_en = fallidos[0]
    assert "JSON" in mensaje
    con.close()


def test_503_reintenta_y_termina_en_exito(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-web.md, E-6: en la única corrida real contra
    Gemini, 503 UNAVAILABLE fue justamente el error que apareció varias
    veces. Dos fallos transitorios seguidos de un éxito -- termina con la
    factura leída, y quedan 3 intentos registrados (2 fallidos + 1
    exitoso), todos contando para el tope de la hora."""
    from core.extraccion.gemini import ExtraccionError

    llamadas = []

    def _falla_dos_veces_despues_exito(*a, **k):
        llamadas.append(1)
        if len(llamadas) <= 2:
            raise ExtraccionError("Error llamando a Gemini: 503 UNAVAILABLE")
        return _factura_telefonia_julio()

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falla_dos_veces_despues_exito)
    monkeypatch.setattr(pipeline_mod.time, "sleep", lambda segundos: None)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    fila = con.execute(
        "SELECT emisor FROM facturas WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila[0] is not None  # se leyó de verdad, no es el borrador vacío

    from core.almacenamiento import llamadas_ultima_hora

    assert len(llamadas) == 3
    assert llamadas_ultima_hora(con) == 3
    con.close()


def test_503_agota_los_reintentos_y_deja_borrador_vacio(tmp_path, monkeypatch):
    """Mismo error transitorio en TODOS los intentos -- se rinde después de
    intentos_gemini_por_llamada() intentos (3 por default) y deja el
    borrador vacío de siempre, no se queda reintentando para siempre."""
    from core.extraccion.gemini import ExtraccionError

    def _siempre_503(*a, **k):
        raise ExtraccionError("Error llamando a Gemini: 503 UNAVAILABLE")

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _siempre_503)
    monkeypatch.setattr(pipeline_mod.time, "sleep", lambda segundos: None)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    assert "503" in resultado.detalle

    from core.almacenamiento import intentos_gemini_fallidos_recientes, llamadas_ultima_hora

    assert len(intentos_gemini_fallidos_recientes(con)) == 3
    assert llamadas_ultima_hora(con) == 3
    con.close()


def test_error_permanente_no_reintenta(tmp_path, monkeypatch):
    """Sin API key (u otro error permanente) no tiene sentido reintentar --
    va a fallar exactamente igual las tres veces. Un solo intento."""
    from core.extraccion.gemini import ExtraccionError

    llamadas = []

    def _sin_clave(*a, **k):
        llamadas.append(1)
        raise ExtraccionError("No hay GEMINI_API_KEY configurada.")

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _sin_clave)
    con = conectar(tmp_path / "test.duckdb")

    procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key=None)

    assert len(llamadas) == 1
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


def test_fallo_al_guardar_evidencia_propaga_el_detalle(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-confirmacion.md, D-8: Gemini SÍ pudo leer la
    factura, pero el PDF no se pudo guardar como evidencia (disco lleno,
    S3 caído) -- antes `ResultadoPipeline.detalle` quedaba vacío en este
    caso, así que `cargar.py` no podía avisar de qué se trataba."""
    monkeypatch.setattr(
        pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_telefonia_julio()
    )
    monkeypatch.setattr(
        pipeline_mod, "guardar_pdf", lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno"))
    )
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")

    assert resultado.estado == "borrador"
    assert "disco lleno" in resultado.detalle
    con.close()


# --- confirmar_factura: la última puerta antes de que algo impacte --------


def test_confirmar_guarda_aprobada_por_defecto(tmp_path, monkeypatch):
    con = conectar(tmp_path / "test.duckdb")
    _dejar_como_borrador(con, "h1")
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
    _dejar_como_borrador(con, "h1")
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
    _dejar_como_borrador(con, "h1")
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
    _dejar_como_borrador(con, "h1")
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
    _dejar_como_borrador(con, "h1")
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
    _dejar_como_borrador(con, "h1")
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


def test_confirmar_usa_concepto_sugerido_cuando_dice_no_homologa(tmp_path):
    """docs/auditoria-2026-09-web.md, E-19: si Dice no encuentra nada pero
    el modelo ya había sugerido un concepto al extraer, y esa sugerencia
    pertenece al diccionario DEL SERVICIO de la factura, se usa de
    respaldo -- con un motivo distinto, para que quede claro que no vino
    de Dice."""
    con = conectar(tmp_path / "test.duckdb")
    _dejar_como_borrador(con, "h1")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.conceptos = [
        Concepto(
            "Descripción rarísima sin ningún alias parecido",
            1,
            None,
            10400.0,
            10400.0,
            concepto_sugerido="servicio_telefonia",
        )
    ]

    confirmar_factura(con, factura)

    fila = con.execute(
        "SELECT concepto_normalizado, motivo_homologacion FROM conceptos WHERE hash_pdf = 'h1'"
    ).fetchone()
    assert fila == ("servicio_telefonia", "sugerido_por_modelo")
    con.close()


def test_confirmar_descarta_concepto_sugerido_de_otro_servicio(tmp_path):
    """Una sugerencia del modelo que pertenece a OTRO servicio (acá,
    "consumo_gas" en una factura de telefonía) no se usa -- mismo criterio
    que ya aplicaba Dice (A-3): el diccionario se acota por servicio."""
    con = conectar(tmp_path / "test.duckdb")
    _dejar_como_borrador(con, "h1")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.conceptos = [
        Concepto(
            "Descripción rarísima sin ningún alias parecido",
            1,
            None,
            10400.0,
            10400.0,
            concepto_sugerido="consumo_gas",
        )
    ]

    confirmar_factura(con, factura)

    fila = con.execute(
        "SELECT concepto_normalizado, motivo_homologacion FROM conceptos WHERE hash_pdf = 'h1'"
    ).fetchone()
    assert fila == (None, None)
    con.close()


def test_confirmar_guarda_item_duplicado_como_alerta_y_caso(tmp_path):
    # docs/auditoria-2026-09.md, hallazgo A-6: alertas_por_item_duplicado
    # tiene que correr sobre la factura individual con sus conceptos, y
    # convertirse en un caso operativo aunque la factura quede aprobada
    # directo (sin pasar nunca por decision_factura).
    from core.almacenamiento import listar_casos_alerta

    con = conectar(tmp_path / "test.duckdb")
    _dejar_como_borrador(con, "h1")
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
    _dejar_como_borrador(con, "h1")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura)

    filas = con.execute("SELECT * FROM alertas WHERE hash_pdf = 'h1'").fetchall()
    assert filas == []
    con.close()


# --- confirmar_factura como autoridad (docs/auditoria-2026-09-confirmacion.md,
# --- D-5, D-6, D-7, D-22) -------------------------------------------------


def test_confirmar_sin_borrador_previo_no_se_puede(tmp_path):
    """D-6: sin un borrador con ese hash, no hay nada que confirmar --
    antes esto simplemente insertaba una factura 'aprobada' de la nada."""
    con = conectar(tmp_path / "test.duckdb")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    with pytest.raises(ValueError, match="borrador"):
        confirmar_factura(con, factura)
    con.close()


def test_confirmar_dos_veces_la_misma_factura_no_se_puede(tmp_path):
    """D-6: una vez aprobada, ya no es un 'borrador' -- confirmarla de
    nuevo (una pestaña vieja, una carrera con 'Revisar facturas') debe
    rechazarse en vez de pisar en silencio lo ya confirmado."""
    con = conectar(tmp_path / "test.duckdb")
    _dejar_como_borrador(con, "h1")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    assert confirmar_factura(con, factura) == "aprobada"
    with pytest.raises(ValueError, match="borrador"):
        confirmar_factura(con, factura)
    con.close()


def test_confirmar_preserva_texto_extraido_y_motivo_carga(tmp_path):
    """D-5: antes, confirmar borraba estas dos columnas porque no se las
    pasaba a guardar_factura -- se perdía el respaldo del texto del PDF y
    el rastro de que la factura había llegado rota."""
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(
        con,
        replace(_factura_telefonia_julio(), hash_pdf="h1", ruta_pdf="/tmp/x.pdf"),
        estado="borrador",
        texto_extraido="TOTAL A PAGAR $ 12.584,00",
        motivo_carga="No se pudo leer con Gemini: timeout",
    )
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura)

    fila = con.execute(
        "SELECT texto_extraido, motivo_carga FROM facturas WHERE hash_pdf = 'h1'"
    ).fetchone()
    assert fila == ("TOTAL A PAGAR $ 12.584,00", "No se pudo leer con Gemini: timeout")
    con.close()


def test_confirmar_preserva_procedencia_aunque_el_formulario_no_la_traiga(tmp_path):
    """D-7: la procedencia (ruta_evidencia, respuesta_extraida,
    modelo_extraccion, version_prompt, version_esquema) la decide lo que
    hay guardado en el borrador, no lo que arme el llamador -- antes, un
    `FacturaExtraida` armado sin esos campos (como ya hace este mismo
    archivo con `replace(...)` en otros tests) borraba en silencio el
    vínculo con el PDF de evidencia real."""
    con = conectar(tmp_path / "test.duckdb")
    borrador = _factura_telefonia_julio()
    borrador.hash_pdf = "h1"
    borrador.ruta_pdf = "/tmp/x.pdf"
    borrador.ruta_evidencia = "/tmp/evidencia/h1.pdf"
    borrador.modelo_extraccion = "gemini-2.5-flash"
    guardar_factura(con, borrador, estado="borrador")

    # El formulario llega SIN esos campos de procedencia (None por default).
    factura_editada = _factura_telefonia_julio()
    factura_editada.hash_pdf = "h1"
    factura_editada.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura_editada)

    fila = con.execute(
        "SELECT ruta_evidencia, modelo_extraccion FROM facturas WHERE hash_pdf = 'h1'"
    ).fetchone()
    assert fila == ("/tmp/evidencia/h1.pdf", "gemini-2.5-flash")
    con.close()


def test_confirmar_registra_accion_confirmacion_no_carga(tmp_path):
    """D-22: el evento de auditoría de una confirmación tiene que decir
    'confirmacion', no 'carga' -- antes los dos eran indistinguibles."""
    con = conectar(tmp_path / "test.duckdb")
    _dejar_como_borrador(con, "h1")
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura, actor="ana")

    # Filtra "correccion" (D-4: el borrador vacío de _dejar_como_borrador no
    # tiene ningún dato de cabecera, así que TODOS los campos cambian al
    # confirmar -- eso ya lo cubre el Bloque 3, acá solo importa carga/confirmacion).
    acciones = con.execute(
        "SELECT accion, actor FROM decisiones_factura "
        "WHERE hash_pdf = 'h1' AND accion IN ('carga', 'confirmacion') ORDER BY creado_en"
    ).fetchall()
    assert acciones == [("carga", "sistema"), ("confirmacion", "ana")]
    con.close()


# --- D-4: registrar qué corrigió la persona al confirmar ------------------


def test_confirmar_registra_correccion_de_cabecera(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(
        con,
        replace(_factura_telefonia_julio(), hash_pdf="h1", ruta_pdf="/tmp/x.pdf", emisor="???"),
        estado="borrador",
    )
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.emisor = "Comunicaciones Sur S.A."  # corregido a mano en la pantalla

    confirmar_factura(con, factura, actor="ana")

    correccion = con.execute(
        "SELECT campo, valor_anterior, valor_nuevo, actor, motivo "
        "FROM correcciones_factura WHERE hash_pdf = 'h1' AND campo = 'emisor'"
    ).fetchone()
    assert correccion == (
        "emisor",
        "???",
        "Comunicaciones Sur S.A.",
        "ana",
        "corrección al confirmar la carga",
    )
    con.close()


def test_confirmar_sin_cambios_de_cabecera_no_registra_correccion(tmp_path):
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(
        con,
        replace(_factura_telefonia_julio(), hash_pdf="h1", ruta_pdf="/tmp/x.pdf"),
        estado="borrador",
    )
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"

    confirmar_factura(con, factura)

    filas = con.execute("SELECT * FROM correcciones_factura WHERE hash_pdf = 'h1'").fetchall()
    assert filas == []
    con.close()


def test_confirmar_con_linea_editada_deja_la_marca_en_el_motivo(tmp_path):
    """Los conceptos/impuestos/recargos/créditos no tienen tabla de
    corrección propia -- si cambiaron, queda la marca en el motivo del
    evento 'confirmacion'."""
    con = conectar(tmp_path / "test.duckdb")
    guardar_factura(
        con,
        replace(_factura_telefonia_julio(), hash_pdf="h1", ruta_pdf="/tmp/x.pdf"),
        estado="borrador",
    )
    factura = _factura_telefonia_julio()
    factura.hash_pdf = "h1"
    factura.ruta_pdf = "/tmp/x.pdf"
    factura.conceptos[0].importe = 10000.0  # sin cambios reales todavía

    # Corrige de verdad una línea (el precio unitario, no solo el objeto):
    factura.conceptos[0].precio_unitario = 2600.0
    factura.conceptos[0].importe = 10400.0
    factura.subtotal = 10800.0
    factura.impuestos[0].importe = 2268.0
    factura.total = 13068.0

    confirmar_factura(con, factura)

    motivo = con.execute(
        "SELECT motivo FROM decisiones_factura WHERE hash_pdf = 'h1' AND accion = 'confirmacion'"
    ).fetchone()[0]
    assert "líneas de conceptos o montos corregidas" in motivo
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


# --- Escenarios de verificación del plan de confirmación de carga
# --- (docs/estado.md): de punta a punta con las fixtures sintéticas --------


def test_de_punta_a_punta_factura_rota_se_corrige_y_confirma(tmp_path, monkeypatch):
    """El caso que antes iba a cuarentena (5 × $100 = $500, pero la factura
    dice $800): ahora queda como borrador editable. Confirmar SIN corregir
    tiene que rechazarse (la aritmética no cierra); corregir el importe a
    lo que realmente da la cuenta lo destraba, y la factura termina
    visible en el análisis (`totales_por_periodo`, lo que usa Evolución)."""
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_rota())
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "rota_importe_no_cierra.pdf", con, api_key="fake")
    assert resultado.estado == "borrador"

    # Sin corregir: confirmar tiene que rechazarse, no colarse al análisis.
    con_el_dato_roto = replace(
        _factura_rota(), hash_pdf=resultado.hash_pdf, ruta_pdf=str(resultado.ruta)
    )
    with pytest.raises(ValueError, match="no cierra aritméticamente"):
        confirmar_factura(con, con_el_dato_roto)

    # Corregido a mano en la pantalla (5 × $100 = $500, no $800):
    corregida = replace(
        con_el_dato_roto,
        conceptos=[Concepto("Abono 5 líneas móviles", 5, "línea", 100.0, 500.0)],
        subtotal=500.0,
        impuestos=[Impuesto("IVA 21%", importe=105.0)],
        total=605.0,
    )
    estado = confirmar_factura(con, corregida)
    assert estado == "aprobada"

    from core.almacenamiento import totales_por_periodo

    assert totales_por_periodo(con, servicio="telefonia") != {}
    en_cuarentena = con.execute(
        "SELECT 1 FROM cuarentena WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert en_cuarentena is None  # nunca pasó por ahí
    con.close()


def test_de_punta_a_punta_editar_como_lo_haria_el_editor_y_confirmar(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-confirmacion.md, D-20: a diferencia del test
    de arriba (que arma la factura corregida con `Concepto(...)` directo),
    este pasa por `conceptos_desde_filas`/`montos_desde_filas` con filas en
    la forma EXACTA que deja `st.data_editor` en `confirmar.py`
    (`list[dict]`, con una fila vacía de más como agrega el editor
    dinámico) -- exactamente el camino donde vivía D-1 (una fila vacía que
    colaba un concepto llamado "nan")."""
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: _factura_rota())
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "rota_importe_no_cierra.pdf", con, api_key="fake")
    assert resultado.estado == "borrador"

    filas_conceptos_del_editor = [
        {
            "descripcion": "Abono 5 líneas móviles",
            "cantidad": 5.0,
            "unidad": "línea",
            "precio_unitario": 100.0,
            "importe": 500.0,  # corregido: antes decía 800.0
        },
        # Fila extra vacía, como deja un editor dinámico sin usar:
        {
            "descripcion": float("nan"),
            "cantidad": float("nan"),
            "unidad": None,
            "precio_unitario": float("nan"),
            "importe": float("nan"),
        },
    ]
    filas_impuestos_del_editor = [{"nombre": "IVA 21%", "importe": 105.0}]

    corregida = replace(
        _factura_rota(),
        hash_pdf=resultado.hash_pdf,
        ruta_pdf=str(resultado.ruta),
        conceptos=conceptos_desde_filas(filas_conceptos_del_editor),
        impuestos=montos_desde_filas(filas_impuestos_del_editor, Impuesto),
        subtotal=500.0,
        total=605.0,
    )

    estado = confirmar_factura(con, corregida)

    assert estado == "aprobada"
    fila = con.execute(
        "SELECT count(*), sum(importe) FROM conceptos WHERE hash_pdf = ?", [resultado.hash_pdf]
    ).fetchone()
    assert fila == (1, 500.0)  # UN concepto, con el importe CORREGIDO -- no el original roto
    con.close()


def test_de_punta_a_punta_factura_sin_periodo_se_completa_a_mano_y_confirma(tmp_path, monkeypatch):
    """docs/auditoria-2026-09-facturas-reales.md, B-1: el caso real que
    bloqueaba al usuario -- una factura de luz que el modelo lee bien pero
    sin devolver un período interpretable. Antes tenía su propio estado
    intermedio ("necesita_datos"); ahora es un borrador más, que queda
    confirmable en cuanto se completa el período a mano (como si se
    escribiera "07/2022" en el campo de la pantalla, que ya se normaliza
    al confirmar)."""
    factura_sin_periodo = replace(_factura_telefonia_julio(), periodo_desde=None)
    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", lambda *a, **k: factura_sin_periodo)
    con = conectar(tmp_path / "test.duckdb")

    resultado = procesar_pdf(FIXTURES / "telefonia_2026-07.pdf", con, api_key="fake")
    assert resultado.estado == "borrador"

    sin_periodo = replace(
        factura_sin_periodo, hash_pdf=resultado.hash_pdf, ruta_pdf=str(resultado.ruta)
    )
    with pytest.raises(ValueError, match="período y servicio"):
        confirmar_factura(con, sin_periodo)

    # Completado a mano -- "07/2022" tal como lo escribiría el usuario ya
    # llega acá normalizado (_normalizar_fecha, el mismo que usa la
    # pantalla en _fecha_editable).
    completada = replace(sin_periodo, periodo_desde="2022-07-01")
    estado = confirmar_factura(con, completada)
    assert estado == "aprobada"
    con.close()
