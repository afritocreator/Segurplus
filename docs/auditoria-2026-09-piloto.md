# Auditoría de Segurplus — piloto operativo y migración a Postgres (septiembre 2026)

**Este documento solo audita. No se corrigió nada acá.** Cada hallazgo trae la evidencia que
lo reproduce; las correcciones van en un plan aparte, después de revisar esto. Misma regla
que las dos auditorías anteriores (`docs/auditoria-2026-09.md`, A-1 a A-28, y
`docs/auditoria-2026-09-rediseno.md`, A-29 a A-48), cuya numeración esta continúa: acá van
**A-49 a A-59**.

**Alcance**: los diez commits `382af54`..`9210524` (~2.800 líneas), que nadie revisó
todavía:

- **Tres de Codex**: las correcciones de A-29 a A-48, el "piloto operativo auditable"
  (estados de factura y revisión humana, PostgreSQL + S3, OIDC con roles, casos de alerta,
  impuestos/recargos/créditos separados) y `docs/investigacion-2026-09-piloto-operativo.md`.
- **Siete míos**: la integración y corrección de ese trabajo (veredicto `efecto_dominante`,
  aprobación en lote y configurable, métricas por proveedor, sincronización de casos) y la
  migración de la persistencia a Postgres.

**Advertencia sobre esta auditoría**: a diferencia de la anterior, acá **una buena parte del
código auditado lo escribí yo**. Tres de los cuatro hallazgos más graves son míos, no de
Codex: A-50 y A-51 nacen directamente del valor por defecto que elegí en el Bloque 2, y
A-52/A-53 son patrones preexistentes que la migración a Postgres —también mía— volvió
costosos. Lo digo acá arriba porque una auditoría del propio trabajo vale menos que una
externa, y conviene leerla sabiendo eso.

**Método**: lectura completa de cada archivo nuevo o modificado (no con el subagente
`Explore`, que lee extractos y sirve para localizar código, no para revisarlo), más
**verificación ejecutando código** — los bloques de evidencia de A-49, A-50, A-51 y A-52 son
salida real de Python, no interpretación de lectura.

**Motivo**: desde hoy Segurplus escribe en un PostgreSQL real (Supabase), compartido con
otros productos del usuario, y está por entrar la primera tanda de facturas reales. Varias
cosas que eran inofensivas con un archivo DuckDB local dejaron de serlo.

---

## Resumen ejecutivo

**Un hallazgo crítico y dos altos encadenados bloquean la carga de facturas reales.**

- **A-49 — un `pytest` de rutina puede escribir en la base de producción.** `conectar()`
  mira `DATABASE_URL` **antes** que el parámetro `ruta`, así que los 250+ tests que creen
  estar apuntando a un archivo temporal (`tmp_path / "test.duckdb"`) se conectan al Supabase
  real si esa variable está exportada en el entorno — y los tests insertan, actualizan y
  borran filas. Acabamos de configurar `DATABASE_URL`; exportarla localmente para probar es
  lo más natural del mundo. CLAUDE.md prohíbe explícitamente que un test toque la base real,
  y las protecciones existentes (el hook de Claude Code y el pre-commit de git) no cubren
  esta vía, porque no pasa por ningún archivo del repo.
- **A-50 + A-51 — con la configuración por defecto, una factura mal leída no tiene
  salida.** `revision_humana_obligatoria` está en `false` (el default que elegí), así que
  toda factura nace `aprobada` y **nada la pone nunca en `requiere_revision`** — que es
  justamente el estado que `decision_factura` y `registrar_correccion` exigen. Resultado: no
  se puede rechazar una factura ni corregirle la cabecera desde ninguna pantalla. Y como
  `recalcular` aborta el lote entero si una sola fila tiene `servicio IS NULL` (la
  corrección de A-40, correcta en sí misma), una única factura donde el modelo no detectó el
  servicio deja **la re-homologación de toda la base inutilizable, sin forma de arreglarla**.
  Las dos situaciones —una lectura equivocada, un servicio no detectado— son esperables en
  la primera carga real, no casos de laboratorio.

Después de esos, el patrón que más se repite es que la migración a Postgres cambió el costo
de cosas que antes eran gratis: 27 sentencias DDL por cada render de página (A-52) y
escrituras en la base al simplemente navegar la pantalla de Evolución (A-53), ahora contra
un servidor remoto en vez de un archivo local.

**Lo que está bien y conviene no tocar**: la validación aritmética ahora contempla créditos
(`total = subtotal + impuestos + recargos − créditos`) y esa fórmula es **la misma** que usa
`componentes_financieros_periodo` para mostrar el total pagable, así que el tablero no puede
divergir del control que decide si una factura entra al análisis. `aplicar_cambios` quedó
transaccional y escribe solo los cambios semánticos (cierra A-35). El umbral se lee una vez
por lote, no por fila (cierra A-36). El crash con `score_antes = None` está bien resuelto
(A-31), y la guarda de diccionario vacío (A-40) es del tipo correcto: falla ruidosamente en
vez de borrar homologaciones en silencio. `quitar_periodo` ya cubre las 14 grafías de mes
(A-29). El formato de moneda argentino llegó hasta los ejes de los gráficos
(`separators=",."` en `estilo.py`), y `FONDO` dejó de ser una constante muerta.

---

## Crítico

### A-49 — `conectar(ruta)` ignora la ruta si `DATABASE_URL` está seteada: los tests escriben en producción (resuelto)

**Dónde**: `core/almacenamiento.py:191-206`.

```python
def conectar(ruta: Path | None = None) -> duckdb.DuckDBPyConnection | ConexionPostgres:
    url = os.environ.get("DATABASE_URL")
    if url:
        con = ConexionPostgres(url)   # <-- el parámetro `ruta` no se mira nunca
        _ejecutar_ddl(con)
        return con
    ruta = ruta or RUTA_BASE
    ...
```

Toda la suite de tests llama `conectar(tmp_path / "test.duckdb")` confiando en que eso
garantiza un archivo temporal. No lo garantiza: si `DATABASE_URL` está en el entorno, la
función toma la rama de Postgres y descarta el path. Verificado:

```
conectar('test.duckdb') intento ir a Postgres igual -> RuntimeError
archivo temporal creado? False
```

(el `RuntimeError` acá es solo porque `psycopg` no está instalado en este venv — lo que
importa es que entró a la rama de Postgres y nunca creó el archivo temporal).

**Qué haría un `pytest` en esa situación**: `guardar_factura` inserta facturas ficticias
("Movistar", `hash_pdf='abc123'`) en la tabla real; `test_reprocesar_no_duplica_conceptos`
corre `DELETE FROM conceptos WHERE hash_pdf = ?`; `test_aprobar_pendientes_...` aprueba
facturas y escribe en `decisiones_factura`; los tests de re-homologación corren
`UPDATE conceptos SET concepto_normalizado = ...`. Todo eso contra la base que a partir de
ahora es el registro operativo del negocio, en un proyecto Supabase **compartido con otros
productos**.

**Por qué es crítico ahora y no antes**: hasta ayer `DATABASE_URL` no existía en ningún
lado. Hoy es el secret que hace funcionar la app, está anotado en el README, y la forma
obvia de depurar algo localmente es exportarlo. CLAUDE.md marca `data/reales/` como zona
restringida y exige que los tests nunca toquen la base real; el hook de Claude Code y el
pre-commit de git implementan esa regla **sobre archivos del repo**, y una variable de
entorno pasa por fuera de los dos.

**Resuelto** (Bloque A): `conectar()` invierte la precedencia — si `ruta is not None`, usa
DuckDB con esa ruta e ignora `DATABASE_URL` sin mirarla siquiera; solo cuando `ruta is None`
se consulta la variable de entorno. Además, `tests/conftest.py` (fixture `autouse` de
sesión) saca `DATABASE_URL`/`S3_BUCKET`/`EVIDENCIA_DIR` del entorno durante toda la corrida
de tests — la protección que de verdad importa, porque varios tests de página parchean
`RUTA_BASE` y llaman `conectar()` sin argumentos, un caso que el fix del parámetro solo no
cubriría. `scripts/rehomologar.py` pasa `conectar(args.base)` (`None` si no se usó `--base`)
en vez de forzar siempre una ruta. Verificado: `conectar(tmp_path / "x.duckdb")` devuelve
DuckDB aun con `DATABASE_URL` seteada.

---

## Altos

### A-50 — Con la configuración por defecto, corregir y rechazar facturas es imposible (resuelto)

**Dónde**: `core/almacenamiento.py:278` y `:339`, contra `core/pipeline.py:168` y
`data/operacion.yaml`.

Las dos operaciones exigen un estado concreto:

```python
# decision_factura
if fila[0] != "requiere_revision":
    raise ValueError("Solo se pueden decidir facturas que requieren revisión.")

# registrar_correccion
if fila is None or fila[1] != "requiere_revision":
    raise ValueError("La factura no está disponible para corrección.")
```

Pero con `revision_humana_obligatoria: false` —el default que elegí en el Bloque 2— el
pipeline guarda `estado="aprobada"`, y **ningún otro camino del código escribe
`requiere_revision`** (verificado por grep: el único lugar es `core/pipeline.py:168`, detrás
de ese mismo interruptor). Verificado de punta a punta:

```
revision_humana_obligatoria (data/operacion.yaml): False
estado con que queda una factura recien cargada: aprobada

=== Con el default, corregir y rechazar quedan INALCANZABLES ===
  rechazar la factura: ValueError -> Solo se pueden decidir facturas que requieren revisión.
  corregir el servicio: ValueError -> La factura no está disponible para corrección.
```

**Consecuencia**: si Gemini lee mal una factura —emisor equivocado, período equivocado,
servicio no detectado, un total que cerró aritméticamente pero corresponde a otra cosa— no
hay forma de corregirla ni de sacarla del análisis desde ninguna pantalla. La página
"Revisar facturas" queda permanentemente vacía, y con ella todo el circuito de auditoría que
Codex construyó (correcciones con valor anterior, motivo y actor, en `correcciones_factura`).

**Es un error de diseño mío**: al hacer opcional la revisión resolví bien el problema que
tenía (24 aprobaciones manuales antes de ver un gráfico), pero dejé colgadas las dos
operaciones que dependían de ese estado, en vez de permitirlas también sobre una factura ya
aprobada. La corrección razonable no es volver atrás el default, sino que `decision_factura`
y `registrar_correccion` acepten también `aprobada` como estado de partida (registrando el
cambio en la auditoría, que es lo que importa).

**Resuelto** (Bloque A): `decision_factura` y `registrar_correccion` aceptan
`{"requiere_revision", "aprobada"}` como estado de partida (una `rechazada` sigue sin ser
corregible: no tiene sentido). Al rechazar se borran los casos de esa factura
(`DELETE FROM casos_alerta WHERE hash_pdf = ?`). Nueva `listar_facturas_aprobadas()`, y
`apps/segurplus/paginas/revision.py` tiene una pestaña "Ya aprobadas" con el mismo
formulario de corrección y el mismo botón de rechazo que ya existían para las pendientes.
Test de punta a punta: rechazar y corregir una aprobada deja el mismo rastro en
`decisiones_factura`/`correcciones_factura`; corregir una rechazada sigue fallando.

### A-51 — Una sola factura sin servicio bloquea la re-homologación de toda la base, sin salida (resuelto)

**Dónde**: `core/rehomologacion.py:83-86` (la corrección de A-40), encadenado con A-50.

```python
if fila.servicio is None or not diccionarios_por_servicio.get(fila.servicio):
    raise ValueError(f"Diccionario inseguro o servicio ausente: {fila.servicio!r}")
```

El criterio es correcto —no homologar contra un diccionario vacío, que era el riesgo de
A-40— pero **aborta el lote completo, no la fila**. Y la única forma de asignarle un
servicio a esa factura es `registrar_correccion`, que A-50 vuelve inalcanzable. Verificado:

```
filas a rehomologar: 2 (servicios: ['telefonia', None])
  recalcular: ValueError -> Diccionario inseguro o servicio ausente: None
```

**Consecuencia**: una única factura donde el modelo no detectó el servicio deja el circuito
de calibración —la razón de ser de la pantalla "Sin clasificar" y de
`scripts/rehomologar.py`— inutilizable para **todos** los proveedores, de forma permanente:
no hay salida por la UI (el botón "Previsualizar cambios" corta con el mismo error) ni por
el CLI (`scripts/rehomologar.py:72` devuelve código 2 y no procesa nada).

`servicio` es un campo que el modelo infiere del texto de la factura; que falle en alguna es
cuestión de tiempo, no una hipótesis remota. Y el daño no es proporcional a la falla: una
factura rota inhabilita el mantenimiento del diccionario entero.

**Resuelto** (Bloque A): el criterio pasó de "abortar el lote" a "omitir la fila y
reportar" — sigue sin homologar a ciegas (que era lo que A-40 quería evitar), pero por fila.
`CambioHomologacion` gana `motivo_omision`; `recalcular` ya no lanza `ValueError` para una
fila sin servicio o con diccionario vacío, emite un cambio omitido (la fila queda sin
tocar) y sigue con el resto. `scripts/rehomologar.py` y `sin_clasificar.py` informan las
filas omitidas en vez de cortar con error. De paso se encontró y corrigió un bug latente:
las dos llamaban `cargar_diccionario(None)` para filas sin servicio, lo que combina TODOS
los YAML de todos los servicios — reabriendo la competencia entre servicios que A-3 estaba
diseñado a evitar; ahora se filtran antes de construir el diccionario. Test: una fila sin
servicio no impide re-homologar las demás, y no se escribe.

---

## Medios

### A-52 — 27 sentencias DDL por cada render de página, ahora contra un Postgres remoto (resuelto)

**Dónde**: `core/almacenamiento.py:184-206`.

`_ejecutar_ddl` corre el `_DDL` completo dentro de **cada** `conectar()`, y cada página del
tablero abre su conexión al empezar a renderizar. Medido:

```
Sentencias DDL que corren en CADA conectar(): 27
  - CREATE TABLE IF NOT EXISTS facturas (
  - ALTER TABLE facturas ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT now();
  - ALTER TABLE facturas ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMP ...
  ...
```

Con DuckDB local eran 27 llamadas a una biblioteca en proceso: gratis. Contra Supabase en
`sa-east-1` son **27 round-trips de red antes de la primera consulta útil**, y Streamlit
re-ejecuta el script entero ante cualquier interacción con un widget (cambiar de pestaña,
mover un selectbox, tocar un checkbox). Se suma que Streamlit **ejecuta el cuerpo de todas
las pestañas siempre** —no son lazy—, así que las 8 consultas de la pestaña "Composición
total" corren aunque nadie la abra.

No es un bug de corrección: es un costo que la migración volvió visible y que conviene
resolver antes de que el uso diario lo haga molesto (correr el DDL una vez por proceso, o
detrás de una marca de versión de esquema, en vez de por conexión).

**Resuelto** (Bloque C): el DDL se memoiza por destino (la URL, o la ruta absoluta) en un
`set` de módulo protegido por un `threading.Lock` (Streamlit corre varios hilos de script).
`conectar(..., forzar_ddl=False)` permite saltar la memoización en tests. Test: dos
`conectar()` al mismo destino ejecutan el DDL una sola vez.

### A-53 — La página de Evolución escribe en la base en cada render (resuelto)

**Dónde**: `apps/segurplus/paginas/evolucion.py:245-249`.

```python
sincronizar_casos_alertas(
    con,
    referencia=f"comparacion:{servicio}:{periodo_0}:{periodo_1}",
    alertas=alertas_totales,
)
```

Está a nivel de script, sin ninguna condición ni botón: **cada render** ejecuta un
`INSERT ... ON CONFLICT (clave) DO UPDATE SET severidad = ..., mensaje = ..., actualizado_en = now()`
por cada alerta de la comparación. Es decir: navegar —una operación que el usuario percibe
como de solo lectura— muta la base.

Dos consecuencias: el costo de red por interacción (se suma a A-52), y que
`casos_alerta.actualizado_en` deja de significar "cuándo cambió este caso" para significar
"cuándo alguien miró esta comparación por última vez", que es justo lo contrario de lo que
un campo de auditoría debería decir.

**Resuelto** (Bloque C): `sincronizar_casos_alertas` lee primero los casos existentes de
esas claves (un solo `SELECT`) y escribe solo los que faltan o cuyo `severidad`/`mensaje`
cambió. Se resolvió en Python y no con `ON CONFLICT ... DO UPDATE ... WHERE` a propósito:
ese `WHERE` no está garantizado igual en DuckDB y en Postgres. Test: llamar dos veces con
las mismas alertas no cambia `actualizado_en`; cambiar la severidad sí lo actualiza.

### A-54 — Las dos páginas nuevas no tienen ni un test (resuelto)

**Dónde**: `apps/segurplus/paginas/revision.py` y `apps/segurplus/paginas/casos.py`, contra
`tests/apps/`.

`tests/apps/` cubre con `AppTest` las páginas de autenticación, cargar, cuarentena,
evolución y sin clasificar. Las dos páginas nuevas no aparecen en ninguno — y son
precisamente **las dos que escriben en la base desde la interfaz**: aprobar de a una,
aprobar en lote, rechazar, corregir cabeceras, asignar y cerrar casos.

La auditoría anterior había cerrado exactamente este hueco (A-19: "Evolución y Cuarentena,
0% de cobertura — nunca se ejecutaron en un test"). Las páginas nuevas lo reabren, y el
botón de aprobación en lote lo agregué yo sin test de página.

**Resuelto** (Bloque D): `tests/apps/test_revision_app.py` y `tests/apps/test_casos_app.py`,
siguiendo el patrón ya establecido para las otras páginas (`AppTest` + `RUTA_BASE`
parcheada). Cubren render sin excepción, estado vacío, aprobación en lote, rechazo,
corrección de cabecera (pendiente y ya aprobada), y actualización de un caso.

### A-55 — La calibración y la re-homologación no filtran por `estado`; el análisis sí (resuelto)

**Dónde**: `core/almacenamiento.py` (`conceptos_sin_clasificar`,
`filas_sin_clasificar_por_periodo`, `importes_por_periodo`, `metricas_por_proveedor`) y
`core/rehomologacion.py::leer_filas_a_rehomologar`.

Ninguna de esas consultas mira `facturas.estado`. En cambio `totales_por_periodo`,
`recargos_del_periodo`, `alertas_del_periodo`, `componentes_financieros_periodo` y las
consultas de `evolucion.py` filtran `estado = 'aprobada'`.

Hoy el desalineamiento es latente, porque con el default toda factura nace aprobada. Pero en
cuanto se rechace una factura, o se encienda `revision_humana_obligatoria`, la pantalla
"Sin clasificar" va a mostrar plata y conceptos de facturas que el análisis ignora, las
métricas por proveedor van a contar facturas que no impactan ningún número, y —lo más
serio— `leer_filas_a_rehomologar` va a **reescribir** `concepto_normalizado` de conceptos
pertenecientes a facturas rechazadas.

**Resuelto** (Bloque B): se agregó `AND f.estado = 'aprobada'` en `conceptos_sin_clasificar`,
`filas_sin_clasificar_por_periodo`, `importes_por_periodo`, `leer_filas_a_rehomologar` y en
las consultas de conceptos de `metricas_por_proveedor`. Para no perder la señal de calidad,
`metricas_por_proveedor` suma una columna propia `facturas_rechazadas` (y
`MetricasProveedor` el campo correspondiente), reflejada en la tabla de la página.

### A-56 — Rechazar una factura es un callejón sin salida (resuelto)

**Dónde**: `core/almacenamiento.py`, por ausencia.

No existe ningún `DELETE FROM facturas` ni un equivalente a `borrar_de_cuarentena` para
facturas rechazadas (verificado por grep: `borrar_de_cuarentena` es la única función de
borrado del módulo). Una vez rechazada, la fila queda en `facturas`, así que
`factura_ya_procesada` sigue devolviendo `True` para ese hash y **ese PDF no se puede volver
a cargar nunca**, ni siquiera después de corregir la causa del rechazo (ajustar el prompt,
agregar un alias, conseguir un PDF mejor del mismo período). La cuarentena sí tiene esa
válvula de escape desde A-17; el rechazo no.

Relacionado: `guardar_factura` pisa el estado en cada re-guardado
(`ON CONFLICT ... DO UPDATE SET ... estado = excluded.estado`, con el default
`estado="aprobada"`), así que cualquier re-guardado revierte silenciosamente un rechazo.

**Resuelto** (Bloque B, con criterio elegido por el usuario): `factura_ya_procesada` pasa a
`WHERE hash_pdf = ? AND estado <> 'rechazada'`. Volver a subir el mismo PDF lo reprocesa de
cero; `guardar_factura` lo pisa con el estado nuevo y `decisiones_factura` conserva el
rechazo anterior como historia — mismo criterio que la cuarentena ya tiene desde A-17. Test:
rechazar → `factura_ya_procesada` da `False` → `procesar_pdf` lo vuelve a procesar.

---

## Bajos

### A-57 — El adaptador de Postgres no lo ejercita ningún test (resuelto)

`psycopg[binary]` está declarado como dependencia obligatoria, pero no está instalado en el
venv de desarrollo, así que `ConexionPostgres` no se ejecuta nunca en la suite. Lo único que
lo cubre es un test **estático** (`tests/test_conexion_postgres.py`, que revisa por AST que
ningún SQL del módulo tenga un `%` suelto). Quedan sin ejercitar, entre otros: el
`BEGIN`/`COMMIT` manual de `aplicar_cambios` sobre una conexión abierta con
`autocommit=True` (psycopg desaconseja mezclar transacciones manuales con autocommit), el
paso de `params=[]` a consultas sin placeholders, y la traducción `?`→`%s`. Todo eso se
verificó a mano contra el deploy real, no en CI — sirve, pero no protege contra regresiones.

**Resuelto** (Bloque D): `tests/test_conexion_postgres_real.py`, que se salta salvo que
exista `TEST_DATABASE_URL` (y `psycopg` sea importable), marcado `red_real` como los tests
contra Gemini/INDEC. Ejercita `conectar` + DDL + `guardar_factura` + `aplicar_cambios` (el
`BEGIN`/`COMMIT` sobre una conexión `autocommit=True`) + `conceptos_sin_clasificar`, contra
un schema descartable. Documentado en el README cómo correrlo.

### A-58 — Los roles, o no controlan nada, o bloquean todo (resuelto)

**Dónde**: `apps/segurplus/autenticacion.py`.

Con la contraseña compartida (el modo actual), el login asigna
`st.session_state["segurplus_rol"] = "administrador"` a cualquiera que entre, así que
`requerir_rol(...)` no filtra absolutamente nada: es decorativo.

Con `OIDC_PROVIDER` configurado pero sin los secrets `ROLE_*_EMAILS`, `_rol_oidc` devuelve
`"cargador"` para todos, y entonces `requerir_rol("revisor", "responsable", "administrador")`
deja a **todo el mundo afuera** de "Revisar facturas" y de "Casos" — incluido quien
configuró el sistema.

No hay un punto intermedio razonable por defecto: el que enciende OIDC sin leer el README se
queda sin acceso a dos páginas, y el que no lo enciende tiene un control de roles que no
controla nada.

**Resuelto** (Bloque E, con criterio elegido por el usuario: fallar abierto con aviso
visible, no dejar a nadie afuera de su propia herramienta): si **ningún** `ROLE_*_EMAILS`
está configurado, `_rol_oidc` devuelve `"administrador"` para todos y `requerir_rol` muestra
un `st.warning` permanente explicando que los roles no están configurados y qué secret
configurar para que empiecen a restringir de verdad. Test unitario con y sin secrets.

### A-59 — Detalles menores acumulados (resuelto)

- `motivos_cuarentena_por_proveedor` separa los motivos por `"; "`; un motivo de falla que
  contenga esa secuencia se parte en dos motivos distintos y se cuenta doble.
- `pesos_ars(-0.004)` devuelve `"$-0,00"`: signo menos en un valor que redondea a cero.
- La rama `denominador == 0` de `efecto_dominante` es inalcanzable (si los tres efectos son
  cero, `variacion_total` ya salió por la rama anterior). Guarda defensiva muerta, mía.
- Los casos generados desde una comparación guardan en `casos_alerta.hash_pdf` un string del
  tipo `comparacion:telefonia:2026-08-01:2026-09-01`, que no es un hash de PDF: la columna
  miente sobre lo que contiene, y cualquier join futuro contra `facturas.hash_pdf` va a
  fallar en silencio para esas filas.

**Resuelto** (Bloque E), los cuatro:
- `pesos_ars` redondea primero y decide el signo sobre el valor ya redondeado.
- Los motivos se guardan separados por `"\n"` (un motivo real nunca trae salto de línea, a
  diferencia de `"; "`), aceptando también `"; "` al leer para no perder compatibilidad con
  lo ya guardado.
- Se sacó la rama `denominador == 0` de `efecto_dominante`, confirmada inalcanzable.
- `casos_alerta.hash_pdf` no se renombró (el `_DDL` es aditivo e idempotente, un
  `RENAME COLUMN` no lo sería) — se documentó en el DDL y en `sincronizar_casos_alertas` que
  guarda un hash de factura real o un identificador sintético de comparación, según quién
  llame.

---

## Tabla resumen

| # | Severidad | Dónde | Qué |
|---|---|---|---|
| A-49 | **Crítico** | `core/almacenamiento.py:191-206` | `conectar()` ignora el path si hay `DATABASE_URL`: un `pytest` con esa variable exportada inserta, actualiza y borra en la base de producción — **resuelto** |
| A-50 | Alto | `core/almacenamiento.py:278,339` + `core/pipeline.py:168` | Con el default (`revision_humana_obligatoria: false`) ninguna factura llega a `requiere_revision`, así que rechazar y corregir cabeceras es imposible desde cualquier pantalla — **resuelto** |
| A-51 | Alto | `core/rehomologacion.py:83-86` | Una sola fila con `servicio IS NULL` aborta la re-homologación de toda la base, y A-50 impide corregirle el servicio — **resuelto** |
| A-52 | Medio | `core/almacenamiento.py:184-206` | 27 sentencias DDL por cada `conectar()`, o sea por cada render de página, ahora contra un Postgres remoto — **resuelto** |
| A-53 | Medio | `apps/segurplus/paginas/evolucion.py:245-249` | La página escribe casos en la base en cada render; `actualizado_en` pasa a medir visitas, no cambios — **resuelto** |
| A-54 | Medio | `tests/apps/` | `revision.py` y `casos.py` — las dos páginas que escriben desde la UI — sin ningún test, reabriendo el hueco de A-19 — **resuelto** |
| A-55 | Medio | `almacenamiento.py`, `rehomologacion.py` | Calibración, métricas y re-homologación no filtran `estado='aprobada'`; el análisis sí — la re-homologación llega a reescribir conceptos de facturas rechazadas — **resuelto** |
| A-56 | Medio | `core/almacenamiento.py` (ausencia) | Una factura rechazada bloquea su hash para siempre: no hay forma de recargar ese PDF; y un re-guardado revierte el rechazo en silencio — **resuelto** |
| A-57 | Bajo | `tests/`, `pyproject.toml` | El camino PostgreSQL no lo ejercita ningún test; solo hay un chequeo estático del SQL — **resuelto** |
| A-58 | Bajo | `apps/segurplus/autenticacion.py` | Los roles son decorativos con contraseña compartida, y con OIDC mal configurado bloquean a todos — **resuelto** |
| A-59 | Bajo | varios | Motivos de cuarentena partidos por `"; "`, `"$-0,00"`, rama muerta en `efecto_dominante`, `casos_alerta.hash_pdf` con un valor que no es un hash — **resuelto** |

---

## Qué conviene resolver antes de cargar la primera factura real

**Los 11 hallazgos de esta auditoría (A-49 a A-59) quedaron resueltos**, en el orden de
prioridad que este documento planteaba (bloqueantes primero) — ver el criterio de cada uno
en su sección y en la tabla resumen de arriba. No queda ningún pendiente propio de este
documento antes de empezar a cargar facturas reales.

**Siguen diferidos de auditorías anteriores**, sin cambios: **A-26** (`_parsear_monto` con
separadores de miles mezclados) y **A-27** (alerta de período faltante que asume periodicidad
mensual, y hace que un servicio bimestral alerte siempre). Los dos necesitan facturas reales
para verificarse, así que su momento sigue siendo después de la primera carga.
