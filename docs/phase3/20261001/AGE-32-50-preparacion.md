# AGE-32/50 — preparación y bloqueos, 01/10/2026

Referencia vigente: contrato técnico v1 de [PR7](https://github.com/gastonortigoza/agentapp/pull/7), SHA `970aa9085cc4df0b1ed314babf666cee6e050cd7`, contrato `c94f752ed8b08eefb16fa5dc840e8c04fd947070e773cd3139d7bbd7465475dc`. Se conserva la política firmada por hash y el manifiesto **enabled=false/deployment=disabled**. Las entregas locales no alteran ese documento congelado.

AGE-30 tiene instalación limpia y suite aprobada; [PR8](https://github.com/gastonortigoza/agentapp/pull/8) permite revisión humana del mismo snapshot PR5. Main sigue en `9c02be1d1ce9465eb2338ac3981d21f2a2c1009c`; todavía no contiene la base integrada PR5/6. No se habilita ni modifica el controlador activo, que contiene cambios locales anteriores de otros chats.

## Preparación de AGE-32

En la base integrada se debe congelar una revisión nueva del manifiesto ligada a este requisito, su contrato, política y plan de archivos. El plan inicial conserva `frontend/`, `backend/`, `infra/postgres/` y pruebas por componente. La entrega PostgreSQL de AGE-34 puede incorporarse después de revisión con su digest, hashes y secretos referenciados fuera del repositorio. No introducir los antiguos nombres UUID plan_id, /api/me/profiles o fechas start_at/end_at.

Faltan root/frontend/backend package.json, scripts reales, lockfiles npm y versiones comprobadas de React/Vite/Fastify/pg/sharp/argon2/libphonenumber-js/Vitest/Playwright. Por eso no hay comandos FE/BE/build/E2E que puedan afirmarse como ejecutados. Node22.18.0/npm10.9.3 son observaciones históricas, no una autorización para usar sus versiones sin revisar engines/pins en la base integrada.

El ejecutor nuevo debe admitir únicamente argv/cwd/archivos y servicios enumerados en el plan aprobado; detectar rutas o dependencias no autorizadas, rechazar escapes y proteger fuentes/configuración del controlador. Instalación bloqueada durante una fase de preparación explícita; ejecución generada sin credenciales, Docker socket o red arbitraria. Validar hashes/runtime/modelo al reanudar y bloquear si cambiaron. El controlador actual sólo acepta pilotos fijos y no se convierte en ejecutor general ampliando sus permisos de forma implícita.

## Preparación de AGE-50

La revisión local 8/8 de ayer es asesora. No demuestra detecta→corrige→aprueba sin intervención. Los 26 tests documentales y 50 del adaptador v10 tampoco sustituyen pruebas del producto ni una evaluación independiente.

El ciclo que falta debe recibir JSON tipado con un esquema estricto; cada criterio de rechazo debe citar una cláusula del requisito congelado. Primero se fijan casos de aceptación y regresiones independientes; después se entrega el error verificable al agente, que debe corregir su propia salida y recibir una revisión nueva. Se conservan todos los intentos, límites, hashes y consumo. No aceptar filtros, TTL, rutas o condiciones comerciales inventadas por el revisor. Truncación, drift, exceso de presupuesto o envío incierto bloquean y no autorizan repetir a ciegas.

Caso nuevo útil descubierto en PostgreSQL: una activación que empezó su transacción antes puede adquirir el bloqueo users después de otra. El ejemplo de referencia debe capturar una hora única **después** del bloqueo; usar la hora de BEGIN puede permitir dos períodos porque el nuevo período aparece futuro respecto a la transacción que esperó. El ensayo PostgreSQL es evidencia de persistencia/bloqueo, no API, auth ni E2E. Registrar este caso en AGE-37 y en la evaluación independiente; la corrección de este chat fue de Codex, no autocorrección del modelo.

La ejecución Studio `age34-sql-draft-20261001` produjo un borrador SQL con una inferencia local (1779 tokens entrada/1410 salida). El proceso no ejecutó SQL ni usó herramientas del modelo. Codex supervisó permisos, provisión, correcciones y pruebas. El estado de ese borrador no certifica el cierre de AGE-32/50 ni el producto.

AGE-32 permanece pendiente de AGE-30 y de la adaptación; AGE-50 continúa abierto. Esta preparación no integra código ni habilita despliegues.
