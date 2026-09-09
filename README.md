# Segurplus

Análisis automatizado de facturas de servicios: separa, en cada aumento de una factura
de telefonía, energía, gas, agua, seguros o alquiler, cuánto es por cambio de **cantidad**
(más líneas, más consumo) y cuánto por cambio de **precio** unitario, compara contra la
inflación (IPC), y genera alertas (recargos, conceptos nuevos, ítems duplicados).

Ver el plan completo en [`docs/PLAN.md`](docs/PLAN.md) y la decisión de cómo se lee cada
factura en [`docs/decisiones/ADR-001-lectura-de-facturas.md`](docs/decisiones/ADR-001-lectura-de-facturas.md).

Repo hermano de `afritocreator/Consultora` (de ahí se reutiliza el deflactor por IPC y el
patrón de reportes) y de `afritocreator/Kleric-` (de ahí se reutiliza el circuito de
lectura de facturas con IA y su validación aritmética).

## Cómo funciona, en una frase

El modelo (Gemini, gratis) solo extrae texto a JSON. Ningún número se muestra sin pasar
antes por un control aritmético determinístico (`core/extraccion/validacion.py`): si una
factura no cierra, va a cuarentena y no entra al análisis.

## Instalación

```bash
pip install -e ".[dev]"
python scripts/instalar-git-hooks.py   # una sola vez, protege data/reales/
```

Para usar la extracción con IA (opcional — todo lo demás funciona sin esto):

```bash
export GEMINI_API_KEY=...   # gratis en aistudio.google.com
```

## Correr los tests

```bash
pytest -q
ruff check .
```

## Estructura

- `core/extraccion/` — esquema canónico, llamada a Gemini, validación aritmética.
- `core/ingesta/` — lectura de texto de PDF, doble lectura del total, hash para idempotencia.
- `core/analisis/` — homologación de conceptos, descomposición precio/cantidad, variación
  real (deflactada por IPC), alertas.
- `core/deflactor/`, `core/macro/` — copiados de Consultora (ajuste por IPC).
- `core/almacenamiento.py` — persistencia en DuckDB (`data/reales/facturas.duckdb`,
  excluida de git).
- `docs/fixtures/` — generador de facturas sintéticas en PDF (nunca reales) para los tests.
- `data/reales/` — PDFs y base de datos reales. **Nunca se commitea** (ver CLAUDE.md).
