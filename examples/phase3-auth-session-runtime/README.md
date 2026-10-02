# Entorno de sesiones — preparación técnica

Este workspace fija Node 22.18.0, npm 10.9.3 y 19 dependencias directas exactas
en backend/frontend. Un único lock v3 de npm cubre ambos workspaces; no se
combinan locks independientes. Auth depende de @node-rs/argon2 2.2.1, jose
6.2.12 y @fastify/cookie 11.1.2. El frontend incorpora libphonenumber-js 1.13.14.

`runtime-input-check.mjs` y `runtime-loader-probe.ts` son fixtures de Codex que
verifican versiones, resolución, imports y ejecución desde raíz readonly. No
implementan el producto. `backend/src/auth.ts` y `frontend/src/AuthPages.tsx`
quedan ausentes hasta recibir propuestas originales CrewAI/Ollama revisadas.

La instalación calificada usa tarballs íntegros del registro oficial y npm ci
offline/ignore-scripts dentro de Docker. UID1000, network none, sin puertos ni
montajes del host, raíz readonly, sin capacidades, no-new-privileges, CPU0.8,
RAM1GiB/128pids y tmpfs noexec. Los paquetes instalados se extraen por tar y se
copian a una imagen readonly: el cargador nativo no se ejecuta desde tmpfs.
Los recursos temporales se eliminan sólo después de verificar su nonce propio.

`schema.sql` y las nueve pruebas de `auth-acceptance.test.mjs` conservan los
bytes del scaffold anterior. Las pruebas A01/A02/A03/A24 siguen sin ejecutar:
no hay fuente auth ni proveedor/rutas UI integrados. El comando previsto es
`node --import tsx --test auth-acceptance.test.mjs`, con base exclusivamente
`age50_auth_test` y credenciales separadas app/fixture. Ninguna credencial se
incluye aquí.

La calificación de PostgreSQL verificó por TCP un servidor final descartable,
bootstrap atómico único, autenticación real del rol app sin privilegios
administrativos, rollback, commit de revocación y bloqueo concurrente de la fila
users. Rechazó DELETE/TRUNCATE/users, cambios email/birth_date, DDL, escalamiento
de rol y tablas temporales. Fixture puede resetear su propia base. Los comandos
de comprobación son fixtures confiables; todavía falta conectar el ejecutor
aislado de fuentes generadas exclusivamente con credenciales de aplicación.

Faltan wiring API/UI, pruebas Chromium HTTPS con cadena confiada, manifest de
ejecución auth que consuma revisiones originales y cola durable entre incrementos.
No hay despacho habilitado ni aceptación de registro/login. La preview HTTP
existente continúa con el directorio sintético; no habilita cookies Secure allí.
