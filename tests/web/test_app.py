"""Tests de web/app.py de punta a punta con `TestClient` -- a diferencia de
`AppTest` de Streamlit (que no puede simular subir un archivo real, ver
docstring de tests/apps/test_cargar_app.py), `TestClient` SÍ puede mandar
un upload multipart real, así que acá se prueba el flujo completo
subir → revisar → confirmar → aparece en Ver, no solo el renderizado."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import core.almacenamiento as almacenamiento_mod
import core.pipeline as pipeline_mod
from core.extraccion.esquema import Concepto, FacturaExtraida, Impuesto, Recargo
from core.pipeline import confirmar_factura
from web.app import app

_FIXTURE_ENERGIA = (
    Path(__file__).resolve().parents[2] / "docs" / "fixtures" / "sintetico" / "energia_2026-07.pdf"
)


@pytest.fixture(autouse=True)
def _base_de_prueba(tmp_path, monkeypatch):
    monkeypatch.setattr(almacenamiento_mod, "RUTA_BASE", tmp_path / "test.duckdb")
    monkeypatch.setenv("APP_PASSWORD", "clave-de-test")
    monkeypatch.setenv("SECRET_KEY", "clave-de-firma-test")


@pytest.fixture
def cliente_logueado():
    cliente = TestClient(app)
    cliente.post("/login", data={"contrasena": "clave-de-test"})
    return cliente


def test_ruta_protegida_sin_sesion_redirige_a_login():
    cliente = TestClient(app)
    r = cliente.get("/subir", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_incorrecto_muestra_error():
    cliente = TestClient(app)
    r = cliente.post("/login", data={"contrasena": "mal"})
    assert "Contraseña incorrecta" in r.text


def test_login_correcto_da_acceso(cliente_logueado):
    r = cliente_logueado.get("/subir")
    assert r.status_code == 200
    assert "Subir facturas" in r.text


def test_logout_saca_el_acceso(cliente_logueado):
    cliente_logueado.post("/logout")
    r = cliente_logueado.get("/subir", follow_redirects=False)
    assert r.status_code == 303


def test_revisar_sin_borradores_muestra_mensaje(cliente_logueado):
    r = cliente_logueado.get("/revisar")
    assert "No hay facturas esperando confirmación" in r.text


def test_ver_sin_facturas_aprobadas_muestra_mensaje(cliente_logueado):
    r = cliente_logueado.get("/ver")
    assert "Todavía no hay facturas cargadas" in r.text


def test_subir_sin_api_key_deshabilita_boton(cliente_logueado, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = cliente_logueado.get("/subir")
    assert "disabled" in r.text


def test_subir_con_solo_groq_api_key_sigue_deshabilitado(cliente_logueado, monkeypatch):
    """docs/auditoria-2026-09-web.md, E-9: el pipeline real
    (core.pipeline.procesar_pdf) solo usa GEMINI_API_KEY -- con otra clave
    configurada (Groq, o cualquier otra) sola, el botón tiene que seguir
    deshabilitado, porque cada factura fallaría igual."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "clave-de-groq")
    r = cliente_logueado.get("/subir")
    assert "disabled" in r.text


def test_subir_archivo_de_mas_de_10mb_se_rechaza_sin_procesar(cliente_logueado, monkeypatch):
    """docs/auditoria-2026-09-web.md, E-4 del plan de arreglos: un PDF
    escaneado sin tope llenaría el plan gratis de Supabase en pocas
    facturas -- se rechaza antes de intentar leerlo con Gemini."""

    def _no_deberia_llamarse(*a, **k):
        raise AssertionError("no debería intentar leer un archivo rechazado por tamaño")

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _no_deberia_llamarse)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    contenido_grande = b"x" * (10 * 1024 * 1024 + 1)
    r = cliente_logueado.post(
        "/subir",
        files={"archivos": ("gigante.pdf", contenido_grande, "application/pdf")},
    )
    assert r.status_code == 200
    assert "más de 10 MB" in r.text


def test_subir_mas_de_10_archivos_se_rechaza_sin_procesar(cliente_logueado, monkeypatch):
    """docs/auditoria-2026-09-web.md, E-8: subir muchos archivos juntos es
    una sola request de varios minutos, expuesta al corte del proxy --
    mejor pedir subir de a tandas que arriesgar perder el lote entero."""

    def _no_deberia_llamarse(*a, **k):
        raise AssertionError("no debería intentar leer ningún archivo del lote rechazado")

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _no_deberia_llamarse)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    archivos = [(f"archivo{i}.pdf", b"contenido", "application/pdf") for i in range(11)]
    r = cliente_logueado.post(
        "/subir",
        files=[("archivos", (nombre, contenido, tipo)) for nombre, contenido, tipo in archivos],
    )
    assert r.status_code == 200
    assert "el máximo por tanda es 10" in r.text


def test_flujo_completo_subir_revisar_confirmar(cliente_logueado, monkeypatch):
    """El flujo entero: subir un PDF real (mockeando solo la llamada a
    Gemini, no el pipeline), verlo en la lista de Revisar, corregirlo y
    confirmarlo -- y que después aparezca disponible para Ver."""

    def _falso_extraer(pdf_bytes, *, api_key=None, texto_extraido=None):
        return FacturaExtraida(
            emisor="Usina de Prueba",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-07-01",
            periodo_hasta="2026-07-31",
            fecha_emision="2026-08-01",
            fecha_vencimiento=None,
            numero_comprobante="A-1",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 28374.5, 28374.5)],
            subtotal=28374.5,
            total=28374.5,
        )

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falso_extraer)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    with open(_FIXTURE_ENERGIA, "rb") as f:
        r = cliente_logueado.post(
            "/subir", files={"archivos": ("energia.pdf", f, "application/pdf")}
        )
    assert r.status_code == 200
    assert "1" in r.text  # métrica "Listas para confirmar"

    r = cliente_logueado.get("/revisar")
    assert "Usina de Prueba" in r.text

    con = almacenamiento_mod.conectar()
    try:
        hash_pdf = almacenamiento_mod.listar_borradores(con)[0][0]
    finally:
        con.close()

    r = cliente_logueado.get(f"/revisar/{hash_pdf}")
    assert r.status_code == 200
    assert "La aritmética cierra" in r.text

    r = cliente_logueado.post(
        f"/revisar/{hash_pdf}/confirmar",
        data={
            "emisor": "Usina de Prueba",
            "cuit": "30-1",
            "servicio": "energia",
            "moneda": "ARS",
            "periodo_desde": "2026-07-01",
            "periodo_hasta": "2026-07-31",
            "fecha_emision": "2026-08-01",
            "fecha_vencimiento": "",
            "numero_comprobante": "A-1",
            "concepto_descripcion": ["Cargo fijo", "", "", ""],
            "concepto_cantidad": ["1", "", "", ""],
            "concepto_unidad": ["", "", "", ""],
            "concepto_precio_unitario": ["28374.5", "", "", ""],
            "concepto_importe": ["28374.5", "", "", ""],
            "impuesto_nombre": ["", "", ""],
            "impuesto_importe": ["", "", ""],
            "recargo_nombre": ["", "", ""],
            "recargo_importe": ["", "", ""],
            "credito_nombre": ["", "", ""],
            "credito_importe": ["", "", ""],
            "subtotal": "28374.5",
            "total": "28374.5",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/revisar"

    r = cliente_logueado.get("/revisar")
    assert "No hay facturas esperando confirmación" in r.text


def test_confirmar_con_aritmetica_rota_repuebla_el_formulario_con_el_error(
    cliente_logueado, monkeypatch
):
    def _falso_extraer(pdf_bytes, *, api_key=None, texto_extraido=None):
        return FacturaExtraida(
            emisor="Usina de Prueba",
            cuit=None,
            servicio="energia",
            periodo_desde="2026-07-01",
            periodo_hasta="2026-07-31",
            fecha_emision=None,
            fecha_vencimiento=None,
            numero_comprobante=None,
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 100.0, 100.0)],
            subtotal=100.0,
            total=100.0,
        )

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falso_extraer)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    with open(_FIXTURE_ENERGIA, "rb") as f:
        cliente_logueado.post("/subir", files={"archivos": ("energia.pdf", f, "application/pdf")})

    con = almacenamiento_mod.conectar()
    try:
        hash_pdf = almacenamiento_mod.listar_borradores(con)[0][0]
    finally:
        con.close()

    # Cantidad × precio (2 × 100 = 200) no coincide con el importe (100) --
    # tiene que rechazarse, no confirmarse en silencio.
    r = cliente_logueado.post(
        f"/revisar/{hash_pdf}/confirmar",
        data={
            "emisor": "Usina de Prueba",
            "cuit": "",
            "servicio": "energia",
            "moneda": "ARS",
            "periodo_desde": "2026-07-01",
            "periodo_hasta": "2026-07-31",
            "fecha_emision": "",
            "fecha_vencimiento": "",
            "numero_comprobante": "",
            "concepto_descripcion": ["Cargo fijo", "", "", ""],
            "concepto_cantidad": ["2", "", "", ""],
            "concepto_unidad": ["", "", "", ""],
            "concepto_precio_unitario": ["100", "", "", ""],
            "concepto_importe": ["100", "", "", ""],
            "impuesto_nombre": ["", "", ""],
            "impuesto_importe": ["", "", ""],
            "recargo_nombre": ["", "", ""],
            "recargo_importe": ["", "", ""],
            "credito_nombre": ["", "", ""],
            "credito_importe": ["", "", ""],
            "subtotal": "100",
            "total": "100",
        },
    )
    assert r.status_code == 200
    assert "no cierra aritméticamente" in r.text
    # El valor tipeado (2, no el 1 original) tiene que seguir ahí -- no se
    # pierde lo editado por el rechazo.
    assert 'value="2"' in r.text


def test_guardar_sin_confirmar_no_pasa_por_la_validacion(cliente_logueado, monkeypatch):
    def _falso_extraer(pdf_bytes, *, api_key=None, texto_extraido=None):
        return FacturaExtraida(
            emisor=None,
            cuit=None,
            servicio=None,
            periodo_desde=None,
            periodo_hasta=None,
            fecha_emision=None,
            fecha_vencimiento=None,
            numero_comprobante=None,
            moneda="ARS",
            conceptos=[],
        )

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falso_extraer)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    with open(_FIXTURE_ENERGIA, "rb") as f:
        cliente_logueado.post("/subir", files={"archivos": ("energia.pdf", f, "application/pdf")})

    con = almacenamiento_mod.conectar()
    try:
        hash_pdf = almacenamiento_mod.listar_borradores(con)[0][0]
    finally:
        con.close()

    r = cliente_logueado.post(
        f"/revisar/{hash_pdf}/guardar",
        data={
            "emisor": "Corregido a mano",
            "cuit": "",
            "servicio": "",
            "moneda": "ARS",
            "periodo_desde": "",
            "periodo_hasta": "",
            "fecha_emision": "",
            "fecha_vencimiento": "",
            "numero_comprobante": "",
            "concepto_descripcion": [""],
            "concepto_cantidad": [""],
            "concepto_unidad": [""],
            "concepto_precio_unitario": [""],
            "concepto_importe": [""],
            "impuesto_nombre": [""],
            "impuesto_importe": [""],
            "recargo_nombre": [""],
            "recargo_importe": [""],
            "credito_nombre": [""],
            "credito_importe": [""],
            "subtotal": "",
            "total": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/revisar/{hash_pdf}"

    con = almacenamiento_mod.conectar()
    try:
        datos = almacenamiento_mod.leer_borrador(con, hash_pdf)
    finally:
        con.close()
    assert datos["emisor"] == "Corregido a mano"


def test_descartar_borrador_lo_saca_de_la_lista(cliente_logueado, monkeypatch):
    def _falso_extraer(pdf_bytes, *, api_key=None, texto_extraido=None):
        return FacturaExtraida(
            emisor=None,
            cuit=None,
            servicio=None,
            periodo_desde=None,
            periodo_hasta=None,
            fecha_emision=None,
            fecha_vencimiento=None,
            numero_comprobante=None,
            moneda="ARS",
        )

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falso_extraer)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    with open(_FIXTURE_ENERGIA, "rb") as f:
        cliente_logueado.post("/subir", files={"archivos": ("energia.pdf", f, "application/pdf")})

    con = almacenamiento_mod.conectar()
    try:
        hash_pdf = almacenamiento_mod.listar_borradores(con)[0][0]
    finally:
        con.close()

    r = cliente_logueado.post(f"/revisar/{hash_pdf}/descartar", follow_redirects=False)
    assert r.status_code == 303

    r = cliente_logueado.get("/revisar")
    assert "No hay facturas esperando confirmación" in r.text


def test_pdf_sirve_una_factura_ya_confirmada_no_solo_borradores(cliente_logueado, monkeypatch):
    """docs/auditoria-2026-09-web.md, E-4 del plan de arreglos: antes
    `/pdf/{hash}` usaba `leer_borrador`, que rechaza cualquier hash que no
    esté en estado 'borrador' -- una vez confirmada, la factura dejaba de
    tener PDF visible en ningún lado."""
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCIA_DIR", raising=False)

    def _falso_extraer(pdf_bytes, *, api_key=None, texto_extraido=None):
        return FacturaExtraida(
            emisor="Usina de Prueba",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-07-01",
            periodo_hasta="2026-07-31",
            fecha_emision="2026-08-01",
            fecha_vencimiento=None,
            numero_comprobante="A-1",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 28374.5, 28374.5)],
            subtotal=28374.5,
            total=28374.5,
        )

    monkeypatch.setattr(pipeline_mod, "extraer_con_gemini", _falso_extraer)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")

    with open(_FIXTURE_ENERGIA, "rb") as f:
        cliente_logueado.post("/subir", files={"archivos": ("energia.pdf", f, "application/pdf")})

    con = almacenamiento_mod.conectar()
    try:
        hash_pdf = almacenamiento_mod.listar_borradores(con)[0][0]
    finally:
        con.close()

    r_borrador = cliente_logueado.get(f"/pdf/{hash_pdf}")
    assert r_borrador.status_code == 200

    cliente_logueado.post(
        f"/revisar/{hash_pdf}/confirmar",
        data={
            "emisor": "Usina de Prueba",
            "cuit": "30-1",
            "servicio": "energia",
            "moneda": "ARS",
            "periodo_desde": "2026-07-01",
            "periodo_hasta": "2026-07-31",
            "fecha_emision": "2026-08-01",
            "fecha_vencimiento": "",
            "numero_comprobante": "A-1",
            "concepto_descripcion": ["Cargo fijo", "", "", ""],
            "concepto_cantidad": ["1", "", "", ""],
            "concepto_unidad": ["", "", "", ""],
            "concepto_precio_unitario": ["28374.5", "", "", ""],
            "concepto_importe": ["28374.5", "", "", ""],
            "impuesto_nombre": ["", "", ""],
            "impuesto_importe": ["", "", ""],
            "recargo_nombre": ["", "", ""],
            "recargo_importe": ["", "", ""],
            "credito_nombre": ["", "", ""],
            "credito_importe": ["", "", ""],
            "subtotal": "28374.5",
            "total": "28374.5",
        },
    )

    r_confirmada = cliente_logueado.get(f"/pdf/{hash_pdf}")
    assert r_confirmada.status_code == 200
    assert r_confirmada.content == r_borrador.content


def test_ver_con_dos_periodos_muestra_el_analisis(cliente_logueado):
    con = almacenamiento_mod.conectar()
    try:
        f1 = FacturaExtraida(
            emisor="P",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-07-01",
            periodo_hasta="2026-07-31",
            fecha_emision="2026-08-01",
            fecha_vencimiento=None,
            numero_comprobante="A-1",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 100.0, 100.0)],
            subtotal=100.0,
            total=100.0,
            hash_pdf="h1",
        )
        almacenamiento_mod.guardar_factura(con, f1, estado="borrador")
        confirmar_factura(con, f1, actor="test")
        f2 = FacturaExtraida(
            emisor="P",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-08-01",
            periodo_hasta="2026-08-31",
            fecha_emision="2026-09-01",
            fecha_vencimiento=None,
            numero_comprobante="A-2",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 110.0, 110.0)],
            subtotal=110.0,
            total=110.0,
            hash_pdf="h2",
        )
        almacenamiento_mod.guardar_factura(con, f2, estado="borrador")
        confirmar_factura(con, f2, actor="test")
    finally:
        con.close()

    r = cliente_logueado.get("/ver")
    assert r.status_code == 200
    assert "$100,00" in r.text
    assert "$110,00" in r.text
    # Bloque 5: el relato en castellano tiene que estar arriba de todo.
    assert 'class="relato"' in r.text
    assert "pagaste $110,00 de energia" in r.text

    r = cliente_logueado.get(
        "/ver/excel",
        params={"servicio": "energia", "periodo_0": "2026-07-01", "periodo_1": "2026-08-01"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_ver_muestra_el_total_pagable_como_numero_principal_no_solo_consumos(
    cliente_logueado,
):
    """Bloque 6 del plan de rediseño de septiembre 2026: el número grande
    tiene que ser lo que la factura cobra de verdad (consumos + impuestos),
    no solo la suma de consumos -- antes eran la misma cosa mostrada como
    si fuera el total."""
    con = almacenamiento_mod.conectar()
    try:
        f1 = FacturaExtraida(
            emisor="P",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-07-01",
            periodo_hasta="2026-07-31",
            fecha_emision="2026-08-01",
            fecha_vencimiento=None,
            numero_comprobante="A-1",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 100.0, 100.0)],
            impuestos=[Impuesto("IVA", 21.0)],
            subtotal=100.0,
            total=121.0,
            hash_pdf="h1",
        )
        almacenamiento_mod.guardar_factura(con, f1, estado="borrador")
        confirmar_factura(con, f1, actor="test")
        f2 = FacturaExtraida(
            emisor="P",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-08-01",
            periodo_hasta="2026-08-31",
            fecha_emision="2026-09-01",
            fecha_vencimiento=None,
            numero_comprobante="A-2",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 110.0, 110.0)],
            impuestos=[Impuesto("IVA", 23.1)],
            subtotal=110.0,
            total=133.1,
            hash_pdf="h2",
        )
        almacenamiento_mod.guardar_factura(con, f2, estado="borrador")
        confirmar_factura(con, f2, actor="test")
    finally:
        con.close()

    r = cliente_logueado.get("/ver")
    assert r.status_code == 200
    # Total pagable (110 + 23,10 = 133,10), no los consumos solos (110,00).
    assert "$133,10" in r.text
    assert "pagaste $133,10 de energia" in r.text
    # Los consumos siguen visibles, como referencia -- no desaparecen.
    assert "Consumos sin impuestos: $100,00 → $110,00" in r.text


def test_ver_y_excel_muestran_las_mismas_alertas(cliente_logueado):
    """docs/auditoria-2026-09-web.md, E-11: pantalla y Excel usan la misma
    `calcular_comparacion` -- para la misma comparación no pueden mostrar
    una cantidad distinta de alertas. Uso un recargo (alerta determinística,
    no depende del IPC) para que el resultado no dependa de la red."""
    con = almacenamiento_mod.conectar()
    try:
        f1 = FacturaExtraida(
            emisor="P",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-07-01",
            periodo_hasta="2026-07-31",
            fecha_emision="2026-08-01",
            fecha_vencimiento=None,
            numero_comprobante="A-1",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 100.0, 100.0)],
            subtotal=100.0,
            total=100.0,
            hash_pdf="pe1",
        )
        almacenamiento_mod.guardar_factura(con, f1, estado="borrador")
        confirmar_factura(con, f1, actor="test")
        f2 = FacturaExtraida(
            emisor="P",
            cuit="30-1",
            servicio="energia",
            periodo_desde="2026-08-01",
            periodo_hasta="2026-08-31",
            fecha_emision="2026-09-01",
            fecha_vencimiento=None,
            numero_comprobante="A-2",
            moneda="ARS",
            conceptos=[Concepto("Cargo fijo", 1, None, 100.0, 100.0)],
            recargos=[Recargo("Interés por mora", importe=50.0)],
            subtotal=100.0,
            total=150.0,
            hash_pdf="pe2",
        )
        almacenamiento_mod.guardar_factura(con, f2, estado="borrador")
        confirmar_factura(con, f2, actor="test")
    finally:
        con.close()

    r_pantalla = cliente_logueado.get("/ver")
    assert r_pantalla.status_code == 200
    assert "🔴 1 alta(s)" in r_pantalla.text

    r_excel = cliente_logueado.get(
        "/ver/excel",
        params={"servicio": "energia", "periodo_0": "2026-07-01", "periodo_1": "2026-08-01"},
    )
    wb = load_workbook(BytesIO(r_excel.content))
    ws = wb["Resumen"]
    filas = {ws.cell(row=i, column=1).value: ws.cell(row=i, column=2).value for i in range(3, 15)}
    assert filas["Cantidad de alertas"] == 1
