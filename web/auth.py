"""Gate de acceso de la app web: mismo esquema que
`apps/segurplus/autenticacion.py` (contraseña compartida, no control de
acceso real -- ver `core/autenticacion.py` para las limitaciones) pero
reescrito para HTTP: una cookie de sesión firmada en vez de
`st.session_state`.

Reusa `core.autenticacion.verificar_contrasena` (framework-agnóstico) para
la comparación en sí; lo único que agrega este módulo es la firma/lectura
de la cookie con `itsdangerous`, para que no se pueda armar una cookie
válida sin conocer una clave de firma que solo tiene el servidor."""

from __future__ import annotations

import os

from itsdangerous import BadSignature, URLSafeTimedSerializer

from core.autenticacion import verificar_contrasena

NOMBRE_COOKIE = "segurplus_sesion"
# Cuatro horas -- una jornada de carga, sin dejar la sesión abierta
# indefinidamente en una compu compartida.
DURACION_SEGUNDOS = 4 * 60 * 60


def _serializador() -> URLSafeTimedSerializer:
    # SECRET_KEY nunca hardcodeada -- si falta, cada reinicio del servidor
    # invalida las cookies existentes (todo el mundo tiene que loguearse de
    # nuevo), pero no hay una clave fija en el código para forjar cookies.
    clave = os.environ.get("SECRET_KEY") or "clave-de-desarrollo-local-no-usar-en-produccion"
    return URLSafeTimedSerializer(clave, salt="segurplus-sesion")


def contrasena_configurada() -> str | None:
    return os.environ.get("APP_PASSWORD")


def crear_cookie_sesion(*, usuario: str, rol: str) -> str:
    return _serializador().dumps({"usuario": usuario, "rol": rol})


def leer_sesion(valor_cookie: str | None) -> dict | None:
    """`{"usuario": ..., "rol": ...}` si la cookie es válida y no expiró,
    `None` en cualquier otro caso (cookie ausente, forjada, o vieja)."""
    if not valor_cookie:
        return None
    try:
        return _serializador().loads(valor_cookie, max_age=DURACION_SEGUNDOS)
    except BadSignature:
        return None


def intentar_login(contrasena_ingresada: str) -> str | None:
    """Devuelve la cookie de sesión si la contraseña es correcta, `None` si
    no. Mismo criterio que el piloto de Streamlit: una sola cuenta
    compartida (`operador-transitorio`), rol `administrador` -- no hay
    usuarios individuales todavía (ver docstring de
    `core/autenticacion.py`)."""
    esperada = contrasena_configurada()
    if not esperada:
        return None
    if not verificar_contrasena(contrasena_ingresada, esperada):
        return None
    return crear_cookie_sesion(usuario="operador-transitorio", rol="administrador")
