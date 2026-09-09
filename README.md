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

## Correr el tablero localmente

```bash
streamlit run streamlit_app.py
```

## Publicar en Streamlit Community Cloud (gratis, accesible desde cualquier compu)

No usamos Vercel: Segurplus es Python + Streamlit (para reutilizar el análisis financiero
ya escrito en Consultora), y Vercel no corre este tipo de servidor persistente — eso es
lo que sí resuelve Streamlit Community Cloud, gratis, mismo patrón que ya usa Consultora
(ver `docs/decisiones/ADR-002-deploy.md`).

1. Entrá a [share.streamlit.io](https://share.streamlit.io) con la cuenta de GitHub de la
   organización y elegí "New app".
2. Repo: `afritocreator/Segurplus`, branch: `main`, archivo principal: `streamlit_app.py`.
   **Marcala como pública, no privada** — el plan gratis solo permite una app privada por
   workspace y ese lugar ya lo ocupa Consultora (ver el addendum de
   `docs/decisiones/ADR-002-deploy.md`).
3. En **Advanced settings → Secrets**, pegá:
   ```toml
   GEMINI_API_KEY = "la-api-key-real"
   APP_PASSWORD = "una-clave-que-compartas-por-fuera-de-github"
   ```
   (ver `.streamlit/secrets.toml.example` para el formato — ese archivo real nunca se
   commitea, solo se carga acá). `APP_PASSWORD` es la contraseña que la app pide antes de
   mostrar cualquier pantalla, ya que queda pública — **sin esto configurado, cualquiera
   con el link entra directo**, así que no te olvides de cargarlo.
4. Deploy. Queda accesible por un link (tipo `segurplus.streamlit.app`) desde cualquier
   computadora con navegador, sin instalar nada — y sin la contraseña, no se puede usar.

**Sobre la contraseña**: es una barrera simple, no control de acceso real — no hay
usuarios ni registro de quién entró. Alcanza para que la app no quede abierta a cualquiera
que encuentre el link, pero si en algún momento maneja información más sensible, conviene
pagar el plan con apps privadas de verdad (ver el addendum del ADR).

**Importante sobre los datos**: el disco de la app en la nube NO es persistente entre
reinicios del servidor gratuito — `data/reales/facturas.duckdb` puede perderse si el
servidor se reinicia por inactividad. Mientras se prueba esto no es grave (se puede
recargar el mismo lote de PDFs, es idempotente por hash), pero antes de depender de esto
en el día a día hay que decidir dónde persiste la base de verdad — ver `docs/estado.md`.

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
