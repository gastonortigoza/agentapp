# Conexión de sesiones — scaffold del operador

Estas fuentes son de Codex. CrewAI/Ollama deberán entregar originales revisados
únicamente en `backend/src/auth.ts` y `frontend/src/AuthPages.tsx`. No se copió
un fixture a esas rutas. Lock FE/BE y pins permanecen exactamente iguales.

`backend/src/session-app.ts` valida configuración HTTPS/key/issuer/audience,
inyecta el Pool y config sin reemplazar privilegios, desactiva logs y fija errores
400/503 con request_id opaco. `session-server.ts` carga sólo credenciales propias
del sandbox, importa registerAuth real y cierra servidor/pool. Es entrada de
sesiones; no sustituye el backend/directorio público ya aceptado ni su migración.
No se compiló la entrada real todavía porque auth.ts está ausente.

`AuthShell.tsx` recibe Pages tipadas y el directorio público. Mantiene Session
sólo en estado React, rechaza respuestas inválidas/callbacks de otra ruta y pasa
a /mi-perfil después de recibir sesión. Este corte deja allí un aviso explícito
de gestión pendiente; no acredita el formulario de perfil/phone/fotos. Logout
envía Bearer/credentials y espera204 o401, conserva sesión y permite retry en
fallo. Una respuesta de logout anterior no puede borrar una sesión nueva.
Auth sólo renderiza formularios por HTTPS; el directorio sigue accesible público.
No hace auto-refresh: la calificación de memoria tras reload no acredita UI09.

`session-main.tsx` es la entrada real futura: importa AuthPages y App/style del
snapshot original de directorio. Esos originales todavía deben materializarse
con procedencia por el manifiesto, que también fijará la importación del CSS.
No compila ahora porque no fueron suplidos.
Nunca usa FixturePages. `wiring.html`/`fixtures/wiring-main.tsx` tienen entry
distinta y explícita; sólo aparecen en la calificación del scaffold y deben
excluirse del entry/bundle/producto real. Los controles emiten tokens sintéticos.

`auth-gateway.mjs` es el proceso confiable que sirve el bundle por HTTPS9443 y
proxy /api al backend loopback3000. Config/CA/cert/key se inyectan; nada privado
se incluye aquí. La ejecución futura debe separar gateway del worker generado:
su clave TLS no será accesible desde el filesystem del backend/agentes.
No se confían headers Forwarded del caller. El backend observa la conexión del
proxy; el IP de cliente LAN real y su política de rate limit quedan pendientes
de calificar antes de activar auth en una preview compartida. No se habilitó
esa preview ni se cambiaron firewall/permisos/trust existentes.

Calificación efectuada:

- Tipos del adaptador, AuthShell y fixture; cuatro pruebas adapter con registrar
  de prueba explícito. No prueban register/login ni SQL real.
- Nueve casos Chromium141/HTTPS con Pages/APIlogout fixtures:root público,
  sesión en memoria/URL propia sin token renderizado, reload sin restaurar token,
  callback viejo y sesión inválida rechazados, logout/retry204, logout anterior
  no borra sesión de reemplazo, navegación360/1280 sinoverflow, authHTTP bloqueada.
- Cuatro casos de gateway con backend de prueba:SPA sin auth, transporte real
  de método/body/Origin/cookie/Bearer y flags Set-Cookie, eliminación de headers
  forwarding del caller, symlink/traversal sin acceso fuera del root.

17 comprobaciones nuevas verdes. No se requalificó Argon2/JWT ni la suite previa
de TLS; las dependencias y herramientas se reutilizan por hashes de recibos.
Seis recursos del scaffold y dos del gateway se eliminaron con nonce propio.
No hubo propuestas nuevas, rondas de agentes ni presupuesto viejo reiniciado.

`frontend/e2e/auth-session.spec.ts` fija CINCO casos del producto:root abierto,
minor422/adult201 y cookie real, login401 con retry, logout204/cookie borrada,
formularios360/1280/teclado. Sólo sintaxis comprobada, nunca ejecutados todavía.
Las nueve pruebas PG/API originales conservan bytes y siguen sin ejecutar.
UI06 parcial:phone/perfil pertenece al siguiente incremento; UI09 sigue pendiente.

Pendientes para despachar:manifest auth propio que ligue seed/locks/tests/roles/
originales y snapshots públicos, ejecución aislada PG/API/gateway/Chromium con
credenciales app/fixture separadas, cola durable entre incrementos y consumo/
reserva por operación. Sólo después se encadenarán propuestas locales, revisión
y aceptación real. No ampliar SECTIONS/PATHS del workflow de directorio agotado.
