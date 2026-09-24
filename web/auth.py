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

import hashlib
import hmac
import os

from itsdangerous import BadSignature, URLSafeTimedSerializer

from core.autenticacion import verificar_contrasena

NOMBRE_COOKIE = "segurplus_sesion"
# Cuatro horas -- una jornada de carga, sin dejar la sesión abierta
# indefinidamente en una compu compartida.
DURACION_SEGUNDOS = 4 * 60 * 60


def secret_key_configurada() -> str | None:
    return os.environ.get("SECRET_KEY")


def _serializador() -> URLSafeTimedSerializer:
    # docs/auditoria-2026-09-web.md, E-17: antes había una clave fija de
    # respaldo si faltaba SECRET_KEY -- el comentario decía "nunca
    # hardcodeada" pero la clave hardcodeada estaba ahí mismo, dos líneas
    # abajo. Sin SECRET_KEY (y sin SEGURPLUS_DEV=1) no se arma ninguna
    # cookie: mismo criterio que ya usa APP_PASSWORD en `post_login`.
    clave = secret_key_configurada()
    if not clave:
        if os.environ.get("SEGURPLUS_DEV") == "1" and os.environ.get("SEGURPLUS_PRODUCTION") != "1":
            clave = "clave-de-desarrollo-local-no-usar-en-produccion"
        else:
            raise RuntimeError(
                "Falta SECRET_KEY. Por seguridad, la app no arma cookies de sesión sin "
                "ella (para desarrollo local, definí SEGURPLUS_DEV=1)."
            )
    return URLSafeTimedSerializer(clave, salt="segurplus-sesion")


def contrasena_configurada() -> str | None:
    return os.environ.get("APP_PASSWORD")


def crear_cookie_sesion(*, usuario: str, rol: str) -> str:
    datos = {"usuario": usuario, "rol": rol}
    return _serializador().dumps(datos)


def crear_token_csrf(valor_cookie: str) -> str:
    huella = hashlib.sha256(valor_cookie.encode()).hexdigest()
    return URLSafeTimedSerializer(secret_key_configurada(), salt="segurplus-csrf").dumps(huella)


def verificar_token_csrf(valor_cookie: str | None, token: str | None) -> bool:
    if not valor_cookie or not token or not secret_key_configurada():
        return False
    try:
        huella = URLSafeTimedSerializer(
            secret_key_configurada(), salt="segurplus-csrf"
        ).loads(token, max_age=DURACION_SEGUNDOS)
    except BadSignature:
        return False
    return hmac.compare_digest(huella, hashlib.sha256(valor_cookie.encode()).hexdigest())


def leer_sesion(valor_cookie: str | None) -> dict | None:
    """`{"usuario": ..., "rol": ...}` si la cookie es válida y no expiró,
    `None` en cualquier otro caso (cookie ausente, forjada, vieja, o sin
    SECRET_KEY configurada). Se llama en cada request que llega al
    servidor (`_gate_de_sesion`), así que nunca puede levantar una
    excepción por falta de configuración -- eso lo reporta `post_login`,
    donde sí hay una pantalla para mostrar el error."""
    if not valor_cookie:
        return None
    if not secret_key_configurada() and (
        os.environ.get("SEGURPLUS_DEV") != "1"
        or os.environ.get("SEGURPLUS_PRODUCTION") == "1"
    ):
        return None
    try:
        sesion = _serializador().loads(valor_cookie, max_age=DURACION_SEGUNDOS)
        return sesion
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
