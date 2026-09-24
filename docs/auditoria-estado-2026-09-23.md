# Segurplus — diagnóstico y auditoría del estado actual

> **Reconciliación posterior (2026-09-23).** El texto original de abajo es
> una fotografía del checkout `11ad46d`, no del remoto actual. Se avanzó el
> checkout a `40e16ec` (`claude/invoice-analysis-automation-7axk9u`) y se
> trabaja en `codex/auditoria-piloto-web`. La auditoría web E-1–E-24 del remoto
> tiene cierres de código y tests documentados en
> [`auditoria-2026-09-web.md`](auditoria-2026-09-web.md); **E-7 (rotación de
> credenciales) sigue sin constancia de ejecución**. Los estados siguientes
> distinguen corrección en esta rama de verificación real en Render/Supabase:

| ID | Estado reconciliado | Evidencia / falta para cierre |
| --- | --- | --- |
| S-01 | En corrección | Escrituras y confirmación transaccionales con rollback probado en DuckDB; falta integración PostgreSQL real. |
| S-02 | En corrección | Decisiones/correcciones atómicas y snapshots inmutables añadidos; falta ensayo real de restauración. |
| S-03 | Resuelto en remoto | Consultas de sin clasificar y re-homologación ya filtran aprobadas; suite remota reproducida. |
| S-04 | Resuelto en remoto | E-5 usa IPC desconocido como `None`, no como cero; paridad pantalla/Excel probada. |
| S-05 | En corrección | Esta rama bloquea modo producción sin PostgreSQL y PDF recuperable; faltan bucket, migración y backup reales. |
| S-06 | Corregido localmente | Se eliminó cabecera SSE incompatible en el adaptador S3; prueba con cliente simulado. |
| S-07 | En corrección | Google OIDC, lista permitida y CSRF implementados localmente; falta cliente OAuth y prueba real. |
| S-08 | Parcialmente resuelto en remoto | FastAPI ya permite revisar líneas y montos con PDF; falta prueba con corpus real autorizado y versión financiera completa. |
| S-09 | Corregido localmente | Clasificación explícita antes de Gemini y vía manual sin modelo; falta validar política y operación desplegada. |
| S-10 | Pendiente | “Sin clasificar” aún no está en la web. |
| S-11 | Pendiente | Re-homologación web y snapshot seguro de previsualización no implementados. |
| S-12 | Pendiente | Casos carecen de identidad estable independiente del mensaje y eventos con actor. |
| S-13 | Pendiente | Alertas de comparación aún no alimentan una cola web exhaustiva al aprobar. |
| S-14 | Parcialmente corregido | Cero explícito preservado y probado; tolerancia aritmética requiere corpus real. |
| S-15 | Corregido localmente | Aprobación no ARS bloqueada y agregados ARS excluyen históricos no ARS; falta revisión del despliegue. |
| S-16 | Resuelto en remoto | Intentos reales de Gemini persistidos y usados para cuota, incluidos fallidos. |
| S-17 | Parcialmente resuelto | `conectar(ruta)` ya respeta destino local y tests aíslan `DATABASE_URL`; integración PostgreSQL real sigue opt-in. |
| S-18 | Pendiente | IPC aún no conserva publicación durable/versionada. |
| S-19 | Dependiente de muestras | A-27 fue corregido con `periodo_hasta`; A-26 y excepciones de periodicidad requieren PDFs autorizados. |
| S-20 | En corrección | Esta reconciliación actualiza el diagnóstico; README, estado y ADRs aún deben quedar alineados con el despliegue. |

### Verificación de infraestructura (2026-09-24)

La fuente de verdad remota no es un proyecto independiente: es el esquema
`segurplus` del proyecto Supabase `klerico`, separado de las tablas de
Klericó. El esquema está vacío y mantiene el conjunto histórico de tablas,
pero aún no contiene `versiones_factura`, `clasificaciones_documento`,
`eventos_caso` ni `documentos_pdf` que requiere esta rama.

El intento de aplicar esa migración aditiva desde la integración de Supabase
fue rechazado con `must be owner of table conceptos`: el rol de administración
disponible no es dueño de las tablas creadas por el rol de la aplicación. No
se reintentó ni se modificó ningún permiso. La migración debe correr con la
misma conexión de aplicación que usa Render, dentro del corte controlado.

El asesor de seguridad de Supabase informó que las 11 tablas existentes del
esquema tienen RLS desactivado. Mientras el esquema esté expuesto por Data
API, una clave `anon`/`authenticated` con permisos podría acceder a las filas.
**No se habilitó RLS automáticamente**: hacerlo sin políticas explícitas puede
bloquear el rol servidor que usa Render. El corte productivo debe incluir una
migración aditiva para las tablas nuevas y una política de acceso de mínimo
privilegio, verificada contra el rol de la aplicación, antes de cargar datos.
La base quedó en cero filas durante la verificación.

La suite remota en este checkout pasó **554 tests, 4 omitidos** con dependencias
web instaladas temporalmente; los omitidos incluyen integraciones que no
pueden certificarse aquí. Ningún cambio de esta rama se desplegó ni se
ejecutó contra facturas reales o credenciales productivas.

**Fecha:** 2026-09-23. **Base examinada:** `11ad46deee45ac82f2766607dbbc6cb81c80845a`, branch local `claude/invoice-analysis-automation-7axk9u`. **Alcance:** código, tests, CI y documentación; no se abrieron facturas ni bases de `data/reales/`, no se usaron credenciales, no se modificó código. No se pudo refrescar `origin` porque el entorno denegó escritura en `.git/FETCH_HEAD`; por eso «actual» significa el checkout local, no una afirmación sobre el servidor ni sobre el despliegue.

## Veredicto

Hay un núcleo analítico razonablemente sólido y bien testeado con datos sintéticos. El circuito operativo incorporó revisión, evidencia, PostgreSQL opcional y casos, pero **todavía no demostraría las garantías que promete para uso cotidiano con facturas reales**: hay escrituras parciales posibles, consultas que mezclan estados, alertas de IPC que pueden mentir cuando falta el índice y ausencia de pruebas de integración del backend productivo. No recomiendo presentar el piloto como registro financiero confiable hasta cerrar los P0 y probar restauración y un lote real autorizado.

**Verificación local:** `pytest -q --basetemp .audit-pytest-20260923`: **225 passed, 1 skipped**; `python -m ruff check .`: **limpio**. La primera corrida de pytest produjo 61 errores de fixture por `PermissionError` sobre el directorio temporal global de Windows; desaparecieron al usar un temporal dentro del workspace. Ese episodio es del entorno de ejecución, no evidencia de 61 bugs del repo. El test omitido requiere la API real. Los tests no equivalen a una prueba de PostgreSQL/S3/OIDC/Gemini con facturas reales.

## Lo que sí está construido

- Ingesta de PDF digital, extracción estructurada con Gemini 3.6 Flash, esquema canónico y controles aritméticos; hash del PDF, cuarentena y límite básico de llamadas.
- Homologación por servicio, descomposición precio/cantidad, comparación nominal/real con IPC, alertas, Excel a demanda y tablero Streamlit.
- Estado `requiere_revision` en la carga normal, aprobación/rechazo con actor y motivo, cabecera corregible, impuestos/recargos/créditos separados y casos de alerta.
- Adaptador opcional de PostgreSQL y almacenamiento S3 compatible, mientras DuckDB queda como fallback local. CI ejecuta pytest y Ruff.

## Hallazgos verificables

La severidad indica impacto potencial, no que haya ocurrido pérdida o exposición de datos. «Confirmado» significa deducible del flujo/código o de documentación oficial; donde falta prueba de producción se señala.

| ID | Severidad | Hallazgo y evidencia | Impacto / comprobación pendiente |
|---|---|---|---|
| S-01 | **P0** | `core/almacenamiento.py:167,183-198,386-495` abre PostgreSQL con `autocommit=True`; `guardar_factura` hace upsert, varios `DELETE/INSERT` y recién al final registra auditoría. `core/pipeline.py:150-166` guarda alertas después. | Si falla una instrucción quedan factura, líneas o alertas parciales. El hash luego bloquea el reintento (`factura_ya_procesada`). Probar fallo inyectado en cada etapa en PostgreSQL y DuckDB. |
| S-02 | **P0** | `core/almacenamiento.py:256-278,281-327` cambia estado/campo antes de grabar decisión/corrección, sin transacción común. | Puede existir factura aprobada o corregida sin evidencia de quién/por qué; viola la trazabilidad. Probar rollback ante fallo del registro posterior. |
| S-03 | **P0** | `core/almacenamiento.py:498-540` consulta todos los estados en «Sin clasificar» y en el denominador; `core/rehomologacion.py:101-143` lee todas las filas. | Una factura pendiente/rechazada afecta métricas y una operación de calibración; contradice «solo aprobadas». Definir si la re-homologación de pendientes se permite, pero nunca mezclarla silenciosamente con cifras ejecutivas. |
| S-04 | **P0** | `apps/segurplus/paginas/evolucion.py:190-213,231-248,260-268` inicializa IPC en `0.0`; si descarga/cálculo falla, igual genera `precio_sobre_ipc` y muestra inflación `+0.0%`. | Una indisponibilidad de IPC se convierte en falsa alerta «por encima del IPC». Probar red caída, serie incompleta y período fuera de rango; estado «IPC desconocido» debe ser distinto de 0%. |
| S-05 | **P0** | `core/evidencia.py:13-45` puede devolver `None` si falta bucket y directorio; `apps/segurplus/paginas/cargar.py:25-31` solo informa; `core/almacenamiento.py:189-198` cae a DuckDB si falta URL. | El tablero deja procesar PDFs reales sin evidencia durable o con BD efímera en Community Cloud. La presencia de variables tampoco prueba escritura/lectura ni backup. [Streamlit advierte que el disco local no es persistente](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data). |
| S-06 | **P0** | `core/evidencia.py:32-41` fuerza `ServerSideEncryption="AES256"` para cualquier S3 compatible. La [compatibilidad S3 de Cloudflare R2](https://developers.cloudflare.com/r2/api/s3/api/) marca `x-amz-server-side-encryption` como no soportado en `PutObject`. | Una opción gratuita plausible de bucket falla al cargar cada PDF. Es incompatibilidad concreta, no una falla comprobada del deploy actual; falta elegir y probar proveedor. |
| S-07 | **P0** | `apps/segurplus/autenticacion.py:45-66` asigna `cargador` a cualquier identidad OIDC con email no listado. `streamlit_app.py` registra todas las páginas; solo Revisión y Casos llaman `requerir_rol`. «Sin clasificar» puede re-homologar y Cuarentena puede borrar entradas sin control de rol. La contraseña compartida otorga `administrador` y actor genérico (`autenticacion.py:69-113`). | Acceso excesivo, acciones sensibles sin atribución individual. Probar roles por página y denegar por defecto identidad no autorizada. No se verificó la configuración efectiva de OIDC del deploy. |
| S-08 | **P0** | `apps/segurplus/paginas/revision.py:39-132` permite corregir solo cabecera. No muestra PDF original (solo URI), ni líneas/impuestos/créditos comparados contra él. `core/almacenamiento.py:281-327` no revalida fechas, servicio, moneda ni recalcula homologación/alertas antes de aprobar. | «Revisión por campos» es parcial: un importe o cantidad mal leídos pero aritméticamente consistentes puede aprobarse, y un servicio corregido conserva la homologación anterior. Falta prueba end-to-end de corrección→validación→aprobación. |
| S-09 | **P0 / decisión de datos** | `core/extraccion/gemini.py:85-112` envía el PDF completo al tier gratuito. Las [condiciones oficiales de Gemini para servicios gratuitos](https://ai.google.dev/gemini-api/terms) indican uso de entradas/salidas para mejorar productos, posible revisión humana y «do not submit sensitive, confidential, or personal information». | El consentimiento previo al piloto gratis no elimina la necesidad de clasificar cada factura real y autorizar ese tratamiento. Si contiene datos confidenciales/personales, el plan 100% gratis debe usar otra vía apta (por ejemplo, captura/manual local) o no procesarla, nunca enviarla automáticamente. |
| S-10 | **P1** | `apps/segurplus/paginas/sin_clasificar.py:46-82` captura indisponibilidad del IPC y ordena nominalmente, aunque el objetivo es priorizar en pesos constantes; el texto lo avisa, pero la tabla y métrica quedan visualmente iguales. | Prioridad y porcentaje pueden ser engañosos. Separar claramente «sin priorización real» y no exhibir un ranking intertemporal nominal como sustituto. |
| S-11 | **P1** | `apps/segurplus/paginas/sin_clasificar.py:27-35,141-193` firma solo diccionarios, no umbral, snapshot de filas ni versión de datos. No hay segunda confirmación de regresiones. `core/rehomologacion.py:145-164` devuelve cantidad intentada, sin verificar filas realmente afectadas, y no persiste candidatos de empate/motivo al aplicar. | Una previsualización puede quedar obsoleta o informar cambios no ejecutados. Probar concurrencia/cambio de umbral y regresiones. |
| S-12 | **P1** | `core/almacenamiento.py:618-672` identifica casos con el texto del mensaje y sobreescribe estado/responsable/evidencia sin evento de auditoría ni actor. `apps/segurplus/paginas/casos.py:45` usa `tipo + mensaje[:70]` como clave visible de un diccionario. | Un cambio de redacción/importe crea otro caso y dos alertas de igual prefijo pueden ocultar una opción; un cierre puede hacerse sin responsable/evidencia y no se reconstruye su historia. |
| S-13 | **P1** | `apps/segurplus/paginas/evolucion.py:231-250` materializa casos solo al abrir una comparación específica. `core/almacenamiento.py:593-641` incorpora la pareja de períodos en la clave. | La cola «Casos» no es exhaustiva: depende de qué páginas navegó alguien y puede duplicar anomalías entre comparaciones. Probar generación al aprobar/recalcular y deduplicación por evento económico. |
| S-14 | **P1** | `core/extraccion/esquema.py:237-270` usa `float(c.get("cantidad", 1) or 1)`, que transforma cero explícito en 1. `core/extraccion/validacion.py:103-155` usa tolerancia del 2% y omite doble lectura si no hay `total_impreso`. | Riesgo de alterar cantidad/precio y de aprobar errores pequeños o lecturas que cierran internamente. La tolerancia es una decisión deliberada, no un bug automático: calibrarla con PDFs autorizados y conservar diferencia exacta por comprobante. |
| S-15 | **P1** | `core/extraccion/esquema.py:189-270` no valida `moneda` frente a ARS; `core/extraccion/gemini.py:55-59` pide ARS aunque la factura diga otra cosa; `evolucion.py` formatea todo como pesos. | Una factura en otra moneda puede mezclarse con ARS. Debería ir a revisión/cuarentena o a una serie separada, nunca sumarse sin conversión explícita. |
| S-16 | **P1** | `core/almacenamiento.py:201-215` estima cuota Gemini por PDFs persistidos, no por llamadas efectivas; `core/pipeline.py:90-112` falla antes de persistir en API/evidencia. | Reintentos fallidos no consumen el contador y el límite propio no protege la cuota real. Contrastar con límites reales de AI Studio ([documentación de rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)). |
| S-17 | **P1** | `core/almacenamiento.py:183-198` da precedencia a `DATABASE_URL` aun cuando el llamador pasa `ruta` temporal. Los tests de almacenamiento no ejercitan `ConexionPostgres`, bucket ni OIDC (búsqueda en `tests/`). | Un entorno de test con URL configurada podría conectarse a la BD real; el backend productivo carece de pruebas de contrato, migración, concurrencia y restauración. |
| S-18 | **P1** | `core/macro/ipc.py:15-54` guarda solo el parquet local, sin fecha de descarga, versión de serie ni snapshot durable. `evolucion.py` toma el IPC al renderizar. | Una cifra histórica puede cambiar sin que quede registrado con qué publicación se calculó. Además el cache desaparece con reinicios del hosting. |
| S-19 | **P2** | `core/analisis/alertas.py:180-226` asume mensualidad para todo servicio (A-27 diferido); `core/ingesta/pdf_texto.py:67-105` conserva ambigüedades de monto (A-26 diferido). | Falsos positivos en servicios bimestrales y lectura dudosa de algunos formatos; resolver con muestras reales y periodicidad por suministro, no con suposiciones generales. |
| S-20 | **P2** | `docs/estado.md` repite «Falta», dice 215 tests y simultáneamente afirma que falta persistencia después de describirla; `README.md:80` indica branch `main` aunque el checkout local solo tiene la rama Claude. `streamlit_app.py` conserva descripción de contraseña compartida como única protección. | La documentación no sirve como estado operativo. Distinguir «código presente», «configurado», «verificado en producción» y «pendiente». No se comprobó qué branch usa la app desplegada. |

### Brechas de prueba y operación

- No hay prueba en CI con PostgreSQL real ni almacenamiento S3 compatible; la migración DDL se ejecuta en cada `conectar()` (`core/almacenamiento.py:176-198`) y no tiene versionado explícito ni prueba de rollback.
- No hay AppTests del flujo completo de Revisión ni Casos, ni una prueba con usuarios OIDC de distintos roles. Los tests actuales cubren piezas del core y páginas antiguas, pero no la promesa operativa de extremo a extremo.
- El ADR-003 **declara** backup diario, 30 días y restauración mensual, pero en el repo no hay runbook, registro ni prueba de restauración. No se puede inferir que el proveedor gratuito lo cumpla.
- No se comprobó el despliegue, la configuración efectiva de secretos, el bucket, la base, la política de datos ni facturas reales. Estas son puertas de aceptación, no defectos confirmados del entorno.
- A-26 y A-27 siguen explícitamente diferidos; los hallazgos A-29–A-48 parecen incorporados en código y tests, pero esta auditoría revisa la implementación actual y no certifica cada hallazgo histórico por separado.

## Prioridad inmediata

Primero proteger integridad, trazabilidad y veracidad (`S-01` a `S-09`); luego cerrar pruebas operativas, IPC/calibración y casos (`S-10` a `S-18`). El documento hermano [`plan-mejora-gratis-2026-09-23.md`](plan-mejora-gratis-2026-09-23.md) define el orden, criterios de salida y opciones de costo cero sin asumir servicios pagos.
