# Auditoría de Segurplus — rediseño web, relato, proveedores y deploy en Render (septiembre 2026)

**Este documento solo audita. No se corrigió nada acá.** Cada hallazgo trae la evidencia
que lo reproduce; las correcciones van en un plan aparte, después de revisar esto. Misma
regla que las auditorías anteriores (`docs/auditoria-2026-09.md`, A-1 a A-28;
`docs/auditoria-2026-09-rediseno.md`, A-29 a A-48; `docs/auditoria-2026-09-piloto.md`,
A-49 a A-59; `docs/auditoria-2026-09-facturas-reales.md`, B-1 a B-6 y C-1 a C-16;
`docs/auditoria-2026-09-confirmacion.md`, D-1 a D-25). Esta serie arranca en **E-1** y
llega a **E-24**.

**Alcance**: los commits `edb24ef`..`d7811ee` — el plan de rediseño de septiembre 2026
(bloques 1 a 7) más lo que vino después con la herramienta ya desplegada:

- **Bloques 1-3** — banco de medición, capa de proveedores (`core/extraccion/proveedores/`),
  `concepto_sugerido`.
- **Bloque 4** — la app web nueva (`web/`: FastAPI + Jinja2, Subir / Revisar / Ver).
- **Bloque 5** — `core/relato.py`, el párrafo en castellano.
- **Bloque 6** — total pagable como número principal, sacar jerga, arreglos del Excel.
- **Después del plan** — `render.yaml`, la ruta temporal de purga (ya sacada), la decisión
  de dejar Groq de lado.

**Advertencia**: igual que en D, **todo el código auditado lo escribió el mismo asistente
que ahora lo audita**, en la misma sesión. Vale menos que una revisión externa. Varios de
los hallazgos altos (E-1, E-2, E-3) son justamente del bloque que se presentó como la
respuesta al pedido central del usuario ("que el resultado se entienda"), y los tests
verdes no los detectaron.

**Método**: lectura completa de `web/app.py`, `web/auth.py`, `core/relato.py`,
`core/extraccion/proveedores/*`, las plantillas de `web/templates/`, `render.yaml` y
`data/extraccion.yaml`, más **verificación ejecutando código**: los bloques de evidencia de
E-2, E-3 y E-5 son salida real de Python.

**Contexto de uso**: la herramienta ya está desplegada en Render, conectada a la base
Supabase real, con `GEMINI_API_KEY` **y `GROQ_API_KEY`** cargadas en el panel. Eso hace que
varios hallazgos (E-1, E-4, E-6) no sean teóricos: están activos en producción hoy.

---

## Resumen ejecutivo

**El relato en castellano — la pieza que responde al pedido "que se entienda" — puede
decir cosas falsas, y en producción ni siquiera es el texto determinístico el que se
muestra.**

- **E-1 — en Render, el párrafo que ve el usuario lo escribe un modelo de lenguaje sin
  ningún control de que conserve los números.** La "regla dura" de que la IA nunca produce
  un número existe solo como una instrucción en el prompt; el código devuelve lo que el
  modelo contesta. Se activa porque `GROQ_API_KEY` está cargada en Render, aunque Groq se
  haya "descartado".
- **E-2 — la plantilla determinística se contradice sola en casos normales**: dice "salió
  más caro" cuando el gasto bajó, "el gasto no cambió" justo después de "pagaste $300
  más", y "bajó un +23%".
- **E-3 — el relato atribuye a precio/cantidad un cambio que no descompuso**: la cifra es
  el total pagable (con impuestos), pero la causa se calcula solo sobre los consumos. Si
  la suba es por impuestos, el relato igual dice "casi todo es por precio".
- **E-4 — en producción la pantalla Revisar nunca muestra el PDF**: sin `EVIDENCIA_DIR` ni
  `S3_BUCKET` en Render, el PDF original no se guarda en ningún lado. "Corregir con el PDF
  al lado" — lo que el plan llamaba "lo mejor que tiene la herramienta" — no existe en la
  versión desplegada.
- **E-5 — el Excel inventa alertas de "precio por encima de la inflación"**: calcula
  siempre con inflación 0%, así que cualquier aumento igual a la inflación aparece como
  "8% más que la inflación". La pantalla, con el IPC real, no muestra esa alerta — el
  reporte que se le pasaría a un cliente dice algo distinto y falso.
- **E-6 — el pipeline de producción no reintenta nunca**: en la única corrida real contra
  Gemini, 4 de 8 llamadas devolvieron `503 high demand`. Cada una de esas, en la web, es
  un borrador vacío para cargar a mano. La cascada con reintentos (Bloque 2) existe pero no
  está conectada.

Además: credenciales de producción expuestas en texto plano (E-7), subida de facturas que
congela el servidor entero (E-8), el mensaje de confirmación que nunca se muestra (E-10), y
una primera experiencia vacía — con una sola factura cargada, "Ver" no muestra nada (E-12).

| Severidad | Hallazgos |
|---|---|
| **Alta** | E-1, E-2, E-3, E-4, E-5, E-6, E-7 |
| **Media** | E-8, E-9, E-10, E-11, E-12, E-13, E-14, E-15, E-16 |
| **Baja** | E-17, E-18, E-19, E-20, E-21, E-22, E-23, E-24 |

---

## Hallazgos altos

### E-1 — El relato que se muestra en producción lo redacta un modelo sin verificación de números

**Dónde**: `core/relato.py::redactar_con_modelo` (líneas 171-217), llamado en cada carga de
`/ver` desde `web/app.py::_analisis` (línea 844).

**Qué pasa**: si `GROQ_API_KEY` está en el entorno, el párrafo determinístico se manda a
`llama-3.3-70b-versatile` y **se devuelve lo que el modelo conteste, sin comparar nada**:

```python
texto = respuesta.json()["choices"][0]["message"]["content"].strip()
...
if not texto:
    return parrafo_determinista
return texto
```

El docstring del módulo afirma lo contrario: *"si el modelo ... devuelve algo que no se
puede verificar como una reescritura fiel, se usa el párrafo determinístico tal cual"*. No
existe ninguna verificación: ni que los mismos importes y porcentajes aparezcan en la
respuesta, ni que no aparezcan números nuevos. La "regla dura, no negociable" de CLAUDE.md
("el modelo nunca produce un número") depende solo de que el modelo obedezca el prompt.

**Por qué está activo hoy**: el usuario cargó `GROQ_API_KEY` en Render siguiendo
`render.yaml`, y después Groq se "descartó" solo en la documentación (`d7811ee`) — la
variable sigue ahí y el código la sigue usando. Además, cada visita a `/ver` hace una
llamada de red de hasta 8 segundos antes de responder.

**Agravante**: el docstring de `redactar_con_modelo` todavía dice *"Todavía no se probó
contra la API real de Groq"* — cierto, y sin embargo está sirviendo el texto de producción.

**Qué haría falta decidir**: o se saca la redacción por modelo (Groq ya no está en el
plan), o se agrega una verificación real (extraer todos los números del original y de la
respuesta y exigir que coincidan) antes de usar la respuesta.

### E-2 — La plantilla determinística del relato se contradice en casos comunes

**Dónde**: `core/relato.py::generar_relato_determinista`.

**Evidencia (salida real)**:

```
1) precio BAJA:
En agosto de 2026 pagaste $800,00 de energia, $200,00 menos que en julio de 2026 (-20%).
Casi todo el cambio es por PRECIO (90% del movimiento): consumiste una cantidad parecida,
pero salió más caro. Descontada la inflación del período (+4%), tu gasto real bajó un +23%.

2) consumos iguales, suben impuestos:
En agosto de 2026 pagaste $1.300,00 de energia, $300,00 más que en julio de 2026 (+30%).
El gasto no cambió entre los dos meses. Descontada la inflación del período (+4%), tu
gasto real subió un +25%.
```

Tres defectos distintos:

1. **Dirección fija**: la frase de precio dice siempre *"salió más caro"* y la de cantidad
   *"consumiste distinto"*, aunque el precio haya bajado. Una baja de tarifa se lee como
   aumento.
2. **"El gasto no cambió"** cuando `tipo_dominante == "sin_variacion"` — eso significa que
   **los consumos** no cambiaron, pero la frase anterior acaba de decir que se pagó $300
   más (ver E-3).
3. **Signo duplicado**: `_porcentaje(abs(...))` siempre antepone `+`, así que sale *"bajó
   un +23%"*.

Además *"consumiste una cantidad parecida"* es una inferencia que el dato no respalda: que
el precio explique el 60% del movimiento no implica que la cantidad haya sido parecida.

**Por qué no lo detectaron los tests**: `tests/test_relato.py` solo prueba subas.

### E-3 — El relato mezcla dos bases: la cifra es el total pagable, la causa es de los consumos

**Dónde**: `web/app.py::_analisis`, líneas 805 y 844-858.

`DatosRelato` recibe `total_0`/`total_1` = **total pagable** (consumos + impuestos +
recargos − créditos, Bloque 6), pero `tipo_dominante` y `proporcion_dominante` salen de
`efecto_dominante(descomposiciones)`, que solo mira **conceptos** (consumos). La oración
"Casi todo el cambio es por PRECIO" se refiere, gramaticalmente, al cambio que se acaba de
nombrar (el del total pagable). Si la factura subió $300 y $250 son un impuesto nuevo, el
relato atribuye el cambio a "precio" basándose en los $50 restantes.

Es exactamente la confusión consumos/total que el Bloque 6 quiso eliminar: se arregló el
número grande pero el relato quedó con la causa de otra base. La pantalla sí lo aclara en
el veredicto de abajo ("en los consumos"), el relato no.

### E-4 — En Render el PDF original nunca se guarda: Revisar no muestra el PDF

**Dónde**: `core/evidencia.py::guardar_pdf` (líneas 61-63), `render.yaml`.

```python
directorio = os.environ.get("EVIDENCIA_DIR")
if not directorio:
    return None
```

`render.yaml` no define `EVIDENCIA_DIR` ni `S3_BUCKET`, así que en producción
`ruta_evidencia` queda siempre `NULL` y `revisar_detalle.html` cae al texto plano extraído.
La pantalla de Revisar — pensada para corregir mirando el PDF al lado — funciona en los
tests (que no dependen de esto) pero no en la versión desplegada.

Y aunque se definiera `EVIDENCIA_DIR`, el disco de un servicio gratis de Render es efímero:
se borra en cada deploy y cada vez que el servicio se duerme (15 minutos sin tráfico). Un
borrador subido ayer perdería su PDF. La única opción durable ya prevista en el código es
un bucket S3 compatible (ADR-003), que no está configurado.

### E-5 — El Excel calcula las alertas con inflación 0% y marca aumentos normales como "sobre la inflación"

**Dónde**: `web/app.py::get_ver_excel`, línea 1018 (`ipc_periodo_pct=0.0` fijo). También
`_analisis`, línea 793, cuando el IPC no se pudo descargar.

**Evidencia (salida real)** — un cargo fijo que sube 8% con una inflación del 8%:

```
IPC=0.08: []
IPC=0.0: [('precio_sobre_ipc', '"Cargo fijo": el precio subió 8.0% -- un 8.0% más que la
inflación del período, ya descontada esta')]
```

La pantalla (con IPC real) no muestra alerta; el Excel del mismo período dice que el precio
subió 8% **por encima** de la inflación. El Excel es lo que se descarga para mandar o
discutir con un proveedor, así que es la versión más visible de un número falso.

Cuando el IPC falla en pantalla pasa lo mismo: el comentario del código reconoce que 0.0
"no es una inflación real de cero", pero igual se usa para generar la alerta, cuyo texto
afirma una comparación contra la inflación que no se hizo.

Además, el Excel arma sus alertas distinto de la pantalla: no incluye
`alertas_por_periodo_faltante` ni `conceptos_con_cantidad_sintetica`, y agrega los conceptos
sin `acumulado`. Pantalla y Excel pueden mostrar listas de alertas diferentes para la misma
comparación.

### E-6 — El pipeline de producción no reintenta: cada 503 de Gemini es un borrador vacío

**Dónde**: `core/pipeline.py::procesar_pdf` (sin cambios en el rediseño) y
`core/extraccion/proveedores/__init__.py::leer_factura_cascada` (no conectada).

En la única corrida real del banco (`docs/estado.md`, 2026-09-20), Gemini devolvió
`503 UNAVAILABLE ... high demand` en 1 de 4 llamadas de la primera pasada y en 3 de 4 de la
segunda. `procesar_pdf` llama a `extraer_con_gemini` una sola vez; ante ese error, el PDF
queda como borrador sin datos para cargar a mano. La cascada con reintento y backoff del
Bloque 2 existe, está testeada, y no la usa nadie salvo el script del banco. Con Groq
descartado, la cascada se reduce a "Gemini con reintentos" — que es justamente lo que
falta.

### E-7 — Credenciales de producción expuestas en texto plano y sin rotar

No es un defecto de código sino operativo, pero es el de mayor riesgo real:

- La `GEMINI_API_KEY`, la `GROQ_API_KEY` y la `DATABASE_URL` completa de Supabase (usuario
  `segurplus_app` y contraseña) se pegaron en la conversación de desarrollo. Se usaron solo
  como variables de entorno efímeras y no quedaron en el repo (verificado con `git grep`
  antes de cada commit), pero quedaron en el historial de la conversación.
- La contraseña de la base es una palabra de diccionario más un año — adivinable.
- Se recomendó rotarlas; no hay constancia de que se haya hecho.

Qué haría falta: rotar las tres, actualizar los valores en Render, y elegir una contraseña
de base generada al azar.

---

## Hallazgos medios

### E-8 — Subir facturas bloquea el servidor entero mientras Gemini responde

`post_subir` es `async def` pero llama a `procesar_pdf` (sincrónico, 15-35 s por factura
según el banco) directamente, sin `run_in_threadpool`. Durante ese tiempo el event loop de
uvicorn (un solo worker en `render.yaml`) no atiende ningún otro pedido: cualquier otra
persona que abra la app ve la página colgada. Subir 10 facturas juntas es una sola request
de varios minutos sin indicación de progreso, expuesta a timeouts del navegador o del
proxy de Render — y si se corta, no se sabe cuáles se procesaron.

### E-9 — El botón de Subir se habilita con una clave que el pipeline no usa

`_api_key_configurada()` considera configurado cualquier proveedor de
`data/extraccion.yaml`, incluido Groq. Pero `procesar_pdf` solo usa Gemini. Con solo
`GROQ_API_KEY` cargada, la pantalla deja subir y todas las facturas fallan. Hoy no pasa
porque Gemini también está cargada, pero la condición es incorrecta. Relacionado:
`data/extraccion.yaml` sigue listando a Groq en la cascada aunque se haya descartado.

### E-10 — El mensaje de "Factura confirmada" nunca se muestra

`post_confirmar` guarda el mensaje en una cookie `flash` (línea 570), pero **ningún código
la lee**: `grep -rn flash web/` solo encuentra esa línea. Después de confirmar, la persona
vuelve a la lista sin ninguna confirmación de que funcionó. Se pierde también la distinción
"ya impacta el análisis" vs. "queda pendiente de revisión humana".

### E-11 — La serie histórica usa consumos; el número principal usa total pagable

`totales_por_periodo` suma `conceptos.importe` (su docstring lo justifica por coherencia
con la comparación de dos puntos). Pero el Bloque 6 cambió el número principal a total
pagable, así que en la misma pantalla, para el mismo mes, aparecen dos cifras distintas:
la grande (con impuestos) y la de la serie (sin impuestos), sin aclaración en la tabla de
serie. La justificación del docstring quedó invertida.

### E-12 — Con una sola factura cargada, "Ver" no muestra nada

`get_ver` exige al menos dos períodos; con uno, muestra "Cargá al menos dos meses" y nada
más. Es la primera experiencia de cualquier usuario nuevo (y la de hoy, con la base recién
purgada). La rama del relato para "no hay período base" (`total_0 == 0`) queda
prácticamente inalcanzable. Un resumen de la única factura (total, composición, impuestos)
se podría mostrar con datos que ya existen.

### E-13 — Quedan jerga y rutas de archivo en la pantalla principal

El Bloque 6 prometía sacarlas; quedan:

- `ver.html`: *"ver `data/conceptos/{{ servicio }}.yaml`"* — una ruta de archivo del repo
  mostrada al usuario.
- El servicio se muestra como slug (`energia`, `telefonia`, sin tildes) en título, selector
  y relato ("pagaste $X de energia").
- Los períodos se muestran como ISO (`2026-08-01`) en título, selectores y la métrica
  principal ("Pagaste en 2026-08-01"), mientras el relato dice "agosto de 2026".
- Las alertas usan formato numérico inglés (`8.0%`), el resto de la pantalla usa coma.
- "Efecto combinado" sigue en la tabla principal (el plan decía sacarlo; se lo dejó con una
  explicación).

### E-14 — Las facturas de gas no cierran y no hay plan para eso

El banco midió gas_1 con 50% de conceptos correctos y aritmética que **no cierra**; es la
misma ambigüedad de layout a dos columnas ya documentada a mano. En la práctica, cada
factura de gas va a quedar como borrador para corregir a mano — con E-4, sin el PDF al lado
para hacerlo. El rediseño documentó el problema pero no propone nada (plantilla por reglas
para ese emisor, prompt específico, etc.). Además la medición es de una sola factura de gas
leída; gas_2 nunca se pudo medir.

### E-15 — Rutas que caen en error 500 en vez de un mensaje

`leer_borrador` lanza `ValueError` si el hash no es un borrador. Lo llaman sin capturar:
`get_revisar_detalle`, `post_guardar`, `post_descartar` (vía `descartar_borrador`),
`get_pdf`, y `post_confirmar` fuera del `try` interno. Casos reales: volver con el botón
"atrás" del navegador a una factura ya confirmada, doble click en "Confirmar", o dos
personas revisando la misma factura. Todos terminan en "Internal Server Error". También
`_factura_desde_form` usa `zip(..., strict=True)`: un formulario con listas de largo
distinto lanza `ValueError` fuera del manejo de errores.

### E-16 — `render.yaml` despliega producción desde una rama de trabajo

`branch: claude/invoice-analysis-automation-7axk9u`. Cuando esa rama se mergee o se borre,
Render deja de desplegar. Además el blueprint no fija el auto-deploy: el usuario tuvo que
hacer "Manual Deploy" a mano, y el último commit que saca la ruta de purga (`8d85201`)
depende de que alguien se acuerde de hacerlo. Tampoco fija la versión de Python.

---

## Hallazgos bajos

### E-17 — `SECRET_KEY` cae a una clave fija conocida, y el comentario dice lo contrario

`web/auth.py::_serializador` usa `"clave-de-desarrollo-local-no-usar-en-produccion"` si
falta `SECRET_KEY`. El comentario dice que en ese caso "cada reinicio invalida las cookies"
— falso: la clave es constante y pública en el repo, así que cualquiera podría firmar una
cookie de sesión válida. Render la genera (`generateValue: true`), así que hoy no aplica,
pero un deploy en otro lado sin esa variable quedaría abierto sin ningún aviso.

### E-18 — Login sin límite de intentos y cookie sin `Secure`

No hay freno a probar contraseñas; con una única contraseña compartida (sin usuario), es la
única barrera. La cookie de sesión no tiene `secure=True` (Render sirve HTTPS, así que el
riesgo es bajo).

### E-19 — `concepto_sugerido` se pide al modelo y se descarta

El JSON Schema obliga al modelo a proponer un concepto por línea (más tokens, más
superficie de error), `factura_desde_json` lo parsea, y después nada lo guarda ni lo usa:
no está en `guardar_factura`, ni en `confirmar_factura`, ni en la pantalla. Solo lo mide el
banco. Además el enum incluye los conceptos de **todos** los servicios, no los del servicio
de la factura: el modelo puede proponer un concepto de telefonía para una factura de gas.

### E-20 — `groq_qwen` configurado como modelo con imagen sin verificar

`data/extraccion.yaml` declara `qwen/qwen3-32b` con `acepta_imagen: true`. El plan hablaba
de un Qwen con visión; el modelo configurado parece ser de solo texto (a verificar contra
el catálogo vigente de Groq). Con Groq descartado no tiene efecto hoy, pero el día que se
retome fallaría en cada llamada. Tampoco se valida el tamaño de las imágenes renderizadas a
200 dpi contra el límite de request del proveedor.

### E-21 — `leer_factura_cascada` ignora el timeout de Gemini y reintenta errores permanentes

El `timeout_segundos` de la configuración solo se pasa al adaptador OpenAI-compatible, no a
Gemini. Y reintenta (con espera) errores que no pueden resolverse reintentando, como "falta
la clave de API".

### E-22 — `GET /ver` escribe en la base en cada visita

`_analisis` llama a `sincronizar_casos_alertas` en cada carga de la pantalla. Un GET con
efectos secundarios; además la pantalla "Casos" no se portó a la web, así que esos casos se
crean y no hay dónde gestionarlos fuera de Streamlit.

### E-23 — Documentación desactualizada respecto de lo desplegado

- `redactar_con_modelo` dice que no se probó con Groq y que hay verificación (ver E-1).
- `CLAUDE.md` sigue describiendo la cascada y pymupdf como parte del stack; el build de
  Render (`pip install -e .`) no instala el extra `vision`, y Groq quedó descartado.
- `README`/`docs/estado.md` no explican cómo desplegar ni que el auto-deploy está apagado.
- `streamlit` sigue siendo dependencia principal (el build de Render la instala aunque no
  se use).

### E-24 — Huecos de tests que dejaron pasar los altos

Los 517 tests pasan y ninguno cubre: una baja de precio en el relato (E-2), un cambio solo
de impuestos (E-3), el camino con modelo de `redactar_con_modelo` (E-1), paridad de alertas
entre pantalla y Excel (E-5), ni el comportamiento sin `EVIDENCIA_DIR` (E-4). La suite
verde se reportó como evidencia de "terminado" en cada bloque del rediseño.

---

## Lo que esta auditoría no cubrió

- `scripts/banco_extraccion.py` solo se revisó por su resultado, no línea por línea.
- No se probó la app desplegada en Render (no hay acceso desde el entorno de desarrollo);
  E-4, E-8 y E-16 se deducen del código y la configuración, no de observarlos en vivo.
- No se revisó el tablero Streamlit (`apps/segurplus/`), en proceso de reemplazo.
- El criterio 4 del plan (que alguien ajeno entienda la pantalla "Ver") sigue sin hacerse;
  E-2 y E-3 sugieren que conviene arreglarlos antes de esa prueba.
