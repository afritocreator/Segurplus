"""Tests de core/extraccion/proveedores/__init__.py: lectura de
data/extraccion.yaml y la cascada de proveedores con reintentos."""

from pathlib import Path

import pytest
import yaml

from core.extraccion.gemini import ExtraccionError
from core.extraccion.proveedores import (
    RUTA_CONFIGURACION,
    ConfiguracionProveedor,
    leer_configuracion,
    leer_factura_cascada,
)


def test_data_extraccion_yaml_real_se_puede_leer():
    """El YAML del repo (no uno de prueba) tiene que ser válido -- si esto
    falla, ningún proveedor de la cascada real se puede usar."""
    configuraciones = leer_configuracion()
    assert configuraciones
    nombres = [c.nombre for c in configuraciones]
    assert "gemini" in nombres
    assert len(nombres) == len(set(nombres))  # sin nombres repetidos


def test_tipo_desconocido_lanza_value_error(tmp_path):
    ruta = tmp_path / "extraccion.yaml"
    ruta.write_text(
        yaml.dump({"proveedores": [{"nombre": "x", "tipo": "inventado", "modelo": "m"}]})
    )
    with pytest.raises(ValueError, match="desconocido"):
        leer_configuracion(ruta)


def test_sin_proveedores_lanza_value_error(tmp_path):
    ruta = tmp_path / "extraccion.yaml"
    ruta.write_text(yaml.dump({"proveedores": []}))
    with pytest.raises(ValueError, match="vacío"):
        leer_configuracion(ruta)


def test_yaml_sin_la_clave_proveedores_lanza_value_error(tmp_path):
    ruta = tmp_path / "extraccion.yaml"
    ruta.write_text(yaml.dump({"otra_cosa": 1}))
    with pytest.raises(ValueError, match="proveedores"):
        leer_configuracion(ruta)


def test_ruta_configuracion_apunta_al_yaml_del_repo():
    assert RUTA_CONFIGURACION == Path(__file__).resolve().parents[3] / "data" / "extraccion.yaml"
    assert RUTA_CONFIGURACION.exists()


def _config(nombre: str, tipo: str = "gemini") -> ConfiguracionProveedor:
    return ConfiguracionProveedor(nombre=nombre, tipo=tipo, modelo="m")


def test_cascada_usa_el_primero_que_funciona(monkeypatch):
    """El segundo proveedor no debería ni intentarse si el primero anda."""
    llamados = []

    def _falso_leer_con_proveedor(config, pdf_bytes, texto_extraido):
        llamados.append(config.nombre)
        if config.nombre == "primero":
            return "factura-del-primero"
        raise AssertionError("no debería llegar acá")

    monkeypatch.setattr(
        "core.extraccion.proveedores._leer_con_proveedor", _falso_leer_con_proveedor
    )
    resultado = leer_factura_cascada(
        b"pdf", configuraciones=[_config("primero"), _config("segundo")]
    )
    assert resultado == "factura-del-primero"
    assert llamados == ["primero"]


def test_cascada_pasa_al_siguiente_si_el_primero_falla(monkeypatch):
    def _falso_leer_con_proveedor(config, pdf_bytes, texto_extraido):
        if config.nombre == "primero":
            raise ExtraccionError("cuota agotada")
        return "factura-del-segundo"

    monkeypatch.setattr(
        "core.extraccion.proveedores._leer_con_proveedor", _falso_leer_con_proveedor
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)  # no esperar de verdad en el test
    resultado = leer_factura_cascada(
        b"pdf",
        configuraciones=[_config("primero"), _config("segundo")],
        intentos_por_proveedor=1,
    )
    assert resultado == "factura-del-segundo"


def test_cascada_reintenta_antes_de_pasar_al_siguiente(monkeypatch):
    intentos_primero = []

    def _falso_leer_con_proveedor(config, pdf_bytes, texto_extraido):
        if config.nombre == "primero":
            intentos_primero.append(1)
            raise ExtraccionError("falla transitoria")
        return "factura-del-segundo"

    monkeypatch.setattr(
        "core.extraccion.proveedores._leer_con_proveedor", _falso_leer_con_proveedor
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)
    resultado = leer_factura_cascada(
        b"pdf",
        configuraciones=[_config("primero"), _config("segundo")],
        intentos_por_proveedor=3,
    )
    assert resultado == "factura-del-segundo"
    assert len(intentos_primero) == 3  # se reintentó las 3 veces antes de pasar de proveedor


def test_cascada_lanza_con_el_detalle_de_todos_si_todos_fallan(monkeypatch):
    def _falso_leer_con_proveedor(config, pdf_bytes, texto_extraido):
        raise ExtraccionError(f"fallo de {config.nombre}")

    monkeypatch.setattr(
        "core.extraccion.proveedores._leer_con_proveedor", _falso_leer_con_proveedor
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)
    with pytest.raises(ExtraccionError) as excinfo:
        leer_factura_cascada(
            b"pdf",
            configuraciones=[_config("primero"), _config("segundo")],
            intentos_por_proveedor=1,
        )
    assert "fallo de primero" in str(excinfo.value)
    assert "fallo de segundo" in str(excinfo.value)
