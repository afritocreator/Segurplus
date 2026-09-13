# ADR-003: PostgreSQL y objetos privados para el piloto operativo

**Estado**: aceptado. **Fecha**: 2026-09-13.

## Decisión

La fuente de verdad de producción es PostgreSQL administrado, configurado en
`DATABASE_URL`. El PDF original se conserva en un bucket S3 compatible privado,
configurado por `S3_BUCKET`; `S3_ENDPOINT_URL` permite proveedores compatibles.

DuckDB queda como modo local para desarrollo, fixtures y análisis aislado. No se
considera durable cuando la app está desplegada en Streamlit Community Cloud.

## Seguridad y operación

- El bucket no recibe ACL pública; cada carga pide cifrado de servidor AES-256.
- Secrets y URLs se configuran fuera del repositorio.
- El proveedor administrado debe retener backups diarios durante 30 días y permitir
  una prueba mensual de restauración. Esa prueba se registra fuera de la app.
- Sin `DATABASE_URL` y `S3_BUCKET`, la aplicación advierte modo local y no se la debe
  usar como registro operativo cotidiano.

## Consecuencia

Se agregan `psycopg` y `boto3`, con imports diferidos para no forzar servicios externos
en tests ni desarrollo local. La capa de almacenamiento conserva SQL compatible con
DuckDB para las fixtures existentes.
