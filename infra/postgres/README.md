# AGE-34 — PostgreSQL local descartable

Este paquete implementa persistencia local de desarrollo/pruebas para el contrato técnico v1 de AGE-31. No implementa API/FE, auth, geografía oficial, integración Rebill ni staging. Referencia: [contrato PR7](https://github.com/gastonortigoza/agentapp/pull/7), contrato SHA256 `c94f752ed8b08eefb16fa5dc840e8c04fd947070e773cd3139d7bbd7465475dc`.

## Reproducción en Windows

Requisitos observados: Python3.12.14 y Docker Engine accesible. Driver/tests usan únicamente Python estándar y el cliente Docker; no usan dependencias nuevas del controlador. Desde esta carpeta, usando un Python3.12 disponible:

```powershell
docker pull postgres@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722
python age34_driver.py start
python age34_driver.py migrate --target dev
python age34_driver.py migrate --target test
python age34_driver.py seed --target dev
python age34_driver.py seed --target test
python test_age34.py
python age36_recovery.py
python age34_driver.py status --target test
```

La imagen validada ejecuta PostgreSQL18.6. El driver usa `--pull never`; una imagen ausente se descarga explícitamente por su digest antes del arranque. Start no aplica migraciones ni borra datos. Migración y seed son pasos separados y repetibles; tests/recuperación **resetean únicamente la base descartable test**.

Containers `agentapp-age34-dev/test`, bases `agentapp_age34_dev/test`, puertos `127.0.0.1:55434/55435`, volúmenes separados y red Docker `agentapp-age34-local` interna. El servicio PostgreSQL también usa `agentapp-age34-host`, un bridge exclusivo para publicar los puertos de supervisión en loopback. El código generado nunca se conecta a este bridge; no se afirma que el servicio PostgreSQL carezca de toda salida de red. Crear el contenedor inicialmente en el bridge interno no publicó los puertos en Docker Desktop; se recrearon sólo los dos contenedores propios, conservando volúmenes y comprobando la huella completa de datos. Por contenedor:512MiB,1CPU,128pids; healthcheck pg_isready2s/reintentos30; el arranque además comprueba conexión TCP autenticada. Volumen PG18 montado en `/var/lib/postgresql`.

Secretos generados por supervisión y referenciados desde `%LOCALAPPDATA%/AgentApp/age34/credentials.json`, con archivos de entorno locales para el primer arranque. No se incluyen valores en el paquete, evidencias, argv ni tokens para el agente. Es un almacén local de este usuario, no un mecanismo de secretos de producción; su acceso debe permanecer restringido. Si existe un contenedor con nombres coincidentes sin la etiqueta de propiedad o imagen correcta, o el marcador de instancia no coincide con el estado local, el driver rechaza reutilizarlo. No copiar los secretos entre entornos.

Roles LOGIN sin superusuario, CREATEDB, CREATEROLE ni replicación: `age34_app` para CRUD de tablas operativas y sólo lectura del catálogo; `age34_migrator` propietario del esquema, separado. La aplicación no puede crear esquema/tablas, editar planes/geografía, leer metadatos de migración o marcador de control, ni conectarse a la base postgres. Esta separación no sustituye la autorización de propietarios de la futura API; no se afirma RLS ni autenticación HTTP.

Reset explícito:

```powershell
python age34_driver.py reset --target test --database agentapp_age34_test
python age34_driver.py seed --target test
```

Antes de borrar el esquema exige target test, nombre exacto, etiquetas AGE-34/descartable, imagen y UUID de propiedad almacenado en una tabla de control fuera del esquema reseteable. Dev, nombres ajenos/productivos, falta de nombre y marcador divergente se rechazan. El reset recrea los permisos por defecto del nuevo esquema antes de migrar. No borra bases, volúmenes ni servicios de otros proyectos.

## Modelo y límites

Once tablas y restricciones relacionales, FK geográfica compuesta, una foto principal mediante índice parcial, PK idempotency(user_id,key) y hash sin UNIQUE global. Precios snapshot en centavos ARS:basic1500000/promoted3000000. Fechas y respuestas JSONB persistidas. Fechas de nacimiento privadas en users; teléfono en profiles; plan_id es TEXT.

Las semillas son íntegramente sintéticas: nombres de provincias/zonas inventados, emails example.invalid y fechas adultas de fixture. `NOT_A_LOGIN_HASH_SYNTHETIC_ONLY` no es un hash válido de login. No cargar estas semillas como catálogo oficial ni hacer comunicaciones/cobros con ellas.

El ejemplo SQL de activación de los tests verifica almacenamiento/transacciones y bloqueo users, **no endpoint HTTP** ni su guard production, parsing, autenticación, catálogo completo o E2E. La hora se captura una sola vez después de adquirir users FOR UPDATE; usar la hora de BEGIN antes de esperar produjo dos201 concurrentes en el ensayo conservado. Las respuestas201 guardadas se repiten íntegramente; payload distinto revierte409; llave de otro usuario es independiente; otro plan activo no crea fila/error persistido; expiración permite período nuevo.

La migración001 se aplica en transacción con lock_timeout5s,statement_timeout30s y advisory lock34001; el checksum registrado rechaza drift y una repetición no recrea el esquema. El driver inicial no promete una instalación concurrente ilimitada: el aprovisionamiento inicial y las migraciones son pasos de supervisión separados. AGE-36 ensaya la evolución002 y dos migradores sobre la revisión1 ya preparada.

MCP SQL no se usa ni se habilitó. SQL lo ejecuta supervisión confiable sobre recursos locales fijos; el modelo no tiene herramientas, secretos ni Docker socket.

## Autoría y evidencias

CrewAI/Ollama local,qwen3.8:27b-q4_K_M produjo un borrador en una llamada1779/1410 tokens, con límite1call/context16384/output4000/timeout180s. Studio `age34-sql-draft-20261001`. Codex auditó y corrigió email/hashes/status, índices, roles/provisión, permisos de reset y el ejemplo de concurrencia. No se presenta como autocorrección autónoma de AGE-50.

`evidence/` conserva borrador original, recibo de inferencia, resultados y fallos anteriores. Ver el informe de continuidad para relacionar las pruebas con sus alcances. El primer intento falló por permisos tras reset; la segunda pasada aprobó16/17 y encontró la carrera de timestamp; los seis casos afectados pasaron después de la corrección (244.422s), incluido reinicio. Los seis checks de evolución/recuperación pasaron (314.156s). Ambos puertos respondieron al protocolo PostgreSQL desde Windows; las conexiones autenticadas se probaron por TCP dentro de cada contenedor. Dev quedó en revisión1 y test en revisión2; la evolución2 es aditiva. Hashes del paquete sobre bytes, .gitattributes evita conversión de saltos de línea al clonar; las migraciones auditadas se entregan con LF. Estos resultados no certifican API/FE/E2E ni carga de staging.
