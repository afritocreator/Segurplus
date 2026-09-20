# ADR-005: FastAPI + HTML plano en vez de Streamlit; Render en vez de Streamlit Community Cloud

**Estado**: aceptado. **Fecha**: 2026-09-20. Reemplaza a `ADR-002-deploy.md`.

## Contexto

El usuario reportó que la herramienta no sirve para lo que se construyó: además del
problema de lectura (ver `docs/banco_extraccion.md`), el tablero Streamlit costaba de
usar -- 7 páginas, 5 pestañas en la pantalla de análisis, jerga técnica visible
(`(sin_homologar)`, hashes de 64 caracteres, "efecto combinado"). Pidió explícitamente
sacar la herramienta de Streamlit.

## Decisión

**FastAPI + plantillas Jinja2 + HTML/CSS plano** (`web/`), sin ningún framework de
JavaScript -- mismo criterio que `web/index.html` de Consultora (repo hermano): sin
dependencias nuevas de frontend, para que cualquiera del equipo lo pueda tocar sin
aprender un stack nuevo. El hosting pasa de Streamlit Community Cloud a **Render**
(render.com), plan gratuito.

## Por qué NO reescribir el análisis

`core/` son ~6.400 líneas con 470+ tests verdes, fórmulas financieras revisadas por el
subagente `revisor-financiero` y cuatro auditorías encima (ver `docs/estado.md`). El
problema nunca fue el análisis -- es la cara y la lectura. `web/` es una cáscara nueva
sobre el mismo `core/`, sin tocarlo: los mismos módulos que ya usaba
`apps/segurplus/paginas/*.py` (`core.pipeline`, `core.analisis.*`, `core.reportes.excel`)
se llaman igual desde las rutas de FastAPI.

## Por qué FastAPI y no otro framework Python

- Async nativo (relevante para no bloquear en la subida de PDFs grandes), tipado con
  Pydantic solo donde hace falta (no se fuerza en `core/`, que sigue con dataclasses
  simples por CLAUDE.md).
- `TestClient` (basado en `httpx`) permite probar de punta a punta, incluido un
  **upload de archivo real** -- algo que `AppTest` de Streamlit no podía simular (ver
  docstring de `tests/apps/test_cargar_app.py`). Los tests de `web/` en
  `tests/web/test_app.py` prueban el flujo completo subir → revisar → confirmar, no solo
  que la página renderiza.
- Liviano: no impone un ORM, un sistema de plantillas propio, ni un modelo de estado como
  el de Streamlit (`st.session_state`, reruns completos por cada interacción) -- una
  request HTTP normal, formularios HTML comunes.

## Por qué HTML plano y no un framework de JS

El pedido explícito fue simplificar, no cambiar una complejidad por otra. React/Vue
exigirían un build step, un `package.json`, y aprender un stack nuevo para tocar una
pantalla -- exactamente lo que CLAUDE.md pide evitar ("funciones cortas, sin
abstracciones que no se usan todavía"). Las tres pantallas (Subir, Revisar, Ver) son
formularios y tablas: HTML servido por el propio backend alcanza. Las tablas de
"Precio vs. cantidad" y "Serie histórica" se muestran como `<table>` en vez de gráficos
Plotly en esta primera versión -- una reducción de alcance deliberada (ver
`docs/estado.md`, Bloque 4): agregar gráficos interactivos sin JavaScript exigiría
Plotly.js por CDN y JavaScript de verdad para las interacciones, que es exactamente el
tipo de complejidad que este ADR decide no sumar todavía. Un gráfico puede agregarse
después sin tocar `core/`, si de verdad hace falta.

## Por qué Render y no Streamlit Community Cloud

Streamlit Community Cloud solo sirve apps Streamlit -- una vez que el frontend deja de
ser Streamlit, deja de aplicar. Se evaluaron las alternativas gratuitas del ecosistema:

- **Render (elegido)**: permite **uso comercial** en el plan gratuito (a diferencia de
  Vercel, que lo prohíbe -- ver `ADR-002-deploy.md`, la misma razón que descartó Vercel
  para Streamlit sigue vigente acá) y **no pide tarjeta de crédito**. Duerme a los 15
  minutos sin tráfico y despierta en ~1 minuto -- la misma limitación de "primera carga
  lenta" que ya tenía Streamlit Community Cloud, así que no es una regresión.
- **Hugging Face Spaces (descartado)**: los Docker Spaces (necesarios para correr FastAPI)
  ahora exigen un plan pago; el free tier solo cubre Spaces Gradio/estáticos.
- **Vercel (descartado, igual que en ADR-002)**: el plan gratuito prohíbe uso comercial.

## Consecuencias

- **Base de datos**: sin cambios -- sigue siendo el Supabase gratuito ya configurado
  (schema `segurplus`, rol `segurplus_app`, ver `ADR-003-persistencia-durable.md`).
  `core/almacenamiento.py::conectar()` no sabe ni le importa qué framework HTTP lo llama.
- **Autenticación**: mismo esquema que el piloto Streamlit (contraseña compartida, NO
  control de acceso real -- ver `core/autenticacion.py`), reimplementado para HTTP en
  `web/auth.py` con una cookie de sesión firmada (`itsdangerous`) en vez de
  `st.session_state`. La limitación es la misma que ya estaba documentada en ADR-002: sin
  usuarios individuales, sin auditoría de quién entró.
- **Nueva dependencia**: `fastapi`, `jinja2` (ya era transitiva de Streamlit),
  `python-multipart` (uploads), `itsdangerous` (cookie firmada), `uvicorn` (servidor
  ASGI) -- agregadas a `pyproject.toml`. `requirements.txt` (la copia manual para
  Streamlit Community Cloud) NO las incluye a propósito: ese archivo es específicamente
  para el mecanismo de instalación de Streamlit Cloud, que deja de usarse una vez que
  `web/` reemplace a `apps/segurplus/`.
- **Streamlit no se borra todavía**: `apps/segurplus/` y `streamlit_app.py` siguen
  andando en paralelo hasta que `web/` pase el recorrido manual completo con las 4
  facturas reales del banco de medición (ver el plan de rediseño en `docs/estado.md`).
  Recién ahí se borra el tablero viejo y `ADR-002-deploy.md` pasa a ser histórico, igual
  que las auditorías viejas del proyecto no se reescriben.
- **Alcance reducido de esta primera versión de `web/`**: sin gráficos interactivos (ver
  arriba), y sin las pantallas "Casos" ni "Conceptos sin clasificar" -- son pantallas de
  administración interna, no el camino principal que pidió el usuario. Quedan pendientes
  de una versión posterior si hacen falta.
