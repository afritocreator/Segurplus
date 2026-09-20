# Segurplus — CLAUDE.md

## Qué es esto
Herramienta de control de gastos: automatiza el análisis de facturas de servicios
(telefonía, energía, gas, agua, seguros, alquileres) para separar, en cada aumento,
cuánto es porque cambió la cantidad consumida y cuánto porque cambió el precio, y para
comparar contra la inflación. Ver `docs/PLAN.md` para el plan completo.

Hermano de `Consultora` (repo separado, misma organización): de ahí se reutiliza el
deflactor por IPC y el patrón de reportes en Excel. De `Kleric-` (repo separado) se
reutiliza el circuito de lectura de facturas con IA (Gemini, tier gratuito) y su
validación aritmética — ver `docs/decisiones/ADR-001-lectura-de-facturas.md`.

## Stack cerrado — no agregar dependencias nuevas sin un ADR en docs/decisiones/
Python 3.12 · FastAPI + Jinja2 (`web/`, ver ADR-005) · DuckDB + Parquet · Polars ·
Plotly · openpyxl · pdfplumber · google-genai · pymupdf (opcional, extra `vision`,
ver ADR-004) · pytest · ruff. Nada de Next.js/React en esta etapa: el análisis ya está
en Python (heredado de Consultora) y reescribirlo tiraría trabajo hecho -- y ningún
framework de JavaScript en `web/`: HTML/CSS plano, mismo criterio que `web/index.html`
de Consultora.

`apps/segurplus/` (Streamlit) sigue en el repo pero **en proceso de reemplazo** por
`web/` (rediseño de septiembre 2026, ver `docs/estado.md`) -- no agregar funcionalidad
nueva ahí, el trabajo nuevo va en `web/`. Se borra una vez que `web/` pase el recorrido
manual completo con facturas reales.

## Regla de oro
Toda fórmula de `core/analisis/` lleva un test con un valor calculado a mano. Un error
de signo en una cifra que se usa para discutir con un proveedor es el peor error posible
de esta herramienta. Nunca reportar un cálculo como validado sin haber corrido `pytest`
y mostrado la salida.

## La regla que separa esto de "confiar en la IA"
El modelo (Gemini) SOLO extrae texto a JSON. Nunca decide un número que se muestra sin
pasar antes por `core/extraccion/validacion.py` (control aritmético determinístico:
cantidad × precio = importe, Σ conceptos = subtotal, subtotal + impuestos = total, y la
doble lectura del total contra `pdfplumber`). Cada factura que sube el pipeline queda
primero como un **borrador editable** en "Confirmar carga", con el PDF original al lado
(`core.pipeline.procesar_pdf` nunca decide si algo entra al análisis, ver `docs/estado.md`,
sección "Plan de confirmación de carga") — recién `core.pipeline.confirmar_factura` la deja
aprobada, y solo si la aritmética cierra: si no, sigue como borrador para corregir a mano,
nunca se le muestra un número no verificado a un usuario.

## Definition of Done
`pytest` verde + `ruff check` sin warnings + toda fórmula de `core/analisis/` con
docstring que cite la fórmula + revisada por el subagente `revisor-financiero` antes de
darla por terminada.

## Zonas restringidas
- `data/reales/` (PDFs de facturas reales y `facturas.duckdb`) nunca se lee para código
  de ejemplo ni se commitea. Protegido por `.claude/hooks/guard_facturas.py` (solo dentro
  de Claude Code) y por el pre-commit real de git que instala
  `scripts/instalar-git-hooks.py` (corre siempre). Los dos **fallan abierto**: si no
  pueden leer el input, permiten en vez de bloquear.
- Nunca hardcodear umbrales de alertas ni proveedores: van en `data/alertas.yaml` y
  `data/proveedores/*.yaml`, con `vigencia_desde` cuando aplica.

## Idioma y estilo
Código, nombres de variables y docstrings en español (`efecto_cantidad`, no
`quantity_effect`). Funciones cortas, sin abstracciones que no se usan todavía.

## Datos y confianza
Todo ejemplo, test y demo usa `data/sintetico/` (facturas ficticias generadas con
ReportLab), nunca facturas reales.
