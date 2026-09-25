# Segurplus

Segurplus analiza facturas argentinas de servicios para explicar aumentos por precio,
cantidad e inflación. La aplicación principal está en `web/` (FastAPI + Jinja2); no
agregues funcionalidad nueva a la interfaz heredada de Streamlit.

## Reglas de trabajo

- Usá Python 3.12 y el stack declarado en `pyproject.toml`. Una dependencia nueva exige
  un ADR en `docs/decisiones/`.
- Código, nombres y docstrings en español. Preferí funciones cortas y cambios acotados.
- Gemini solo extrae texto a JSON. Toda cifra visible debe pasar por la validación
  determinística y solo las versiones aprobadas pueden alimentar análisis, alertas o
  Excel.
- Los importes se validan al centavo. No mezcles pesos nominales y constantes; si falta
  IPC, el resultado real es «no calculable», nunca inflación cero.
- Cada fórmula nueva o modificada en `core/analisis/` necesita un test con un resultado
  calculado a mano y una docstring con la fórmula.
- Para cambios financieros, usá la skill `revisor-financiero` antes de dar el trabajo por
  terminado. Para un corte o despliegue, usá `release-verifier`.
- Para tareas de infraestructura, preferí las skills instaladas de Supabase y Render.
  Para inspección visual de comprobantes sintéticos, usá la skill PDF.

## Datos y seguridad

- Nunca leas, copies, muestres ni commitees contenido de `data/reales/`. Su único archivo
  permitido en Git es `data/reales/README.md`.
- Nunca incorpores facturas reales, CUIT válidos, credenciales ni respuestas privadas de
  proveedores a ejemplos, tests, logs o Git.
- Todos los ejemplos, pruebas y recorridos usan `data/sintetico/` o
  `docs/fixtures/sintetico/`.
- No cambies secretos o producción como consecuencia implícita de una tarea de código.

## Verificación

Antes de afirmar que un cambio está terminado:

1. Ejecutá los tests relevantes durante el desarrollo.
2. Ejecutá `ruff check .`.
3. Ejecutá `pytest -q` para el cierre completo.
4. Si cambió una fórmula, aplicá la revisión financiera independiente.
5. Distinguí siempre entre implementado, probado localmente y verificado en Render.

No hagas push, despliegues ni migraciones de producción salvo pedido explícito.
