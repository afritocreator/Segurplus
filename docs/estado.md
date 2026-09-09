# Estado del proyecto

Ver el plan completo en `docs/PLAN.md`. Esto es el resumen rápido de qué está construido
y qué falta, para no tener que releer el plan entero cada vez.

## Construido (Fases 0-3 del plan)

- **Fase 0** — andamiaje: `pyproject.toml`, CI, protección de `data/reales/` (hook de
  Claude Code + pre-commit real de git), subagente `revisor-financiero`, generador de
  facturas sintéticas (`docs/fixtures/generar_fixtures.py`).
- **Fase 1** — extracción: esquema canónico (`core/extraccion/esquema.py`), llamada a
  Gemini (`core/extraccion/gemini.py`, port de Kleric-), validación aritmética
  (`core/extraccion/validacion.py`, port de `validate.ts`), ingesta de PDF con doble
  lectura del total (`core/ingesta/pdf_texto.py`), almacenamiento en DuckDB con
  idempotencia y cuarentena (`core/almacenamiento.py`).
- **Fase 2** — homologación de conceptos (`core/analisis/homologacion.py`, port de
  `match.ts`).
- **Fase 3** — el corazón del análisis: descomposición precio/cantidad
  (`core/analisis/variacion.py`) y variación real deflactada por IPC
  (`core/analisis/real.py`). Ambos con tests de valor calculado a mano.
- **Fase 4 (parcial)** — alertas (`core/analisis/alertas.py`), con umbrales en
  `data/alertas.yaml`.

58 tests pasan, `ruff check` limpio.

## Falta (siguiente trabajo)

- **Fase 4** — Excel de salida (hojas Resumen, Evolución, Descomposición, Alertas,
  Cuarentena) — adaptar `core/reportes/excel.py` de Consultora.
- **Fase 5** — tablero Streamlit (`apps/`): subir PDFs, ver evolución y descomposición,
  panel de alertas, cola de cuarentena para resolver a mano.
- **Fase 6** — motor por reglas 100% local, solo si hace falta (ver punto de decisión
  pendiente en `docs/PLAN.md`).
- **No probado contra facturas reales todavía**: el pipeline corre de punta a punta
  contra las fixtures sintéticas (incluida una factura rota a propósito, que va a
  cuarentena como se espera), pero falta la prueba real con `GEMINI_API_KEY` y las
  facturas que aporte el usuario.
- `data/conceptos/*.yaml` (diccionario de homologación) todavía no tiene contenido real
  — se arma con las primeras facturas reales que se procesen.
