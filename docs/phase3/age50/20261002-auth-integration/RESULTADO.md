# AGE-32/50 — conexión auth preparada y calificada

Codex añadió adaptador Fastify/Pool/config, bootstrap separado, AuthShell con
sesión sólo en memoria/routing/HTTPS/logout y gateway HTTPS para el bundle/API.
Los módulos auth.ts yAuthPages.tsx siguen reservados a originales CrewAI/Ollama;
no se implementaron manualmente ni se ejecutaron propuestas nuevas.

**17 comprobaciones nuevas verdes**:4adapter,9Chromium con Pages/logout API
fixtures,4gateway con backend fixture. Tipos de los scaffolds verificados,
bundle fixture construido. Source/product entries permanecen sin compilar
hasta materializar originales auth y App/style público. Se previenen callbacks
de otra pantalla y logout anterior que borrase sesión nueva. El proxy preserva
Origin/cookies/Bearer/body/respuestas, quita headers de forwarding y no sirve
archivos fuera del static root. Bootstrap usa sólo secretos del sandbox propio.

Scaffold:84719ms/360000ms;gateway:4391ms/120000ms.
Seis+dos recursos temporales limpiados con nonce yexit0. Docker readonly/no-network/
sin ports/hostbinds/hooks, claves sólo stdin/tmpfs y HOME/NSS de browser efímero.
Lock/pins/cache/herramientas se reutilizaron íntegros, no se repitió calificación
Argon2/JWT/TLS aceptada ni presupuesto agotado de perfil. No fallos/reintentos.

Cinco casos de navegador del PRODUCTO fijados y sintaxis verificada; no ejecutados.
Nueve PG/API originales sin cambios y sin ejecutar contra módulo auth. Los fixture
Pages/API no acreditan edad/login/revocación/rotación/seguridad del módulo real,
ni UI06phone/UI09persistencia. Directorio se inyectará desde snapshot original;
gestión de perfil queda aviso explícito pendiente. No auth habilitada en preview.

Falta manifest auth separado que ligue originales/seed/tests/locks/runtime/roles,
ejecutor PG/API/gateway/browser con keyTLS fuera del worker generado y cola durable
antes de despacho local. Luego propuestas, revisión y aceptación real. No se
amplió sandbox niworkflow de directorio, ni se modificó controlador activo.
AGE32/50 InProgress; discrepancia perfil intacta; no se declara SaaS autónomo/Done.
Linear pendiente de autorización humana tras rechazo automático previo.
