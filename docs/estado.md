# Estado del proyecto

Ver el plan completo en `docs/PLAN.md`. Esto es el resumen rápido de qué está construido
y qué falta, para no tener que releer el plan entero cada vez.

## Construido (Fases 0-5 del plan completas)

- **Fase 0** — andamiaje: `pyproject.toml`, CI, protección de `data/reales/` (hook de
  Claude Code + pre-commit real de git), subagente `revisor-financiero`, generador de
  facturas sintéticas (`docs/fixtures/generar_fixtures.py`).
- **Fase 1** — extracción: esquema canónico (`core/extraccion/esquema.py`), llamada a
  Gemini (`core/extraccion/gemini.py`, port de Kleric-), validación aritmética
  (`core/extraccion/validacion.py`, port de `validate.ts`), ingesta de PDF con doble
  lectura del total (`core/ingesta/pdf_texto.py`), almacenamiento en DuckDB con
  idempotencia y cuarentena (`core/almacenamiento.py`, incluye recargos).
- **Fase 2** — homologación de conceptos (`core/analisis/homologacion.py`, port de
  `match.ts`) con diccionario en `data/conceptos/*.yaml`.
- **Fase 3** — el corazón del análisis: descomposición precio/cantidad
  (`core/analisis/variacion.py`) y variación real deflactada por IPC
  (`core/analisis/real.py`). Ambos con tests de valor calculado a mano.
- **Fase 4** — alertas (`core/analisis/alertas.py`), con umbrales en `data/alertas.yaml`,
  y Excel de salida (`core/reportes/excel.py`: hojas Resumen, Descomposición, Alertas,
  Cuarentena), con botón de descarga en el tablero.
- **Fase 5** — tablero Streamlit (`streamlit_app.py` + `apps/segurplus/paginas/`): cargar
  PDFs, ver evolución con descomposición precio/cantidad y alertas, cola de cuarentena.
  Listo para publicar en Streamlit Community Cloud (ver README.md y
  `docs/decisiones/ADR-002-deploy.md`, incluido el addendum sobre app pública + contraseña).
- **Login con contraseña compartida** (`apps/segurplus/autenticacion.py` +
  `core/autenticacion.py`): la app se publica pública (el único slot privado del plan
  gratis ya lo usa Consultora), así que pide `APP_PASSWORD` antes de mostrar cualquier
  pantalla. Sin esa clave configurada en secrets, no bloquea (desarrollo local).
- `core/pipeline.py` une todo lo anterior en una sola función por PDF
  (`procesar_pdf`), que es lo único que llama la app.

151 tests pasan (1 skipped, requiere `GEMINI_API_KEY` real), `ruff check` limpio. El tablero
se probó levantado localmente (HTTP 200, sin errores de import). Las tres páginas del
tablero (Cargar, Evolución, Cuarentena) tienen tests con `AppTest` de Streamlit, no solo el
login -- ver `docs/auditoria-2026-09.md`, hallazgo A-19 (Bloque 8 del plan de correcciones).

## Falta (siguiente trabajo)

- **Fase 6** — motor por reglas 100% local, solo si hace falta (ver punto de decisión
  pendiente en `docs/PLAN.md`).
- **No probado contra facturas reales todavía**: el pipeline corre de punta a punta
  contra las fixtures sintéticas (incluida una factura rota a propósito, que va a
  cuarentena como se espera) y el tablero levanta sin errores, pero falta la prueba real
  con `GEMINI_API_KEY` y las facturas que aporte el usuario.
- `data/conceptos/*.yaml` (uno por servicio + `comunes.yaml`) tiene solo los alias más
  obvios para arrancar — se completa con las primeras facturas reales que se procesen.
  El umbral de homologación (`data/homologacion.yaml`) es un valor conservador elegido
  sin facturas reales — falta calibrarlo (ver Bloque 9 del plan de correcciones).
- **Persistencia en la nube**: el disco de Streamlit Community Cloud gratuito no es
  durable entre reinicios (ver ADR-002). No es grave para probar, sí para depender de
  esto en el día a día — a resolver cuando se decida usarlo en producción.
