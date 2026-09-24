# Segurplus — plan de mejora con costo monetario cero

> **Decisiones posteriores (2026-09-23).** Este documento nació sobre
> `11ad46d`; el remoto avanzó a `40e16ec` y reemplazó la ruta principal de
> Streamlit por FastAPI en Render. La arquitectura elegida para el piloto
> es **Render Free + PostgreSQL y Storage privados de Supabase Free + Google
> OIDC**, con un único operador inicial. R2 y Oracle VM no son destinos de
> esta etapa. La carga por Gemini gratuito solo se permite tras declarar que
> el PDF no contiene datos sensibles, confidenciales ni personales; el
> resto se carga manualmente en la web. Habrá respaldo cifrado externo
> semanal y prueba mensual de restauración. La tabla de opciones de abajo
> queda como análisis histórico, no como una elección todavía abierta.
>
> El orden vigente es: integridad/seguridad → cierre S-01–S-20 en la web →
> piloto medido. No se activará una rama con auto-deploy hasta configurar
> OIDC y bucket, rotar secretos y pasar las pruebas de integración.

**Fecha:** 2026-09-23. **Base:** [`auditoria-estado-2026-09-23.md`](auditoria-estado-2026-09-23.md), checkout `11ad46d`. **Objetivo:** que una persona pueda confiar en la respuesta «¿qué aumentó, cuánto fue precio/cantidad/impuestos y qué hay que reclamar?», reconstruirla desde el comprobante y no perder ni contaminar datos. Este es un plan, no una implementación; no se cambió código.

## Regla de producto y de costo

No sumar funcionalidades hasta que carga, validación, revisión, análisis, alerta y exportación coincidan con la misma versión aprobada. **Costo cero** significa sin servicios de pago, sin consumir créditos promocionales que vencen y sin pasar automáticamente a un tier facturable. Un «free tier» con exceso cobrable o sin backups suficientes no cumple por sí solo. La compatibilidad técnica, residencia/privacidad, límites y condiciones vigentes se validan antes de elegir proveedor. Si una factura no puede procesarse legal y seguramente con el tier gratuito de Gemini, se revisa/carga por vía local manual o se posterga esa factura: la precisión y la confidencialidad prevalecen sobre automatizar gratis.

Las [condiciones de Gemini gratuito](https://ai.google.dev/gemini-api/terms) permiten usar contenido para mejorar productos y advierten no enviar datos sensibles, confidenciales o personales. `gemini-3.6-flash` existe y admite PDF/salida estructurada ([modelo oficial](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)), pero el riesgo de datos se decide por factura, no por la existencia de cuota gratis. Los límites efectivos se consultan en AI Studio y pueden variar ([rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)).

## Orden de entrega

### Etapa 0 — congelar la verdad operativa (sin infraestructura nueva)

1. Clasificar las garantías actuales como **implementada / probada localmente / verificada en despliegue / pendiente**. Actualizar README, `docs/estado.md` y ADR-003 al terminar cada etapa; no anunciar «durable» por tener solo dos variables de entorno.
2. Definir contrato de datos: una factura aprobada es inmutable como versión financiera; toda corrección produce evento/versionado, validación nueva y decisión nueva. Ninguna consulta ejecutiva toma estados ajenos a `aprobada`; calibración pendiente se separa por estado. Moneda no ARS, período ausente o servicio dudoso no entran en agregados ARS.
3. Preparar fixtures sintéticas de fallas: error después de upsert, después de borrar líneas, después de decisión, IPC ausente, servicio corregido, dos casos con igual texto, empate de homologación, cero explícito, otra moneda. La suite debe reproducir los P0 antes de tocar lógica.

**Salida:** matriz de consultas/acciones y sus estados permitidos; tests de regresión que muestran los defectos sin usar `data/reales/`.

### Etapa 1 — integridad y fail-closed (P0)

1. Hacer atómica la unidad de carga: factura, conceptos, impuestos, créditos, recargos, alertas y evento de carga. La aprobación/rechazo y cada corrección deben persistirse junto con su evento en la misma transacción; fallo = rollback. El hash repetido debe permitir recuperación de intentos incompletos sin duplicar ni sobrescribir una versión aprobada.
2. Exigir evidencia recuperable y verificada **antes** de aceptar una carga operativa. En desarrollo local se permite modo aislado claramente etiquetado; en despliegue operativo, ausencia/fallo de BD o bucket bloquea procesamiento. Añadir verificación de lectura/hash de PDF y reconciliación de referencias huérfanas.
3. Corregir todas las consultas, métricas, acciones de re-homologación y exportaciones para respetar estado y snapshot aprobados. No convertir falta de IPC en 0% ni generar alerta «precio sobre IPC» sin índice válido; el tablero debe decir «no calculable».
4. Validar autorización en cada página **y** en cada acción de escritura; usuarios OIDC no listados se rechazan por defecto. Deshabilitar contraseña compartida en un piloto con decisiones atribuibles. Registrar actor sin secretos. Restringir re-homologación y eliminación/reintento de cuarentena a roles explícitos.

**Salida:** tests transaccionales con fallos inyectados en DuckDB y PostgreSQL; AppTests de acceso por rol; reinicio simulado sin pérdida; pendientes/rechazadas/cuarentena no afectan Evolución, Excel ni casos; sin IPC no hay inflación ficticia.

### Etapa 2 — revisión humana completa y veracidad financiera

1. Mostrar PDF original mediante acceso temporal privado, junto a extracción y validaciones por campo/línea; permitir corregir cantidades, importes, subtotal, impuestos, recargos y créditos, además de cabecera. Revalidar aritmética y moneda; volver a homologar cuando cambia servicio/descripción. Solo aprobar una versión completa y conciliada, con actor, motivo y diferencias visibles.
2. Guardar diferencias de conciliación en pesos, no solo booleanos; revisar la tolerancia actual del 2% con muestras reales autorizadas. Cero de cantidad explícito no se convierte en uno. Un total que cierra internamente no prueba que las líneas correspondan al PDF: la revisión debe comparar evidencia.
3. Definir claramente tres bases: consumo comparable para precio/cantidad, composición del total pagable y variación real por IPC. No llamar «total de factura» al subtotal de conceptos. No sumar monedas distintas.

**Salida:** dado un PDF sintético con una línea incorrecta pero aritméticamente consistente, la revisión la detecta/corrige y se reconstruye qué cifra cambió, cuándo y por quién. El Excel coincide con el tablero y apunta a la misma versión.

### Etapa 3 — casos y calibración utilizables

1. Generar/sincronizar alertas al aprobar o recalcular una versión, no al abrir una página. Clave estable por evento/concepto/regla; registrar cambios de estado, responsable, vencimiento y evidencia como eventos. Cierre/descartado exige justificación y actor. Evitar etiquetas de selector no únicas.
2. Re-homologación: previsualización vinculada a hash de YAML, umbral y snapshot de filas; segunda confirmación para regresiones; conteo de `UPDATE` realmente efectivos; persistir score, motivo y candidatos empatados. Bloquear operación si cambia cualquier insumo o si hay servicio/diccionario inválido.
3. «Sin clasificar» solo usa aprobadas para cifras ejecutivas, distingue no medido de cero/empate y muestra pesos constantes **solo** cuando hay IPC válido; sin IPC, no presenta ranking intertemporal ni porcentaje real. A-26/A-27 se cierran con periodicidad y formato observado en proveedores reales.

**Salida:** toda alerta relevante aparece en la cola sin visitar Evolución; un caso cerrado conserva historia; una previsualización obsoleta no se aplica; ninguna prioridad en pesos reales se inventa a partir de nominales.

### Etapa 4 — prueba de piloto antes de ampliar alcance

1. Corpus pequeño de PDFs **autorizados** fuera de Git, con expected results redactados y sin duplicar datos sensibles en logs. Primero 2–3 comprobantes por proveedor y períodos consecutivos; registrar exactitud por campo, cambios humanos, cuarentenas, homologación, empates, tiempo por factura y llamadas reales a Gemini. El test de API real sigue siendo opt-in.
2. Probar importación, operación concurrente, reinicio, fallo de red, migración y restauración; conciliar conteos/hashes/decisiones/casos contra backup. Definir runbook de respaldo y ensayo periódico con evidencia fechada. Si el servicio gratis elegido no ofrece retención suficiente, generar y verificar copias cifradas fuera del hosting sin subir facturas a Git.
3. Revisar términos y autorización de datos antes de cada ampliación de usuarios o sensibilidad. Mantener carga manual y revisión; no añadir correo, forecasting, contratos, ARCA ni multicliente durante este ciclo.

**Salida:** una persona distinta puede tomar un importe del tablero, reconstruir PDF→extracción→validación→corrección→aprobación→análisis y restaurar un caso de prueba tras caída.

## Arquitectura gratuita: decisión condicionada, no compra anticipada

| Opción | Cuándo sirve | Condición de seguridad/costo |
|---|---|---|
| Piloto local en una PC controlada: DuckDB + PDFs privados + backup cifrado externo | Pocos usuarios y operación supervisada; máxima simplicidad, cero factura de nube | No exponerlo como app multiusuario duradera. Definir quién hace backup, retención y restauración; evitar OneDrive como única copia/garantía transaccional. |
| Streamlit Community Cloud + PostgreSQL gratuito + objetos privados gratuitos | Acceso compartido si proveedor elegido permite datos y el volumen entra holgadamente en límites | La app no guarda estado maestro en disco local ([Streamlit](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)); probar proveedor, cuota, privacidad y backups. No configurar facturación automática por exceso. |
| Buckets S3 compatibles (p. ej. R2 Standard dentro de cuota) | PDFs originales privados cuando el acceso compartido es imprescindible | [R2 publica cuota gratuita](https://developers.cloudflare.com/r2/pricing/), pero el adaptador actual usa una cabecera SSE no admitida por R2 ([compatibilidad](https://developers.cloudflare.com/r2/api/s3/api/)); requiere prueba de integración y diseño de cifrado/backup antes de usarlo. |

No se elige proveedor en este documento: la elección requiere conocer cantidad/tamaño de PDFs, usuarios, jurisdicción, confidencialidad y capacidad de backup. Ningún plan gratuito equivale automáticamente a retención de 30 días ni a costo cero si permite sobrecargos. Si no hay solución gratuita que cumpla durabilidad **y** términos de datos, el piloto queda local/manual antes que degradar garantías.

## Métricas de aceptación (no de vanidad)

- **Integridad:** 0 operaciones parciales tras fallas inyectadas; 100% de cifras mostradas provienen de versiones aprobadas y PDF recuperable.
- **Exactitud:** diferencias de subtotal/total visibles y revisadas; 0 alertas «sobre IPC» si falta IPC; 0 sumas entre monedas diferentes; mismo valor en tablero y Excel para la misma versión.
- **Trazabilidad:** 100% de aprobaciones/correcciones/cierres tienen actor, fecha y motivo; restauración de prueba reconciliada por hash y conteo.
- **Operabilidad:** alertas aparecen sin navegación previa, tienen dueño o estado «sin asignar» explícito; pendientes vencidos visibles; empates y scores sin medir no se esconden.
- **Costo/privacidad:** cuota y llamadas reales visibles; no facturación activada por error; ninguna factura clasificada como confidencial/personal se envía al tier gratuito en contra de sus condiciones.

## Decisiones que necesitan evidencia, no nuevas funciones

Periodicidad de cada suministro (A-27), formatos de monto dudosos (A-26), tolerancia aritmética, criterios de aprobación, clasificación de datos, proveedor gratuito de almacenamiento y política de backup. El siguiente paso útil no es añadir otro modelo: es ejecutar las etapas 0–2 con pruebas reproducibles y validar después con un corpus pequeño autorizado.
