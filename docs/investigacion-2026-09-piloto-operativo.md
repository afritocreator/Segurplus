# Investigación — evolución de Segurplus hacia un piloto operativo confiable

**Fecha:** 2026-09-13  
**Audiencia:** dirección operativa  
**Criterio:** impacto y confiabilidad antes que automatización o estética.

## Conclusión ejecutiva

Segurplus ya tenía una base analítica correcta: extracción estructurada, controles
aritméticos, cuarentena, homologación y separación entre efecto precio y cantidad. El
riesgo principal antes de incorporar facturas reales no era que faltara otra IA, sino que
no existía una cadena operativa completa para demostrar de dónde vino cada cifra, quién
la aprobó y qué ocurrió con una alerta.

La prioridad recomendada fue, por lo tanto: persistencia durable, evidencia del PDF,
revisión humana, identidad individual y casos trazables. Las integraciones automáticas,
el forecasting y los contratos se dejan para cuando el piloto entregue datos reales y
métricas de calidad.

## Hallazgos del estado inicial

1. **Persistencia:** la fuente de verdad era un archivo DuckDB local. Streamlit Community
   Cloud no garantiza la persistencia de archivos locales, por lo que un reinicio podía
   dejar al piloto sin historial operativo. Además, DuckDB es excelente para análisis
   local, pero su modelo de concurrencia no es el adecuado como registro central con
   varios escritores.
2. **Trazabilidad:** se retenían conceptos y recargos, pero no un historial inmutable de
   decisiones humanas, correcciones, versión de prompt/esquema/modelo ni la ubicación
   durable del PDF original.
3. **Calidad:** la validación aritmética evitaba que una factura inconsistente entrara al
   análisis, pero una factura que cerraba podía impactar el tablero sin revisión humana.
4. **Operación:** las alertas informaban una anomalía, pero no tenían responsable,
   vencimiento, evidencia ni resolución dentro de la herramienta.
5. **Seguridad:** la contraseña compartida era una barrera básica, no identidad
   individual ni trazabilidad de acceso.
6. **Uso de IA:** Gemini con JSON Schema reduce errores de formato, pero no valida la
   semántica financiera. El tier gratuito puede usar contenido para mejorar productos;
   el piloto fue expresamente aprobado bajo esa condición y debe conservar visibilidad de
   uso y cuota.

## Recomendaciones priorizadas

| Prioridad | Iniciativa | Resultado esperado |
|---|---|---|
| P0 | PostgreSQL administrado + objetos privados | No perder PDF, factura, decisión ni caso ante un reinicio. |
| P0 | Estados y revisión humana | Solo los comprobantes aprobados afectan evolución, alertas y Excel. |
| P0 | Casos de alerta | Cada anomalía tiene responsable, estado, vencimiento y evidencia. |
| P1 | Métricas por proveedor | Decidir con evidencia cuándo ajustar aliases, prompts o reglas locales. |
| P1 | Composición del total | Diferenciar consumo, impuestos, recargos, créditos y total pagable. |
| P2 | Contratos y presupuestos | Comparar contra lo pactado además de comparar contra meses previos. |

## Diseño recomendado

### Registro maestro y evidencia

Usar PostgreSQL administrado como registro maestro y un bucket privado compatible con S3
para los PDFs. DuckDB puede mantenerse para desarrollo, fixtures y análisis local, pero no
como fuente de verdad del tablero desplegado. El esquema debe preservar hash de documento,
URI de evidencia, resultado de extracción, versión de modelo/prompt/esquema, validaciones,
correcciones, actor y decisión.

La restauración de backups debe verificarse mensualmente. No alcanza con declarar una
retención: la aceptación es poder restaurar y reconciliar un lote de facturas.

### Flujo de aprobación

El ciclo recomendado es:

```text
recibida → extraída → requiere revisión → aprobada | rechazada | cuarentena
```

La cuarentena conserva el motivo y la evidencia. Una revisión puede confirmar o corregir
la cabecera, con valor anterior, valor nuevo, motivo y actor. Los cálculos ejecutivos
consultan exclusivamente registros aprobados.

### Alertas como trabajo operativo

Una alerta deja de ser solo un mensaje y se transforma en un caso deduplicado. El caso
incluye tipo, severidad, referencia de factura o comparación, responsable, vencimiento,
estado (`abierto`, `en_analisis`, `resuelto`, `descartado`) y evidencia de cierre. La
automatización de notificaciones se posterga: primero debe demostrarse que la cola tiene
severidad, reglas y propietarios correctos.

### Seguridad e identidad

Migrar a OpenID Connect y roles de cargador, revisor, responsable y administrador. Los
eventos relevantes se registran sin contraseñas, API keys ni contenido sensible en logs.
La contraseña compartida puede servir transitoriamente para el piloto, pero no debe
presentarse como control de acceso suficiente.

### Calidad de extracción y análisis

Mantener Gemini como extractor estructurado y la validación determinística como última
palabra. Medir por proveedor: aprobación sin corrección, cuarentenas por causa, campos
corregidos, conceptos sin homologar, empates, tiempo de revisión y consumo de cuota.

El IPC oficial debe conservar fecha de descarga, serie y metodología. Es un benchmark de
inflación, no un sustituto de la evolución tarifaria de un servicio específico. Para el
siguiente ciclo, configurar por proveedor la periodicidad real, unidad, suministro,
vencimientos y reglas de recargo; con facturas reales se podrán resolver los casos aún
diferidos de formatos monetarios y facturación bimestral.

## Qué no conviene priorizar todavía

- Ingesta automática por correo o carpeta vigilada: la carga manual revisada ofrece mejor
  evidencia para calibrar el primer lote.
- Forecasting o detección estadística avanzada: sin historia aprobada suficiente, genera
  confianza aparente.
- Integración directa con ARCA: la documentación pública de factura electrónica se orienta
  principalmente a emisión de comprobantes; no sustituye la captura y revisión de PDFs de
  proveedores.
- Multicliente: el piloto se optimiza para una organización. Separar clientes pasa a ser
  requisito cuando esa expansión sea una decisión comercial real.

## Criterios de aceptación del piloto

- Un reinicio no pierde documentación, datos aprobados, decisiones ni casos.
- Cada cifra del tablero puede reconstruirse desde el PDF, extracción, validaciones y
  correcciones.
- Un comprobante pendiente, rechazado o en cuarentena no altera evolución, alertas ni
  Excel ejecutivo.
- Cada alerta relevante queda asignable y conserva evidencia de resolución.
- Antes de ampliar usuarios, datos sensibles o alcance, se revisan la política del tier de
  IA, uso de cuota y efectividad de los controles.

## Fuentes consultadas

- [Streamlit — persistencia de almacenamiento local](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)
- [DuckDB — modelo de concurrencia](https://duckdb.org/docs/current/connect/concurrency)
- [Gemini API — precios y tratamiento de datos por tier](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API — Structured Outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Streamlit — autenticación OIDC](https://docs.streamlit.io/develop/api-reference/user/st.login)
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)
- [INDEC — IPC nacional](https://www.indec.gob.ar/indec/web/Nivel4-Tema-3-5-31)
- [ARCA — web services de factura electrónica](https://arca.gob.ar/ws/documentacion/ws-factura-electronica.asp)
