# Auditoría de Segurplus — septiembre 2026

**Este documento solo audita. No se corrigió nada acá.** Cada hallazgo trae la evidencia que
lo reproduce; las correcciones van en un plan aparte, después de revisar esto.

Motivo de la auditoría: la herramienta se armó rápido, en una sola sesión, y ya está
desplegada en una URL pública (con contraseña) con la API key de Gemini cargada, a punto de
recibir facturas reales cuyos números se van a usar para discutir con proveedores. Hasta acá
alcanzaba con "88 tests pasan" — a partir de ahora no.

---

## Resumen ejecutivo

**No cargar facturas reales todavía.** Dos hallazgos críticos afectan directamente números
que el usuario ve o que van a un Excel que se le muestra a un proveedor:

- **A-1 — la alerta de "precio por encima de la inflación" compara contra 0% siempre**, y
  encima el mensaje que se le muestra al usuario es **activamente falso**, no solo
  impreciso: con inflación mensual real, un aumento que estuvo *por debajo* de la inflación
  se reporta con el texto "X puntos **por encima** del IPC" (verificado con números:
  +6% nominal, +12% inflación real → la app dice "6,0 puntos por encima del IPC" cuando en
  realidad estuvo 6 puntos por debajo). Con inflación alta, esto dispara alerta roja en
  *casi todos* los conceptos.
- **A-20 — plata desaparece del total mostrado, en silencio, si la cantidad agregada de un
  concepto da cero** (típico de una nota de crédito o un ajuste con signo negativo en el
  mismo período): el importe correspondiente no se reasigna a ningún lado, se evapora del
  total que sale en pantalla, en el Excel y en el cálculo de variación real.

**El resto del pipeline de extracción y validación (lo que decide si una factura entra al
análisis o va a cuarentena) es sólido en su lógica** — la identidad
`efecto_cantidad + efecto_precio + efecto_cruzado = variación total` cierra siempre y está
bien testeada, `core/deflactor/` y `core/analisis/real.py` están verificados sin
objeciones. Pero **nunca se probó contra la API real de Gemini** (A-2): no hay ninguna
garantía de que el modelo devuelva el JSON en la forma que el código espera, y si el modelo
omite `subtotal` o `total` — algo plausible en una factura rara — los controles que deberían
frenar eso **se autocumplen y dejan pasar cualquier número** (A-4). Además, la agregación de
conceptos mezcla cantidades de **unidades distintas** (kWh + GB, por ejemplo) bajo un mismo
concepto normalizado sin ninguna barrera (A-16), lo que puede volver sin sentido tanto el
precio unitario promedio como las alertas de precio y de salto de cantidad que dependen de
él.

La homologación de conceptos tiene un umbral demasiado permisivo: cargos que deberían
disparar la alerta de "concepto nuevo" (el caso de uso central de la herramienta) se
absorben en silencio dentro de conceptos existentes (A-3).

**Dos páginas completas del tablero — Evolución y Cuarentena — nunca se ejecutaron en un
test.** Eso incluye la página donde vive el bug de inflación: nadie, ni una corrida
automática, llegó a ver ese cálculo funcionar antes de este informe.

En limpio: **la ingesta y el control aritmético son confiables en su lógica pero no están
verificados contra el mundo real; el análisis de variación (precio/cantidad) es sólido; la
comparación contra inflación está rota; y la operación en la nube (contraseña, cuota,
archivos temporales, reintentos) tiene agujeros de higiene que no afectan un número hoy pero
sí van a doler con uso real.**

---

## Tabla de hallazgos (para escanear rápido)

| ID | Severidad | Dónde | Resumen |
|---|---|---|---|
| A-1 | Crítico | `evolucion.py:86-96,150` | Alerta de precio vs. IPC compara contra 0% siempre; el texto mostrado llega a decir lo contrario de lo que pasó |
| A-2 | Crítico | `core/extraccion/gemini.py` | Nunca se probó contra la API real de Gemini — sin API key en este entorno para auditarlo |
| A-3 | Crítico | `core/analisis/homologacion.py` | Umbral 0,45 absorbe recargos/cargos nuevos como conceptos existentes |
| A-20 | Crítico | `core/analisis/agregacion.py:44` | Plata desaparece del total si la cantidad agregada de un concepto da 0 |
| A-4 | Alto | `core/extraccion/validacion.py` | Controles de subtotal/total se auto-cumplen si el modelo los omite |
| A-5 | Alto | `core/ingesta/pdf_texto.py::total_impreso` | Formato US de total da un valor incorrecto en silencio (no `None`) |
| A-6 | Alto | `evolucion.py` / `core/pipeline.py` | Alerta de ítem duplicado nunca se dispara; comentario en el código dice lo contrario |
| A-7 | Alto | `core/extraccion/gemini.py:33` | Tope de llamadas/hora declarado, nunca aplicado |
| A-8 | Alto | `apps/segurplus/secretos.py` | El login falla abierto ante cualquier error, no solo "sin secrets.toml" |
| A-9 | Alto | `apps/segurplus/paginas/cargar.py` | PDFs de facturas reales quedan en disco sin borrarse |
| A-16 | Alto | `core/analisis/agregacion.py` | Se agregan cantidades de unidades distintas bajo un mismo concepto; `unidad` se pierde antes de llegar a la función |
| A-21 | Alto | `tests/analisis/test_agregacion.py:40-43` | El test que debería cubrir A-20 no prueba el caso que importa |
| A-10 | Medio | CI / `pyproject.toml` | CI nunca corrió; todo desarrollado en 3.11 contra `>=3.12` declarado |
| A-11 | Medio | `core/extraccion/esquema.py` | Fechas del modelo sin validar ni normalizar |
| A-12 | Medio | `evolucion.py:96` | `except Exception` genérico tapa errores distintos con un solo mensaje |
| A-13 | Medio | `data/alertas.yaml` | `dias_tolerancia_periodo` declarado, sin código que lo use |
| A-14 | Medio | `docs/PLAN.md` vs `esquema.py` | El campo `consumos` que describe el plan no existe en el código |
| A-15 | Medio | infraestructura | DuckDB sin manejo de concurrencia; `leer_ipc()` sin cache de Streamlit |
| A-17 | Medio | `core/almacenamiento.py` | Una factura en cuarentena no se puede reintentar nunca |
| A-18 | Medio | `core/pipeline.py` | PDF corrupto (no solo sin texto) tumba el lote completo de carga |
| A-19 | Medio | cobertura de tests | Evolución y Cuarentena, 0% de cobertura — nunca se ejecutaron en un test |
| A-22 | Medio | `core/analisis/alertas.py:119` | Alerta de precio vs. IPC resta porcentajes (lineal) en vez de deflactar |
| A-23 | Bajo | `evolucion.py:84` | Delta nominal mostrado junto a la columna de variación real, sin rotular |
| A-24 | Medio | `core/analisis/agregacion.py` | Cantidad neta negativa (nota de crédito mayor al cargo) sin decisión ni detección |
| A-25 | Medio | `core/extraccion/esquema.py` | Unidad sin normalizar puede fragmentar un concepto en dos etiquetas por mayúsculas/espacios |
| A-26 | Bajo | `core/ingesta/pdf_texto.py::_parsear_monto` | Con coma Y punto en el token, no valida que los grupos de miles no-decimales tengan 3 dígitos |
| A-27 | Medio | `core/analisis/alertas.py::alertas_por_periodo_faltante` | Asume periodicidad mensual; un servicio bimestral (gas, algunos casos de energía) alerta siempre, en todas las comparaciones |

---

## Hallazgos

### Críticos — un número que ve el usuario puede estar mal, o la herramienta no funciona

#### A-1 — La alerta de "precio por encima de la inflación" compara contra 0% siempre

**Dónde**: `apps/segurplus/paginas/evolucion.py:86-96,150`

```python
ipc_periodo_pct = 0.0
try:
    ...
    vr = variacion_real(total_0, fecha_0, total_1, fecha_1, df_ipc=leer_ipc())
    ipc_periodo_pct = (
        vr.variacion_real_pct - vr.variacion_nominal_pct
    )  # aprox. inflación del período
    ...
except Exception:
    st.caption("No se pudo calcular la variación real (sin datos de IPC para ese rango).")
...
alertas_totales = generar_alertas(
    factura_agregada, descomposiciones, ipc_periodo_pct=max(ipc_periodo_pct, 0.0)
)
```

`variacion_real_pct - variacion_nominal_pct` **no es la inflación del período**. La relación
correcta entre variación nominal, real e inflación es
`(1 + nominal) = (1 + real) × (1 + inflación)`, de donde
`inflación = (1+nominal)/(1+real) - 1`. La resta que hay en el código da, salvo casos
degenerados, un número **negativo** (aproximadamente `-inflación`, no `+inflación`), y el
`max(ipc_periodo_pct, 0.0)` de la línea 150 lo aplasta a **0.0 en absolutamente todos los
casos plausibles**.

**Evidencia reproducible**:
```
>>> nominal = 0.15, real = 0.0267857142857  # 15% nominal, ~12% inflación real (caso de test_real.py)
>>> ipc_periodo_pct = real - nominal
-0.1232142857142857
>>> max(ipc_periodo_pct, 0.0)
0.0
```

**Impacto**: `alertas_por_precio_sobre_ipc` (`core/analisis/alertas.py`) recibe
`ipc_periodo_pct=0.0` siempre, así que cualquier concepto cuyo precio suba más de 5 puntos
porcentuales (el umbral de `data/alertas.yaml`) dispara la alerta, sin importar si ese
aumento está en línea con la inflación real o muy por encima. La alerta no discrimina nada
— es un umbral fijo del 5% disfrazado de "comparación contra inflación".

**El texto de la alerta, además, miente activamente** (verificado por el subagente
`revisor-financiero` con el código real corrido, no solo en teoría): con un concepto que
sube 6% nominal en un período con 12% de inflación real, la app muestra
`🔴 alta: "abono_movil": precio unitario subió 6.0%, 6.0 puntos por encima del IPC del
período` — cuando en realidad ese aumento estuvo **6 puntos por debajo** de la inflación.
Con inflación mensual real (2-3% en Argentina), la consecuencia práctica es que
**prácticamente todo concepto dispara alerta roja**, siempre, y nunca hay forma de que un
aumento en línea con la inflación quede sin alertar — el sistema no puede distinguir "esto
sí hay que cuestionarlo" de "esto es lo que pasa todos los meses".

Confirmación algebraica (`revisor-financiero`): con `r = (1+n)/(1+π) - 1`, la expresión que
usa el código da `r - n = -(1+n)·π/(1+π)`, que es **negativa para todo π > 0**,
independientemente del signo de `n` — no es "aproximadamente" la inflación en ningún
régimen, es su opuesto.

**Nota sobre `core/`**: esta cuenta vive dentro de una página de Streamlit, no en
`core/analisis/`, violando la propia docstring del archivo (`evolucion.py:8`: *"ningún
cálculo nuevo vive acá"*) y la regla de oro de CLAUDE.md (test con valor calculado a mano).
Es una fórmula financiera fuera del lugar donde el proyecto se protege a sí mismo, y por eso
nadie —ni una corrida de test— la ejecutó antes de este informe (ver A-19).

**Fix propuesto** (no aplicado): mover el cálculo a `core/`, con dos opciones equivalentes
en el resultado: `(1+nominal)/(1+real) - 1`, o más directo, `coeficiente_ajuste(fecha_0,
fecha_1) - 1` (ya testeado en `tests/deflactor/test_constante.py`, sin necesidad de derivar
la inflación por división inversa de dos números que ya salieron del IPC). Testear contra
el mismo caso de `test_real.py`, y quitar el
`max(...,0.0)` (o dejarlo solo para el caso —raro pero real— de deflación).

---

#### A-2 — La integración con Gemini nunca se ejecutó contra la API real

**Dónde**: `core/extraccion/gemini.py` completo; `tests/extraccion/test_gemini.py`.

**Evidencia**: el único test marcado `red_real` en todo el repo es
`tests/macro/test_ipc.py::test_api_datos_gob_responde` (heredado de Consultora, contra
datos.gob.ar). `grep -rn red_real tests/` no encuentra ningún test de extracción. La suite
de `test_gemini.py` solo prueba: (a) que sin API key se lanza `ExtraccionError`, y (b) que
el JSON Schema tiene los campos esperados — ninguno de los dos llama a `generate_content`.

La cobertura de línea lo confirma: `core/extraccion/gemini.py` tiene **46% de cobertura**,
y las líneas sin cubrir (78-105) son exactamente la llamada real a la API, el manejo de la
respuesta y el `json.loads` del texto devuelto.

**Por qué importa más de lo habitual**: es el único camino de entrada de datos de toda la
herramienta. El ADR-001 del propio repo advierte "al implementar la llamada a Gemini, leer
la documentación vigente y no la memoria del modelo — la forma de esa API viene cambiando",
y el código nunca se validó contra la API real después de escribirse. No hay ninguna
garantía hoy de que:
- el modelo devuelva `periodo_desde`/`periodo_hasta`/fechas en formato ISO como pide el
  prompt (sin validación de formato en ningún punto del código — ver A-11),
- el `response_json_schema` con el formato que usa el código sea aceptado por la versión
  actual del SDK `google-genai` (la firma exacta de `generate_content` cambió más de una vez
  según la propia investigación de Klericó),
- el modelo realmente separe impuestos y recargos de los conceptos como se le pide en el
  prompt (`core/extraccion/gemini.py:40-52`), que es la distinción de la que depende toda la
  homologación y las alertas de recargo.

**No pude correr este pase yo mismo**: no tengo la `GEMINI_API_KEY` en este entorno de
auditoría (solo vive en los secrets de Streamlit Cloud). Queda como el primer paso
obligatorio antes de confiar en cualquier factura real procesada.

**Fix propuesto**: un script chico (`scripts/probar_extraccion.py`, en el espíritu de
`scripts/test-invoice-extraction.ts` de Klericó) que corra la extracción contra una factura
real de muestra y muestre el JSON crudo devuelto, para comparar campo por campo contra lo
que el código espera — antes de cargar ninguna factura por el tablero.

---

#### A-3 — La homologación de conceptos absorbe cargos que deberían alertar

**Dónde**: `core/analisis/homologacion.py` (`UMBRAL_COINCIDENCIA = 0.45`);
`data/conceptos/general.yaml`.

**Evidencia reproducible** (corrido contra el diccionario real del repo):

| Score | Descripción real de factura | Resultado |
|---|---|---|
| 0.571 | `"Recargo por reconexión"` | **absorbido** como `cargo_fijo` |
| 0.558 | `"Servicio de asistencia domiciliaria"` | **absorbido** como `consumo_agua` |
| 0.435 | `"Cargo por gestión administrativa"` | alerta (por poco) |
| 0.412 | `"Alquiler de equipo decodificador"` | alerta |

El umbral de similitud de bigramas (0,45, heredado sin cambios de `match.ts` de Klericó,
donde se usa para emparejar descripciones de MERCADERÍA contra un catálogo — un problema
distinto) es demasiado permisivo para descripciones cortas en español, donde compartir un
puñado de bigramas comunes (`"cargo "`, `" de "`, `"recon..."`) alcanza para superar 0,45
sin que las palabras signifiquen lo mismo.

**Impacto**: el caso de uso explícito de la herramienta —"un recargo por mora que no
debería existir"— puede colarse homologado como un concepto sano si su descripción se
parece lo suficiente a otra cosa, en vez de generar la alerta de "concepto nuevo sin
clasificar" que el diseño promete.

**Fix propuesto**: subir el umbral (probar con facturas reales una vez resuelto A-2 qué
umbral separa bien los casos reales) y/o exigir que la homologación considere el `servicio`
de la factura, no solo el texto — hoy `homologar_concepto` no recibe el servicio, así que
compite `consumo_agua` contra `consumo_gas` en igualdad de condiciones aunque la factura ya
sepa que es de telefonía.

---

#### A-20 — Plata desaparece del total mostrado si la cantidad agregada de un concepto da cero

**Dónde**: `core/analisis/agregacion.py:44` (hallazgo del subagente `revisor-financiero`,
verificado con el código real).

```python
precio_unitario = importe_total / cantidad_total if cantidad_total != 0 else 0.0
```

El guard evita la división por cero, pero el importe correspondiente **no se reasigna a
ningún lado — se evapora**, rompiendo la identidad `cantidad × precio == importe` que el
resto del sistema asume (`total_0`/`total_1` son propiedades derivadas de esa
multiplicación).

**Evidencia reproducible**:
```python
filas = [
    FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=0, importe=5000.0),   # nota de crédito
    FilaConcepto("abono_movil", "Abono",     cantidad=4, importe=10000.0),
]
agregar_conceptos(filas)
# -> {'cargo_fijo': (0.0, 0.0), 'abono_movil': (4.0, 2500.0)}
# total mostrado: $10.000  |  importe real sumado en las facturas: $15.000
```

Ese total incorrecto es el que sale en `col_a.metric("Total período base")`
(`evolucion.py:83`), en el Excel (`core/reportes/excel.py:149-154`) y alimenta
`variacion_real`. Además, un concepto con `total_0 == 0` por este motivo dispara una
**alerta falsa de "concepto nuevo"** (`alertas.py:63`) aunque el concepto haya estado en
ambos períodos.

**Cuándo puede pasar en la práctica**: dos líneas del mismo concepto normalizado con
cantidades que se cancelan — una línea normal (`+4 / $10.000`) y un ajuste o nota de crédito
con signo negativo (`-4 / -$10.000`), o cualquier refacturación. `validacion.py` valida
`cantidad × precio ≈ importe` **por línea**, no por agregado — cada línea individual puede
validar perfectamente y aun así el agregado pierde plata. Una asimetría entre las dos
líneas (`+4/$10.000` y `-4/-$8.000`) deja $2.000 huérfanos sin que ningún control lo note.

**Fix propuesto**: decidir explícitamente qué hacer cuando `cantidad_total == 0` pero
`importe_total != 0` — por ejemplo, tratarlo como un concepto "sin cantidad" (análogo al
`q=1` de un cargo fijo) en vez de descartar el importe, o levantar una alerta específica de
"concepto con cantidad neta cero pero importe distinto de cero" en vez de dejarlo pasar en
silencio.

---

#### A-21 — El test que debería cubrir A-20 da falsa tranquilidad

**Dónde**: `tests/analisis/test_agregacion.py:40-43` (hallazgo del subagente).

```python
def test_cantidad_total_cero_no_divide_por_cero():
    filas = [FilaConcepto("cargo_fijo", "Cargo fijo", cantidad=0, importe=0.0)]
    resultado = agregar_conceptos(filas)
    assert resultado["cargo_fijo"] == (0.0, 0.0)
```

El nombre promete cubrir el caso borde de división por cero, pero elige `importe=0.0` —
justo el único valor con el que el guard de A-20 es inocuo (no hay plata que perder). Con
`importe=5000.0` el test seguiría "pasando" (no explota) y sin embargo el resultado sería
contablemente falso. Es cobertura aparente sobre un cálculo que llega al cliente, no
cobertura real — hace que alguien lea el nombre del test, lo dé por cubierto, y no mire más.

**Fix propuesto**: agregar el caso `cantidad_total=0, importe_total != 0`, con una decisión
explícita (assert de qué comportamiento se espera) en vez de solo verificar que no explota.

---

### Altos — la herramienta afirma proteger algo que hoy no protege

#### A-4 — Los controles de validación se auto-cumplen si el modelo omite subtotal/total

**Dónde**: `core/extraccion/validacion.py::validar_factura`, líneas donde
`subtotal_referencia = factura.subtotal if factura.subtotal is not None else suma_conceptos`
y lo mismo para `total_referencia`.

**Evidencia reproducible**:
```python
factura = FacturaExtraida(
    ..., conceptos=[Concepto('Abono', 4, 'línea', 999999.0, 3999996.0)],  # precio absurdo
    subtotal=None, total=None,
)
r = validar_factura(factura)
# r.subtotal_ok == True, r.total_ok == True, r.factura_valida == True
```

Un precio unitario de casi un millón de pesos pasa **todos** los controles con
`factura_valida=True` si el modelo simplemente no devolvió `subtotal` ni `total` —el
sistema de referencia se compara contra sí mismo. Esto es lo opuesto de lo que el diseño
promete: "si un dato no está en la factura, usá null en vez de inventarlo" (instrucción al
modelo en `gemini.py:47`) combinado con "si algún control no cierra, va a cuarentena" tiene
un agujero exactamente en la intersección de ambas reglas — un `null` en el campo que se usa
de referencia neutraliza el control en vez de dispararlo.

**Impacto directo en A-2**: si el modelo real, en algún porcentaje de facturas, omite
`total` (razonable si la factura no lo imprime de forma clara, o si el modelo decide no
inventar un valor que no ve bien), esas facturas entran al análisis sin ningún control real.

**Fix propuesto**: si `factura.subtotal is None` o `factura.total is None`, la factura va a
cuarentena directamente con el motivo "el modelo no pudo leer el subtotal/total" — nunca se
debe usar el propio cálculo como su referencia de validación.

---

#### A-5 — La doble lectura del total se saltea sin avisar, y en un caso da un valor incorrecto en silencio

**Dónde**: `core/ingesta/pdf_texto.py::total_impreso`,
`_PATRON_TOTAL = re.compile(r"(?:TOTAL(?:\s+A\s+PAGAR)?)\s*:?\s*\$?\s*([\d.]+,\d{2})", ...)`.

**Evidencia reproducible** (formatos de total que un ERP o proveedor real puede imprimir):

| Texto de la factura | `total_impreso()` devuelve | Correcto |
|---|---|---|
| `TOTAL A PAGAR: $ 12.584,00` | `12584.0` | ✅ |
| `TOTAL A PAGAR: $12584` (sin decimales) | `None` | control se saltea |
| `TOTAL A PAGAR: 12584.00` (punto decimal) | `None` | control se saltea |
| **`Total a Pagar $ 12,584.00`** (formato US: coma de miles, punto decimal) | **`12.58`** | ❌ **valor incorrecto, no None** |

El tercer caso (formato estadounidense, que genera cualquier ERP configurado en inglés o
integrado con un sistema extranjero) no devuelve `None`: la regex matchea parcialmente
`"12,58"` de `"12,584.00"` y devuelve **$12,58** en vez de $12.584,00 — un error de más de
cuatro órdenes de magnitud, silencioso, sin ninguna excepción ni log.

**Impacto**: en la práctica esto es defensivo por casualidad — un total mal parseado a
$12,58 casi nunca va a coincidir con el total real dentro de la tolerancia del 2%, así que
`total_impreso_ok` da `False` y la factura va a cuarentena. Pero el motivo que se le muestra
al usuario ("el total extraído no coincide con el total impreso en el PDF") es engañoso: el
problema no es que el modelo se haya equivocado, es que **nuestra propia regex** leyó mal el
PDF. Alguien va a perder tiempo desconfiando del modelo cuando el bug está acá.

**Fix propuesto**: soportar explícitamente el formato con punto decimal (factura sin
separador de miles) y detectar el formato US vs. AR antes de parsear (o, más simple, si el
patrón no matchea el formato esperado exacto, no intentar una interpretación parcial —
devolver `None` en vez de un número construido con una coincidencia parcial de regex).

---

#### A-6 — La alerta de ítem duplicado nunca se dispara en producción, y el comentario que dice lo contrario es falso

**Dónde**: `apps/segurplus/paginas/evolucion.py:126-135`;
`core/analisis/alertas.py::alertas_por_item_duplicado`; `core/pipeline.py` completo.

El comentario en `evolucion.py` dice:
> "El chequeo de ítem duplicado (...) corre por factura individual dentro de
> `core.pipeline.procesar_pdf` en su momento -- no aplica a esta vista agregada."

**Esto es falso.** `grep -rn alertas_por_item_duplicado core/pipeline.py` no devuelve nada
— `procesar_pdf` nunca la llama. La única invocación real de
`alertas_por_item_duplicado` en todo el flujo de producción es dentro de
`generar_alertas(factura_agregada, ...)` en `evolucion.py:147`, con una `factura_agregada`
construida sin `conceptos=[...]` (queda con la lista vacía por defecto de la dataclass) —
así que la llamada corre, pero sobre una factura sin ítems, y siempre devuelve `[]`.

**Impacto**: la alerta de "ítem duplicado dentro de la misma factura" —listada
explícitamente en el PDF entregado a la jefa como funcionalidad ya construida— no se
dispara nunca, en ningún escenario, con el código actual.

**Fix propuesto**: llamar `alertas_por_item_duplicado(factura)` dentro de `procesar_pdf`
inmediatamente después de que la factura pasa la validación (antes de guardarla), y
persistir esas alertas junto con la factura (hoy no hay ninguna tabla para alertas
generadas en el momento de la carga — todo se recalcula al vuelo en `evolucion.py`, que es
por lo que el chequeo por-factura no tiene dónde vivir hoy).

---

#### A-7 — El tope de llamadas por hora está declarado pero nunca se aplica

**Dónde**: `core/extraccion/gemini.py:33`, `MAX_LLAMADAS_POR_HORA = 30`.

El docstring del módulo (líneas 15-19) presenta esto como una protección deliberada,
portada de Klericó: *"Tope de llamadas por hora: sin esto, un bucle (...) podría agotar la
cuota gratuita sin ningún freno."* La constante existe; el freno no. `grep -rn
MAX_LLAMADAS_POR_HORA .` solo encuentra la línea donde se define.

**Impacto**: si alguien sube por error un lote grande, o si un bug hace que `cargar.py`
reintente en bucle, no hay nada en el código que lo frene antes de agotar la cuota gratuita
del día — justo el escenario que el docstring dice estar previniendo.

**Fix propuesto**: contar llamadas en la última hora contra `core/almacenamiento.py`
(cuántas facturas se guardaron/enviaron a cuarentena en los últimos 60 minutos) y rechazar
nuevas llamadas por encima del tope, igual que hace `app/api/invoices/parse/route.ts` en
Klericó contra su tabla `purchases`.

---

#### A-8 — El login de la app falla abierto, no cerrado

**Dónde**: `apps/segurplus/secretos.py::leer_secret`,
`apps/segurplus/autenticacion.py::requerir_contrasena`.

```python
def leer_secret(clave: str) -> str | None:
    try:
        return st.secrets.get(clave)
    except Exception:
        return None
```
```python
contrasena_esperada = leer_secret("APP_PASSWORD")
if not contrasena_esperada:
    st.session_state["autenticado"] = True   # <- entra sin pedir nada
    return
```

Cualquier excepción al leer los secrets (no solo "no existe ningún `secrets.toml`", el caso
para el que se escribió esto — también un timeout transitorio leyendo el secret store de
Streamlit Cloud, o cualquier otro error) se interpreta como "no hay contraseña configurada"
y dejar pasar. Es el mismo patrón de "fallar abierto" que Consultora usa deliberadamente en
`guard_clientes.py` — pero ahí el costo de un falso positivo (bloquear un flujo de trabajo
legítimo) es alto y el costo de un falso negativo (dejar pasar un commit indebido) lo cubre
una segunda capa (el pre-commit real de git). Acá no hay segunda capa: si el login falla
abierto, la app queda abierta a cualquiera con el link, sin nada más atrás.

**Fix propuesto**: distinguir "no hay ningún secret configurado en absoluto" (que sí debe
dejar pasar, para no romper el desarrollo local) de cualquier otro error al leerlo (que
debe bloquear con un mensaje de "error de configuración, contactar al administrador" en vez
de dejar entrar).

---

#### A-9 — Los PDFs de facturas reales quedan en el disco del servidor sin borrarse

**Dónde**: `apps/segurplus/paginas/cargar.py:41-44`.

```python
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
    tmp.write(archivo.getvalue())
    ruta_temporal = Path(tmp.name)
resultado = procesar_pdf(ruta_temporal, con, api_key=api_key)
```

`delete=False` es necesario para que el archivo exista cuando `procesar_pdf` lo abre
(en Windows no se puede tener el archivo abierto dos veces a la vez), pero después de usarlo
nunca se borra. Cada factura subida dumpea un PDF completo en `/tmp` del servidor de
Streamlit Cloud (compartido, aunque efímero entre reinicios) y se queda ahí.

**Fix propuesto**: `ruta_temporal.unlink(missing_ok=True)` en un `finally` después de
llamar a `procesar_pdf`.

---

### Medios

#### A-10 — CI nunca corrió; todo se desarrolló y testeó en Python 3.11 contra un `requires-python = ">=3.12"`

`.github/workflows/ci.yml` dispara solo en push/PR hacia `main`; todo el trabajo vive en
`claude/invoice-analysis-automation-7axk9u` sin PR abierto — **CI nunca ejecutó ni una vez**.
Verificado en esta auditoría, corriendo por primera vez en un entorno con Python 3.12 real:
la suite completa pasa igual (88 passed, 1 skipped) y `ruff check .` no encuentra nada — así
que no había una incompatibilidad de versión escondida, pero **no había forma de saberlo
antes de esta auditoría**, y sigue sin haber ninguna corrida de CI real en la rama activa.

#### A-11 — Fechas del modelo sin validar ni normalizar

`core/extraccion/esquema.py::factura_desde_json` toma `periodo_desde`, `periodo_hasta`,
`fecha_emision`, `fecha_vencimiento` directo del JSON del modelo (`datos.get(...)`) sin
parsear ni validar que sea `YYYY-MM-DD` como pide el prompt. `evolucion.py` después hace
`date.fromisoformat(periodo_0)` — si el modelo alguna vez devuelve `"05/07/2026"` en vez de
`"2026-07-05"` (algo que un LLM puede hacer, sobre todo en una factura donde la fecha
aparece en formato argentino en el texto), esa línea lanza `ValueError`, que cae en el
`except Exception` genérico de la línea 96 y se muestra como "no se pudo calcular la
variación real" — un mensaje que no dice que el problema es un formato de fecha.

Además: `periodos = [... ORDER BY 1]` en `evolucion.py` ordena `periodo_desde` como
**string**, no como fecha — funciona hoy porque ISO ordena igual como string que como
fecha, pero se rompe en silencio si alguna fecha llega en otro formato (ver arriba).

#### A-12 — `except Exception` genérico que tapa cualquier error, no solo el esperado

`evolucion.py:96`, ya citado en A-1 y A-11: el mismo `try/except Exception` cubre tres
fallas completamente distintas (rango de IPC no disponible, fecha mal formada, cualquier
otro bug futuro en `variacion_real`) con el mismo mensaje genérico de "sin datos de IPC
para ese rango" — que en dos de los tres casos es directamente falso.

#### A-13 — `dias_tolerancia_periodo` declarado y sin una sola línea de código que lo lea

`data/alertas.yaml:21` define el umbral para la alerta de "período faltante o factura fuera
del rango de días esperado" que `docs/PLAN.md` promete en la lista de alertas. `grep -rn
dias_tolerancia_periodo --include=*.py .` no encuentra nada — la alerta no existe.

#### A-14 — El campo `consumos` que describe el esquema en `docs/PLAN.md` no existe en el código

`docs/PLAN.md` describe el esquema canónico con un campo separado
`consumos: [ {magnitud, cantidad, unidad} ]` para kWh/m³/GB/minutos. `core/extraccion/esquema.py`
no tiene ningún campo `consumos` — los consumos medidos viven mezclados dentro de
`conceptos` (que sí tiene `cantidad` y `unidad`). Puede ser una simplificación deliberada
que quedó sin actualizar en el plan, pero el plan es lo que alguien nuevo va a leer primero.

#### A-15 — Consideraciones de infraestructura no resueltas

- **DuckDB con dos usuarios simultáneos**: `core/almacenamiento.py::conectar` abre una
  conexión nueva por request; DuckDB no está pensado para escrituras concurrentes desde
  múltiples procesos sobre el mismo archivo. Con un solo usuario a la vez (el uso esperado
  hoy) no es un problema; con dos personas cargando facturas al mismo tiempo, puede fallar
  o corromper. Ya documentado como riesgo de disco efímero en ADR-002, pero la concurrencia
  no está ni documentada ni mitigada.
- `leer_ipc()` puede salir a la red (`descargar_ipc`) en cada rerun de la página de
  evolución si el parquet cacheado no existe en el filesystem efímero del servidor — no hay
  `@st.cache_data` sobre esa llamada.

#### A-16 — Se suman cantidades de distinta unidad bajo un mismo concepto normalizado

**Severidad revisada a ALTA** tras el pase del subagente `revisor-financiero`, que aportó
evidencia concreta de que el problema es estructural, no solo un caso borde teórico.

**Dónde**: `core/analisis/agregacion.py::agregar_conceptos` suma `cantidad` de todas las
filas con el mismo `concepto_normalizado` sin verificar que compartan `unidad`. El problema
es más profundo de lo que parece a primera vista: **`FilaConcepto` (la estructura de datos
misma) no tiene campo `unidad`** — se descarta antes de llegar a la función. El `SELECT` de
`_filas_del_periodo` en `evolucion.py:64-67` ni siquiera pide la columna `unidad` a la base,
aunque `Concepto` sí la tiene (`esquema.py:26`) y `almacenamiento.py:48` sí la persiste. La
unidad se pierde por el camino antes de que `agregar_conceptos` tenga la chance de
verificarla.

Con el umbral de homologación de 0,45 (ver A-3), dos descripciones bien distintas caen bajo
el mismo `concepto_normalizado` con facilidad:

```python
[FilaConcepto('consumo', 'Consumo energia kWh', cantidad=500, importe=50000),
 FilaConcepto('consumo', 'Consumo datos GB',    cantidad=20,  importe=4000)]
-> {'consumo': (520.0, 103.85)}
```

520 "unidades" que son en realidad kWh + GB, y un "precio unitario promedio" de $103,85 por
una unidad que no existe. Ese número contamina, río abajo: `efecto_precio` en la
descomposición se vuelve interpretable solo por casualidad, y tanto
`alertas_por_precio_sobre_ipc` como `alertas_por_salto_de_cantidad` pueden reportar un
cambio de precio o de cantidad que en realidad es solo un cambio en el **mix** entre dos
magnitudes distintas — el equivalente de sumar stocks con flujos.

La docstring de `agregar_conceptos` justifica el uso del promedio ponderado ("no el
promedio simple, que distorsionaría") pero nunca menciona el supuesto del que depende esa
justificación: que todas las filas agregadas compartan unidad. Sin ese supuesto explícito,
ni documentado ni verificado, el promedio ponderado "correcto" es tan sin sentido como el
simple que reemplaza.

**Fix propuesto**: agrupar por `(concepto_normalizado, unidad)` en vez de solo
`concepto_normalizado`, o al menos generar una alerta cuando un mismo concepto trae
unidades mixtas dentro del mismo período.

#### A-17 — Una factura en cuarentena no tiene forma de reintentarse

`factura_ya_procesada` chequea tanto `facturas` como `cuarentena` por `hash_pdf`. Si un PDF
va a cuarentena y después se corrige algo del lado de la extracción (ej. se sube el umbral
de A-3, o se arregla A-5), volver a subir **el mismo PDF** no hace nada — sigue "ya
procesado". No hay ningún comando ni botón para vaciar una entrada de cuarentena y
reintentarla.

#### A-18 — Un PDF corrupto (no solo "sin capa de texto") tumba el lote de carga completo

`core/pipeline.py::procesar_pdf` solo atrapa `PdfSinTextoError` alrededor de
`extraer_texto`. Un PDF truncado o corrupto (no un escaneo sin texto, sino un archivo
inválido) hace que `pdfplumber`/`pdfminer` lancen otra excepción
(`PdfminerException: No /Root object! - Is this really a PDF?`, verificado en esta
auditoría con un archivo de texto plano renombrado a `.pdf`). `cargar.py` no tiene ningún
`try/except` alrededor del loop que llama a `procesar_pdf` — esa excepción no capturada
tumba la página de Streamlit a mitad del lote, perdiendo el resumen de qué se procesó antes
del PDF corrupto (aunque lo ya guardado en la base sigue ahí, el usuario ve un error crudo
de Streamlit sin ningún contexto).

#### A-19 — Dos de las tres páginas del tablero nunca se ejecutaron en un test

Medido con cobertura de línea (`pytest-cov`, instalado puntualmente para esta auditoría —
no está en el stack cerrado, no se agregó al repo):

| Archivo | Cobertura |
|---|---|
| `apps/segurplus/paginas/cargar.py` | 33% (solo las líneas antes del botón "Procesar") |
| `apps/segurplus/paginas/evolucion.py` | **no aparece en el reporte — nunca se importó/ejecutó en ningún test** |
| `apps/segurplus/paginas/cuarentena.py` | **ídem, 0 tests** |
| `core/extraccion/gemini.py` | 46% (la llamada real a la API, sin cubrir — ver A-2) |
| `core/extraccion/esquema.py` | 91% — la función `factura_desde_json`, que convierte la respuesta cruda del modelo al esquema interno, no tiene ningún test directo (los tests de pipeline construyen `FacturaExtraida` a mano, sin pasar por esta función) |
| **Total del repo** | **91%** |

El 91% total esconde que justamente los dos módulos que tocan el mundo real sin filtro —la
llamada a Gemini y la página que muestra el resultado del análisis— son los menos
verificados. El bug de A-1 vive en un archivo con 0% de cobertura; nadie, ni una corrida
automática, ejecutó nunca esa fórmula antes de este informe.

---

#### A-22 — La alerta de precio sobre IPC resta porcentajes en vez de deflactar (aproximación lineal)

**Dónde**: `core/analisis/alertas.py:119`, `exceso_pp = (variacion_precio_pct -
ipc_periodo_pct) * 100`.

Es la aproximación lineal `n - π`, no la variación real `(1+n)/(1+π) - 1`. La docstring
dice "puntos porcentuales por encima de la inflación", así que el código hace lo que dice
literalmente — pero con inflación alta la diferencia entre ambas definiciones deja de ser
despreciable y contradice el espíritu de `core/deflactor/` (que existe justamente para no
aproximar linealmente). Ejemplo con el umbral real de `data/alertas.yaml` (5,0 pp):
precio +55%, IPC +50% → `exceso_pp = 5,0` → dispara alerta alta; pero la variación real es
`1,55/1,50 - 1 = +3,33%`, por debajo del umbral — alerta espuria.

No es un error de signo, y con inflación baja es indistinguible de la fórmula correcta —
pero conviene decidir explícitamente cuál definición se usa (documentarla en la docstring y
en el mensaje al usuario) en vez de que quede ambigua. Los tests actuales
(`test_alertas.py:90-101`) usan números redondos que pasan igual con cualquiera de las dos
definiciones, así que no discriminan cuál está implementada.

---

#### A-23 — El delta de "Total período comparado" mezcla pesos nominales con la columna de al lado, sin rotular

**Dónde**: `apps/segurplus/paginas/evolucion.py:84`,
`col_b.metric("Total período comparado", f"${total_1:,.2f}", delta=f"{total_1 -
total_0:+,.2f}")`.

El delta es una diferencia en pesos **nominales** de dos fechas distintas, mostrada en la
columna contigua a "Variación real (descontado el IPC)". El cálculo en sí no está mal, pero
mezcla unidades a la vista del usuario (pesos corrientes vs. constantes) sin rotularlo —
alguien puede leer las dos columnas como si fueran comparables. Bajo impacto, fácil de
arreglar con un cambio de texto ("variación nominal").

---

## Verificación de fórmulas financieras (subagente `revisor-financiero`)

Corrida completa, independiente de esta auditoría, sobre `core/analisis/variacion.py`,
`core/analisis/real.py`, `core/analisis/agregacion.py`, `core/deflactor/constante.py` y
`apps/segurplus/paginas/evolucion.py`. Sus hallazgos ya están integrados arriba como A-1
(confirmación con evidencia adicional), A-20, A-21, A-16 (evidencia adicional) y A-22/A-23.
Lo que sigue es lo que el subagente verificó como **correcto**, que no está en la lista de
hallazgos porque no lo es:

- **`core/analisis/variacion.py`** — descomposición correcta. La identidad
  `efecto_cantidad + efecto_precio + efecto_cruzado == variación_total` es exacta por
  construcción y está bien testeada (`test_variacion.py:31-46`, seis casos incluyendo
  aparición y desaparición de conceptos). Signos verificados a mano contra el caso Movistar
  (4×2500 → 6×2800: 5.000 + 1.200 + 600 = 6.800 = 16.800 − 10.000). El caso "desaparece" da
  correctamente −500. Sin objeciones.
- **`core/deflactor/constante.py`** — el coeficiente `IPC(destino)/IPC(origen)` está en el
  sentido correcto. Tests con valor conocido a mano, test de inverso, test de normalización
  de día del mes, error explícito cuando falta el IPC. Sin objeciones.
- **`core/analisis/real.py`** — pasa por `a_pesos_constantes` antes de comparar, como exige
  CLAUDE.md; error explícito con `importe_0 == 0` en vez de dividir por cero; test con el
  valor a mano correcto. El problema de A-1 es de quien *consume* esta función
  (`evolucion.py`), no de la función en sí.
- Los `continue` de `alertas_por_salto_de_cantidad` y `alertas_por_precio_sobre_ipc` cuando
  `cantidad_0 == 0` / `precio_0 == 0` son correctos y no dejan un hueco: ese caso lo cubre
  `alertas_por_concepto_nuevo_o_desaparecido`, y combina bien con la sustitución
  `p0 = p1` de `variacion.py`.
- **Ningún otro cálculo financiero fuera de `core/`**: el subagente barrió `apps/` completo
  buscando aritmética; lo único que aparece además del bloque de `evolucion.py` (A-1) es
  `barra.progress((i + 1) / len(archivos))` en `cargar.py`, que es una barra de progreso.
- Ningún parámetro fiscal ni umbral hardcodeado en los módulos auditados — cumple CLAUDE.md.
  (`UMBRAL_COINCIDENCIA = 0.45` en `homologacion.py` está en código, pero es un parámetro
  de algoritmo, no fiscal — fuera del alcance de esa regla, y ya cubierto por A-3.)
- Ningún error de signo en ninguna fórmula de `core/`.

---

## Hallazgos nuevos, encontrados durante la corrección del Bloque 3

El subagente `revisor-financiero`, al revisar el fix de A-20/A-16, encontró un problema
real introducido por ese mismo fix y dos deudas preexistentes. El primero se corrigió en el
momento (mismo commit); los otros dos quedan anotados para un bloque futuro.

#### A-20-bis — corregido en el momento: salto de cantidad espurio sobre la cantidad sintética

El `(1.0, importe_total)` que `agregar_conceptos` devuelve para preservar la identidad
contable cuando la cantidad neta da cero (ver A-20) no es una cantidad real. Comparado
contra la cantidad real de otro período disparaba `alertas_por_salto_de_cantidad` con un
"+300%" artificial. Se agregó `conceptos_con_cantidad_sintetica` a
`alertas_por_salto_de_cantidad`/`generar_alertas`, poblado con
`conceptos_con_cantidad_neta_cero` de ambos períodos, y tests que reproducen exactamente el
caso que encontró el subagente.

#### A-24 — cantidad neta negativa (no cero) sin decisión explícita ni detección

Si una nota de crédito es MÁS GRANDE que el cargo original del mismo período (ej. `+4/
$10.000` y `-6/-$15.000`), `agregar_conceptos` da `(-2.0, 2500.0)` — la identidad matemática
cierra, pero es un resultado raro de mostrar (cantidad negativa), y
`conceptos_con_cantidad_neta_cero` no lo detecta (solo busca `== 0`), así que no dispara el
`st.warning`. Comparado contra un período con cantidad positiva, `alertas_por_salto_de_cantidad`
calcula la variación con un denominador negativo y el signo del mensaje queda invertido
("-300%" para lo que en realidad es un aumento). No es una regresión de este bloque —esa
rama de `alertas.py` no se tocó—, pero es de la misma familia que A-20. Queda pendiente.

#### A-25 — la unidad no se normaliza antes de agrupar

`core/extraccion/esquema.py::factura_desde_json` toma `unidad` literal del JSON del modelo
sin normalizar mayúsculas ni espacios. Con `(concepto, unidad)` como clave de agrupación
(fix de A-16), si el mismo concepto llega con `"kWh"` en un período y `"KWH"` o `" kWh "` en
otro, se generan dos claves de texto distintas (`"consumo [kWh]"` vs `"consumo [KWH]"`), y
la comparación entre períodos los trata como concepto nuevo/desaparecido en vez de la misma
serie — falsa alerta. A confirmar con facturas reales (Bloque 9) si el modelo es consistente
en el formato de unidad o si hace falta normalizar (`.strip().lower()` como mínimo) antes de
agrupar.

#### A-26 — `_parsear_monto` no valida grupos de miles inválidos cuando hay coma y punto mezclados

Encontrado por el `revisor-financiero` al revisar el fix de A-5. Cuando el token tiene coma
Y punto, la función decide el separador decimal por posición (`rfind`) sin validar que los
grupos no-decimales tengan 3 dígitos, a diferencia de las ramas de un solo separador (que sí
validan longitud de grupo). Ejemplos reproducidos a mano:

```python
_parsear_monto("1.2,34")   # -> 12.34   (trata "1.2" como miles válidos; no lo son)
_parsear_monto("1,2,345")  # -> 12345.0 (trata "1,2," como miles válidos; no lo son)
```

En una factura real esto exigiría un agrupamiento de miles ya inválido en el texto (poco
probable con un ERP bien configurado), y no es exactamente el caso que A-5 buscaba cerrar
(una lectura *parcial*) sino un grupo de miles *inválido* tratado como válido sin control.
Severidad baja. A confirmar si aparece en facturas reales (Bloque 9); si no aparece nunca,
no vale la pena endurecer la validación a costa de legibilidad.

#### A-27 — la alerta de período faltante asume periodicidad mensual para cualquier servicio

Encontrado por el `revisor-financiero` al revisar el Bloque 5 (A-6/A-13).
`alertas_por_periodo_faltante` compara cada período contra "un mes después del anterior" de
forma fija. Reproducido a mano:

```python
bimestral = [date(2026,1,1), date(2026,3,1), date(2026,5,1), date(2026,7,1)]
alertas_por_periodo_faltante(bimestral)
# -> 3 alertas de "periodo_faltante", una por cada par consecutivo (hueco de ~60 días)
```

Un servicio con facturación bimestral real (gas residencial, algunos casos de energía en
Argentina —ambos explícitamente contemplados por esta herramienta—) dispara esta alerta en
el 100% de las comparaciones, siempre, aunque nunca falte nada. No es un caso raro: es el
patrón normal de facturación de al menos uno de los servicios que la herramienta target.

**Por qué no se resuelve en el momento**: hacerlo bien (inferir la cadencia real de cada
servicio, o parametrizarla en `data/parametros/*.yaml` como pide CLAUDE.md) necesita ver el
patrón real de facturación de un proveedor de verdad — con solo 2-3 períodos sintéticos no
hay forma de distinguir "bimestral consistente" de "se saltearon un mes". Se calibra en el
Bloque 9, con facturas reales. Mientras tanto, la limitación queda documentada en el
docstring de la función: para un servicio bimestral, esta alerta específica no es confiable.

---

## Sospechas NO confirmadas

Estas ideas surgieron durante la auditoría pero no se verificaron con evidencia reproducible
— quedan anotadas para no perderlas, sin mezclarlas con los hallazgos de arriba.

- **Posible sensibilidad del prompt de extracción a facturas con más de una página.** El
  prompt no dice nada sobre facturas multipágina, y `pdf_texto.extraer_texto` concatena
  todas las páginas con `\n`, pero el PDF se manda a Gemini también completo (nativo). No se
  probó con una factura real de más de una página.
- **El manejo de `st.session_state["autenticado"]` entre pestañas/dispositivos.** No se
  verificó si el estado de sesión de Streamlit aísla correctamente a dos personas que abren
  la app desde compus distintas al mismo tiempo, o si hay alguna forma de que una sesión
  "contamine" a otra.
- **Los umbrales de `data/alertas.yaml` (5 puntos porcentuales sobre IPC, 30% de salto de
  cantidad) no se calibraron contra ningún dato real** — son valores razonables elegidos sin
  evidencia, y una vez resuelto A-1 pueden necesitar ajuste.
- **El prompt le pide al modelo `moneda`** pero ningún control de `validacion.py` verifica
  que sea `ARS` antes de comparar montos entre facturas de distintos períodos — si alguna
  factura viniera en otra moneda (poco probable en servicios locales, pero no imposible con
  un proveedor internacional), se sumaría como si fuera pesos.

---

## Qué se auditó y qué quedó fuera de alcance

**Auditado**: `core/` completo (extraccion, ingesta, analisis, almacenamiento, pipeline,
reportes, autenticacion, deflactor, macro), `apps/segurplus/` completo, `data/*.yaml`,
`docs/PLAN.md`, `docs/estado.md`, los dos ADR, `README.md`, el PDF entregado a la jefa
(contenido reconstruido de esta conversación), los tests existentes, `pyproject.toml`,
`.github/workflows/ci.yml`, cobertura de línea de toda la suite.

**Fuera de alcance** (no se pudo o no correspondía en esta fase):
- **Correr contra la API real de Gemini** (A-2) — falta la API key en este entorno. Es la
  verificación más importante que falta y debería ser lo primero del próximo paso.
- Revisión de seguridad exhaustiva (inyección, XSS, etc.) — Streamlit y sus widgets no dan
  mucha superficie para eso, y no era el foco pedido.
- Carga/estrés con volumen real de facturas.
- El código de Consultora o Klericó en sí (solo se auditó lo que Segurplus heredó o portó).

---

## Corridas de verificación de esta auditoría

Python 3.12.3 (venv aislado, primera vez que el proyecto corre en la versión que declara):

```
$ python -m pytest tests/ -q
............................................................................
........s.........
88 passed, 1 skipped in 2.51s

$ ruff check .
All checks passed!

$ pytest tests/ -q --cov=core --cov=apps --cov-report=term-missing
TOTAL   650 stmts, 59 miss, 91% cover
88 passed, 1 skipped in 5.39s
```
