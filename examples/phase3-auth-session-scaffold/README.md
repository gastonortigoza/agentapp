# Scaffold de aceptación del núcleo de sesiones

Autoría: Codex, infraestructura y pruebas fijas. No implementa registro/login.
`auth-acceptance.test.mjs` importa el futuro original revisado
`backend/src/auth.ts`; ahora sólo se verificó la sintaxis del harness.
No ejecutado contra producto ni PostgreSQL. No hay candidato aceptado, ni cola
auth habilitada, ni permisos nuevos aplicados a bases existentes.

Contrato congelado SHA256:
`c94f752ed8b08eefb16fa5dc840e8c04fd947070e773cd3139d7bbd7465475dc`.
Rutas y estados de registro/login/refresh/logout se conservan; el fallo de CSRF
usa401, uno de los errores previstos para refresh, sin agregar403 al contrato.

Nueve casos fijos: edad18 en fecha civil argentina versus UTC, cero filas ante
rechazo, normalización/duplicado y campos extra, hash/JWT/cookie/refresh persistido,
credenciales genéricas y sexto fallo por IP, rotación/reuso/revocación de familia,
rotación concurrente, Origin y logout/acceso/expiración. La ruta
`/__acceptance/owner` pertenece únicamente al harness; no debe copiarse al producto.
Cookie por Fastify.inject no prueba cookies de un navegador: falta Chromium HTTPS.

`schema.sql` deriva sólo users/sessions del contrato. Usar únicamente una nueva
base desechable `age50_auth_test`, roles distintos migrador/app/fixture, todos
sin superuser/CREATEDB/CREATEROLE. Rol app permite SELECT/INSERT usuarios y
UPDATE(id) exclusivamente para locks de fila; sesiones SELECT/INSERT/UPDATE.
Fixture permite cleanup propio. No reutilizar el SELECT-only de la vista previa.
Provisionar passwords fuera de archivos publicados y referenciarlos por env-file.
No ejecutar este SQL en AGE34, en el preview ni en el controlador activo.

Antes de ejecutar: congelar package/locks workspace y tarballs oficiales con
los pins ya calificados; agregar wiring/scaffold del API y UI; construir imagen
COPY-only con módulos nativos en raíz readonly; límites de recursos/red y diario
de intentos; consumir originales auth_api/auth_pages de la revisión registrada.
No usar tmpfs noexec para cargar el binario nativo Argon2.

TLS: hay CA/cert local y prueba de HTTPS con validación de hostname y cadena,
sin modificar stores Windows/navegador. Los privados permanecen sólo en este
workspace, excluidos de publicación. Todavía falta el entorno Chromium con
cadena confiada explícitamente y prueba real Secure/HttpOnly/rotación, además
de formularios a360/1280px. No desactivar validación de certificados ni bajar
Secure para habilitar auth sobre la HTTP de la vista previa.

Registro de review-only auth no autoriza materialización/ejecución ni altera
las tres secciones del sandbox directorio. Preparar gate exacto de incremento
antes de encadenar agentes y aceptación; conservar presupuestos e incertidumbres
previas. Reset/Mailpit, gestión de perfil, fotos y activación siguen separados.
