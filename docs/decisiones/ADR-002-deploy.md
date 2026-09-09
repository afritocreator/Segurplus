# ADR-002: Streamlit Community Cloud para el deploy, no Vercel

**Estado**: aceptado. **Fecha**: 2026-09-09.

## Contexto

Se necesita que Segurplus quede accesible desde las computadoras del trabajo, sin
instalar nada, gratis. `afritocreator/Kleric-` (repo hermano) despliega en Vercel porque
es una app Next.js.

## Decisión

Se publica en **Streamlit Community Cloud** (share.streamlit.io), no en Vercel.

## Por qué no Vercel

Vercel está construido para aplicaciones Next.js/serverless: funciones que responden y
terminan, o un frontend estático. Streamlit necesita un **proceso de servidor persistente**
con estado en memoria y una conexión en vivo con el navegador (WebSocket) — el modelo de
ejecución de Vercel no soporta eso. Correr Segurplus ahí requeriría reescribir el tablero
entero en Next.js/React, lo que tira el trabajo ya hecho: todo el análisis financiero
(deflactor por IPC, descomposición precio/cantidad) ya está escrito y probado en Python,
heredado de `afritocreator/Consultora` (ver CLAUDE.md, stack cerrado).

## Por qué Streamlit Community Cloud

- Gratis, sin límite de tiempo, pensado exactamente para este stack (Python + Streamlit).
- Mismo servicio que ya usa `Consultora` (ver `requirements.txt` ahí, mismo patrón).
- Deploy desde GitHub directo, sin configuración de infraestructura.
- Secrets (como `GEMINI_API_KEY`) se cargan desde el dashboard, nunca en el repo.

## Consecuencias

- El disco del servidor gratuito **no es persistente** entre reinicios por inactividad —
  `data/reales/facturas.duckdb` puede perderse. Mientras se prueba la herramienta no es
  grave (recargar el mismo lote de PDFs no duplica nada, es idempotente por hash), pero
  si el equipo empieza a depender de esto día a día, hay que decidir dónde persiste la
  base real (opciones: un volumen pago, o exportar a Excel/Sheets después de cada carga
  como respaldo — no se resuelve en este ADR).
- Si en algún momento se necesita autenticación, multiusuario real o un dominio propio, ahí
  sí conviene reevaluar el stack — no antes.
- **Límite de concurrencia de DuckDB** (docs/auditoria-2026-09.md, hallazgo A-15):
  `core/almacenamiento.py::conectar` abre una conexión nueva contra el mismo archivo
  `.duckdb` en cada request. DuckDB soporta una sola conexión de ESCRITURA a la vez sobre un
  mismo archivo (a diferencia de Postgres/MySQL) — con un único usuario cargando facturas a
  la vez (el uso esperado hoy, un equipo chico) no es un problema; si dos personas
  cargan/procesan al mismo tiempo, la segunda conexión puede fallar en vez de esperar. No
  está mitigado (no hay cola ni lock explícito) — si el uso concurrente se vuelve real, la
  opción más simple es serializar el acceso de escritura desde la propia app (ej. un lock de
  archivo), antes de migrar a otro motor.

## Addendum (2026-09-09): app pública con contraseña, no privada

El plan gratuito de Streamlit Community Cloud permite **una sola app privada por
workspace** de GitHub. Ese lugar ya lo ocupa `afritocreator/Consultora` (desplegada
privada, con los socios de la consultora invitados como viewers — ver
`docs/decisiones/ADR-004-app-en-la-nube.md` de ese repo). Segurplus no puede ser también
privada sin pasar a un plan pago (Streamlit Cloud for Teams), lo que rompe el requisito de
"gratis mientras se prueba".

**Decisión**: Segurplus se despliega como app **pública**, pero con un login de
contraseña compartida agregado adentro de la propia app
(`apps/segurplus/autenticacion.py` + `core/autenticacion.py`) — nadie sin la clave ve
ninguna pantalla, aunque técnicamente cualquiera con el link podría llegar hasta el
formulario de login.

**Límite aceptado, dicho sin vueltas**: esto NO es control de acceso real. Es una barrera
contra quien encuentra el link por casualidad, no contra alguien decidido a entrar — no
hay usuarios individuales, no hay registro de quién entró, y la contraseña se comparte
por fuera de la app (Slack, de palabra, como corresponda). El repositorio de GitHub sigue
siendo privado en todo momento — esto solo afecta la visibilidad de la app ya desplegada,
no el código fuente.

Si en algún momento esto pasa a manejar información más sensible o a un uso más amplio,
las alternativas reales son: pagar el plan de Streamlit Cloud con múltiples apps privadas
y login por email/Google, o migrar a otro hosting gratuito con autenticación propia (ej.
Hugging Face Spaces). No se resuelve acá — se documenta como el próximo paso si hace falta.
