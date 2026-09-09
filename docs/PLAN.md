# Segurplus — análisis automatizado de facturas de servicios

Repo: `afritocreator/Segurplus`, separado de `Consultora` (herramienta interna, no un producto de la biblioteca de análisis financiero).

---

## Contexto

La empresa paga servicios a varios proveedores (telefonía/internet, energía, gas, agua,
seguros, alquileres). Cada uno factura con su propio formato y sus propios conceptos, así
que hoy revisar si un servicio "aumentó de más" implica abrir PDF por PDF y comparar a ojo.
Nadie lo hace todos los meses, y los aumentos se detectan tarde o no se detectan.

La pregunta de fondo no es *"¿cuánto aumentó la factura?"* sino **"¿por qué aumentó?"**. Si
Movistar subió 20%: ¿hay más chips, se consumió más, o subió el precio del abono? Son tres
causas con tres respuestas distintas —una decisión de la empresa, un tema de uso, un tema
de negociación con el proveedor— y hoy no se pueden separar.

**Resultado buscado:** tirar los PDFs del mes a una pantalla y obtener (a) la evolución de
cada servicio, (b) la descomposición de cada variación en *precio × cantidad*, (c) la
variación real descontada la inflación, y (d) alertas concretas: conceptos nuevos, aumentos
por encima del IPC, cargos por mora, ítems duplicados.

**Restricción:** costo cero mientras se prueba.

---

## La buena noticia: la parte difícil ya está resuelta en Klericó

Klericó (`afritocreator/Kleric-`) **ya lee facturas de proveedores con IA, en producción y
gratis**. Eso adelanta el proyecto varias semanas y, sobre todo, elimina la parte más
incierta: la investigación de qué modelo usar, cuánto sale y si funciona, ya está hecha y
está escrita en `docs/01-lectura-facturas-gratis.md` de ese repo.

Lo concreto que sale de ahí:

| Pieza de Klericó | Qué aporta a Segurplus |
|---|---|
| **La decisión de modelo** (`docs/01-lectura-facturas-gratis.md`) | Ya está evaluado y descartado Anthropic pago, Mistral OCR (perdió el tier gratis) y Tesseract/OCR local ("no entiende la estructura de una factura"). **Ganó Gemini Flash en tier gratuito: $0.** No hay que volver a investigarlo. |
| `app/api/invoices/parse/route.ts` | El circuito completo: mandar el PDF al modelo y pedir JSON contra un esquema. Incluye dos detalles caros de aprender: **fijar la versión del modelo** (`gemini-3.6-flash`, no el alias `latest`, que devolvía 503) y un **tope de facturas por hora** para que un bucle no queme la cuota. |
| `lib/invoice/schema.ts` | El esquema de extracción, con las descripciones campo por campo que guían al modelo. Se extiende, no se reinventa. |
| `lib/invoice/validate.ts` | **La validación aritmética.** 40 líneas con las tolerancias ya calibradas contra facturas reales: $1 por línea, 2% en el total (percepciones, redondeos). |
| `lib/invoice/match.ts` | Similitud de bigramas (coeficiente de Dice, umbral 0,45) para emparejar la descripción impresa contra un catálogo. **Es exactamente el algoritmo que Segurplus necesita** para homologar conceptos entre proveedores. |
| `docs/fixtures/generate-fixtures.py` | Generador de facturas ficticias en PDF con ReportLab — **ya está en Python**, se usa casi tal cual para los tests. |
| `scripts/test-invoice-extraction.ts` | El patrón de probar la extracción de punta a punta sin levantar la app entera. |

**El principio de diseño que hay que copiar de Klericó, más que el código:** el modelo
extrae, pero **el código audita**. En `validate.ts` está escrito textual — *"No es infalible
confiar ciegamente en el modelo: acá se audita en código"*. Cada factura pasa un control
aritmético determinístico y, si no cierra, no avanza. Eso es lo que hace defendible usar IA
para un número que después va a una discusión con un proveedor.

### Y de `Consultora` se reutiliza toda la parte de análisis

| De Consultora | Para qué |
|---|---|
| `core/macro/ipc.py` (`leer_ipc`, caché parquet del INDEC) | La variación real |
| `core/deflactor/constante.py` (`coeficiente_ajuste`, `a_pesos_constantes`) | Pasar importes a pesos constantes |
| `core/reportes/excel.py` (`_escribir_tabla`, estilos, salida a `BytesIO`) | El Excel de salida, mismo look |
| `apps/consultora/carga.py` + `paginas/arca.py` | Patrón del uploader de Streamlit (regla: no escribir a disco) |
| `CLAUDE.md`, `pyproject.toml`, CI, hooks, subagente `revisor-financiero` | Convenciones y andamiaje |

---

## Decisión de stack: Python + Streamlit (no Next.js)

Klericó es Next.js + Supabase + Vercel porque es una app multiusuario para un almacén.
Segurplus es una herramienta de análisis interna para una pantalla, y **todo el análisis
—deflactar por IPC, descomposición precio/cantidad, Excel— ya está escrito en Python en
Consultora**. Reescribir eso en TypeScript sería tirar trabajo hecho.

De Klericó se **portan ~100 líneas de lógica bien probada** (`validate.ts` y `match.ts` son
40 y 60 líneas), no el stack. Los tests de esos archivos también se portan, así que el
port se verifica solo.

Stack: Python 3.12 · pdfplumber · polars · DuckDB+Parquet · Streamlit · Plotly · openpyxl ·
ReportLab (fixtures) · pytest · ruff · `google-genai`.

---

## Punto de decisión pendiente (el que dijiste que ibas a consultar)

**¿Se puede mandar el contenido de las facturas a la API de Gemini en tier gratuito?**

Klericó ya documentó la contrapartida, textual de Google:

| Tier | Política de datos |
|---|---|
| **Gratis** | *"Content used to improve our products"* — Google puede usar el contenido para entrenar |
| **Pago** | *"Content not used to improve our products"* |

Para Klericó esto se aceptó explícitamente. Para Segurplus la decisión es de tu jefa, y lo
que hay que contarle es esto:

- **Qué saldría de la empresa:** facturas de servicios — CUIT y razón social de la empresa,
  consumos, importes. No hay datos de clientes ni de empleados.
- **La salida de emergencia es trivial y barata:** pasar al tier pago es cargar una tarjeta,
  cuesta centavos por mes con este volumen y **no requiere cambiar una línea de código**.
  Es literalmente un interruptor de facturación, no una migración.
- **Si aun así la respuesta es que no:** la Fase 6 (plantillas por reglas, todo local) cubre
  los proveedores de mayor volumen sin IA. Es más trabajo —medio día por proveedor— pero el
  proyecto no se cae. El diseño deja los dos motores intercambiables detrás de una sola
  función.

**No bloquea el arranque:** las fases 0 y 6 y todo el motor de análisis (fases 2 a 5) no
tocan la nube. Se puede avanzar mientras se decide.

---

## Arquitectura

```
PDF → texto+PDF → extracción (Gemini) → [VALIDACIÓN ARITMÉTICA] → base → análisis → Excel/tablero
                                                ↓ no cierra
                                          cuarentena (revisión manual en pantalla)
```

### `core/ingesta/pdf_texto.py`
Confirmaste que los PDFs son digitales, así que **no hace falta OCR**. `pdfplumber` cumple
dos funciones puntuales:
1. **Detectar un PDF sin capa de texto** y mandarlo a cuarentena con un mensaje claro, en
   vez de devolver vacío en silencio.
2. **La doble lectura del total** (ver abajo).

El PDF se le manda a Gemini **en binario, nativo** (es el camino probado en Klericó y
preserva el layout de las tablas, que en una factura de servicios importa).

### `core/extraccion/esquema.py`
El esquema de `lib/invoice/schema.ts` **extendido** con lo que Klericó no necesita y
Segurplus sí:

```
emisor, cuit, servicio, periodo (desde/hasta), fecha_emision, fecha_vencimiento,
numero_comprobante, moneda,
conceptos: [ {descripcion, cantidad, unidad, precio_unitario, importe} ],
consumos:  [ {magnitud, cantidad, unidad} ]        # kWh, m³, GB, minutos
impuestos: [ {nombre, importe} ]                    # IVA, ingresos brutos, tasas municipales
recargos:  [ {nombre, importe} ]                    # mora, intereses, refacturaciones
subtotal, total
```

Las descripciones campo por campo (`.describe(...)` en el Zod original) son las que guían al
modelo: se traducen y se adaptan al vocabulario de facturas de servicios.

### `core/extraccion/validacion.py` — la pieza clave
Port directo de `lib/invoice/validate.ts`, con sus tolerancias ya calibradas ($1 por línea,
2% en el total) y **dos controles más** propios de Segurplus:

1. `cantidad × precio_unitario == importe` en cada línea *(de Klericó)*
2. `Σ conceptos == subtotal` *(de Klericó)*
3. `subtotal + Σ impuestos + Σ recargos == total`
4. **Doble lectura del total**: el total que dijo el modelo contra el total leído con una
   regex sobre el texto de `pdfplumber`. Dos lecturas independientes del mismo dato; si no
   coinciden, cuarentena. Es un control barato que no necesita plantilla por proveedor y
   que ataca de frente el riesgo de que el modelo invente un número.

Si algo no cierra, la factura **no entra al análisis**: va a la cola de cuarentena con el
detalle de qué control falló, para resolver a mano en el tablero.

### `core/analisis/homologacion.py`
Port de `lib/invoice/match.ts` (Dice sobre bigramas, umbral 0,45). Mapea la descripción
literal del proveedor a un **concepto normalizado**: `"ABONO LINEA MOVIL"`, `"Abono Plan
Control"` y `"Cargo fijo móvil"` son todos `abono_movil`. El diccionario vive en
`data/conceptos/*.yaml` y crece solo. Lo que queda por debajo del umbral **no se descarta**:
sale como alerta *"concepto nuevo sin clasificar"* — que suele ser justamente el cargo que
se coló.

### `core/analisis/variacion.py` — el corazón
Descomposición precio-cantidad entre dos períodos, por concepto:

```
efecto_cantidad = (q1 - q0) * p0
efecto_precio   = (p1 - p0) * q0
efecto_cruzado  = (q1 - q0) * (p1 - p0)
Δtotal = efecto_cantidad + efecto_precio + efecto_cruzado     (identidad exacta)
```

Es exactamente tu ejemplo de Movistar: separa *"pagamos más porque hay 4 chips más"* de
*"pagamos más porque el abono subió 12%"*. Para cargos sin cantidad (cargo fijo, alquiler)
`q = 1` y todo el desvío es efecto precio. El test verifica con valores calculados a mano
que los tres efectos suman exactamente la variación total — **si no cierra, hay un error de
signo**, que es el peor error posible acá.

### `core/analisis/real.py`
Variación **nominal vs. real**. En Argentina "aumentó 8%" no dice nada si la inflación del
período fue 10%: en términos reales *bajó*. Usa `a_pesos_constantes` y el IPC del INDEC,
copiados de Consultora. Este es el número que sirve para discutir con un proveedor.

### `core/analisis/alertas.py`
Umbrales en `data/alertas.yaml`, nunca en el código:
- precio unitario que sube más de X puntos por encima del IPC del período
- concepto que aparece por primera vez / que desaparece
- cargos por mora, intereses o refacturación (no deberían existir nunca)
- ítem duplicado dentro de la misma factura
- salto de cantidad (líneas, chips, medidores) respecto del mes anterior
- período faltante o factura fuera del rango de días esperado

### Almacenamiento
`data/facturas.duckdb`: tablas `facturas` y `conceptos` (una fila por línea). Idempotente
por hash SHA-256 del PDF, así reprocesar una carpeta no duplica nada. Los PDFs reales y la
base van a una carpeta **excluida de git**, con el mismo hook de pre-commit que usa
Consultora para `clientes/`. En el repo solo hay facturas **sintéticas**.

### Salidas
- **Streamlit local**: arrastrar PDFs, evolución por servicio, gráfico de barras apiladas
  con cuánto del aumento es precio y cuánto cantidad, panel de alertas, cola de cuarentena.
- **Excel**: hojas Resumen, Evolución por proveedor, Descomposición, Alertas, Cuarentena.

---

## Fases

| Fase | Qué se entrega | Cómo se sabe que anda |
|---|---|---|
| **0** | Repo armado: `CLAUDE.md`, `pyproject.toml`, ruff+pytest, CI, carpetas gitignoradas, hook de pre-commit. Fixtures: se porta `generate-fixtures.py` de Klericó y se extiende a facturas de servicios (una de telefonía con líneas y consumo, una de energía con cargo fijo + kWh, y **una rota a propósito**) | `pytest` y `ruff check` verdes en CI |
| **1** | Extracción con Gemini + esquema + validación aritmética + doble lectura del total + cuarentena. **Todos los proveedores de una**, no de a uno | Sobre las facturas reales: el total extraído concilia con el total impreso, factura por factura. Las rotas caen en cuarentena |
| **2** | Homologación de conceptos (port de `match.ts`) + base DuckDB | El diccionario cubre los conceptos recurrentes; los raros salen como alerta, no desaparecen |
| **3** | Descomposición precio/cantidad + variación real | Tests con valores calculados a mano; los tres efectos suman la variación total |
| **4** | Alertas + Excel | Se corre sobre el histórico y las alertas coinciden con lo que ya sabés que pasó |
| **5** | Tablero Streamlit | Arrastrar los PDFs de un mes y ver el análisis completo |
| **6** | *(solo si hace falta)* plantillas por reglas, 100% locales, para los 2-3 proveedores de mayor volumen | Dan el mismo resultado que Gemini sobre las facturas de las fases 1-5 |

**El orden importa y cambió respecto de lo que hubiera propuesto sin ver Klericó.** Antes de
saber que Gemini ya andaba, lo razonable era empezar por un solo proveedor con un lector
escrito a mano. Con el camino de Klericó probado, la Fase 1 arranca **con todos los
proveedores al mismo tiempo**, que es justamente el problema que planteaste: no tener que
hacer un lector distinto para cada uno. Las plantillas por reglas pasan de ser el plan
principal a ser un respaldo opcional.

---

## Qué necesito de vos para arrancar

1. **Las facturas que tengas**, de todos los proveedores, de la mayor cantidad de meses
   posible. Van a una carpeta que no se commitea. Mínimo útil: 2 proveedores × 3 meses.
2. Idealmente, **una factura que sepas que tuvo un problema** (un cargo raro, un aumento que
   les llamó la atención): es el mejor test de que las alertas sirven.
3. Para la Fase 1, una **API key de Gemini** (gratuita, en `aistudio.google.com`) — sujeta a
   la decisión de tu jefa.

---

## Verificación

- `pytest` verde y `ruff check` sin warnings, en CI y en cada `Write`/`Edit` vía hook.
- **Cada fórmula de `core/analisis/` con un test de valor calculado a mano** (regla de oro
  heredada de Consultora), en particular que `efecto_cantidad + efecto_precio +
  efecto_cruzado == Δtotal` exactamente.
- El port de `validate.ts` y `match.ts` trae **también sus tests** (`validate.test.ts`), así
  que se verifica contra el comportamiento ya probado en Klericó.
- Suite de facturas **sintéticas** que recorre el pipeline entero, incluyendo los casos que
  *tienen* que fallar: factura que no cuadra, concepto desconocido, PDF sin capa de texto.
- Sobre facturas reales: **conciliar el total extraído contra el total impreso, factura por
  factura**. Es el único criterio que importa antes de mostrarle un número a alguien.
- Antes de dar por buena cualquier fórmula, pasarla por el subagente `revisor-financiero`.

**Una advertencia que dejó escrita Klericó y conviene respetar:** al implementar la llamada a
Gemini, leer la documentación vigente y no la memoria del modelo — la forma de esa API viene
cambiando.
