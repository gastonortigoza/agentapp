# AGE-36 — evolución y recuperación sintética

La migración001 instala el contrato inicial. `002_directory_index.sql` agrega un índice parcial para la consulta geográfica del directorio sin cambiar datos, columnas, DTOs o reglas de negocio. `age36_recovery.py` aplica esta evolución y realiza el ensayo completo exclusivamente en agentapp_age34_test con etiquetas/marcador de propiedad correctos.

La versión2 se aplica en transacción con advisory lock34001,lock_timeout5s y statement_timeout30s. Antes de ejecutar comprueba revisión1 y checksum de cualquier revisión2 existente. El código SQL y el registro de versión se confirman juntos; un checksum diferente rechaza la actualización. Dos migradores concurrentes de versión2 se serializan: uno aplica y el otro observa already_applied. El arranque del contenedor no migra automáticamente.

La evolución es aditiva: consultas/INSERTs de la revisión1 siguen usando las mismas columnas y restricciones. No hay renombres, eliminación, cambio de tipo o backfill. Expand-contract no añade valor en este cambio; se conserva el contrato anterior. Esta justificación fue revisada por Codex. No se afirma coexistencia real de dos APIs porque todavía no hay aplicación implementada. Una evolución incompatible futura requiere ensayo específico de expand/backfill/coexistencia/contract y aprobación antes de destruir datos.

El ensayo instala desde vacío, aplica2 sobre fixtures, repite sin recrear, inyecta error después de crear un índice y registrar versión3, verifica rollback de ambos, prueba dos migradores y rechaza drift. Los tiempos de cada criterio y límites se guardan en la evidencia. El índice se crea de forma transaccional ordinaria sobre tablas sintéticas pequeñas; no se presenta como prueba de CREATE INDEX CONCURRENTLY ni ventana de migración bajo carga de producción.

Recuperación lógica: `pg_dump --data-only --schema=agentapp --no-owner --no-acl`, usando sólo el rol migrador y credencial local referenciada. El archivo sintético queda en `%LOCALAPPDATA%/AgentApp/age34/recovery/synthetic-data.sql`, fuera del repositorio y del ZIP. Se conserva hash del archivo; no contiene roles ni marcador externo de propiedad. Antes de restaurar se rechazan nombres ajenos y checksum alterado. Se reconstruye el esquema con las migraciones exactas1/2 y luego se restaura la instantánea de datos en una transacción. La metadata de versiones se restaura también.

Se compara un SHA256 calculado sobre todas las filas de las once tablas y schema_migrations antes del backup y después del restore, incluidos hashes, fechas, respuestas persistidas y configuración. Se comprueba que el índice2 existe y la consulta de la revisión anterior conserva el perfil sintético. Borrar un perfil de fixture antes del restore demuestra recuperación, no sólo una importación sobre datos intactos.

Revertir una imagen o borrar el índice **no revierte datos**. Este ensayo no autoriza restaurar un entorno real. Para staging faltan destino/retención/cifrado aprobados, backup fuera del host, restore de fotos/objetos, monitoreo y prueba de carga. Esos recursos siguen bajo AGE-33/35; la recuperación local de PostgreSQL no declara staging listo.

Codex escribió y verificó la evolución/recuperación como supervisión confiable. No atribuir este ensayo a un ciclo autónomo de los agentes ni a una aceptación HTTP/E2E.
