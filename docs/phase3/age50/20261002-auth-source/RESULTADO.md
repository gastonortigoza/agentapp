# AGE-32/50 — soporte de revisión auth y scaffold de aceptación

Registro y acceso todavía no están implementados ni habilitados. El controlador
de propuesta ahora admite revisiones separadas auth_api y auth_pages con paths
fijos, reglas ligadas al contrato, citas exactas, límites y originales durables.
No amplía el sandbox de directorio ni los tres módulos que éste puede sustituir.

26 pruebas nuevas califican rechazo de rutas/citas falsas, acceso al host/logs,
persistencia de tokens y la secuencia de corrección con transport simulado. No
son llamadas reales al modelo ni pruebas de auth de producto. Suite completa:
618passed/1skipped, recibo f4062caaac1cad2d185b544e6383eac80e18bb5f087393024718b656d7510555.
Se conserva el primer fallo de impresión cp1252 y el resultado3failed/615passed/
1skipped: el prompt leía /api/auth_rule; el contrato tiene /auth. Corrección exacta,
26 nuevas pruebas verdes y suite completa verde después.

Scaffold examples/phase3-auth-session-scaffold contiene nueve pruebas PG/API
fijas, con frontera18+ en fecha civil argentina, normalización, hash/JWT/cookie,
credenciales/rate limit, rotación/reuso/concurrencia, Origin y revocación/logout.
Sintaxis verificada; no ejecutadas, falta source/runtime/provisión/locks/wiring.
DB propuesta age50_auth_test, app/fixture/migrador separados y mínimos permisos;
no se cambiaron los permisos de bases existentes ni el SELECT-only de preview.

HTTPS de infraestructura: CA y certificado desechables localhost/127.0.0.1,
cuatro comprobaciones con validación TLS activada (cadena+hostname+HTTP200,
headers Secure/HttpOnly/Lax/Path/TTL, rechazo hostname erróneo y CA no confiada).
El segundo probe captura socket.authorized antes de cerrar; original preservado.
Servidor efímero cerrado. Claves privadas no publicadas, CA privada no guardada,
stores Windows/navegador intactos. Esto no comprueba cookies de navegador ni auth.

Auth sigue dispatch_enabled=false; faltan workspace/locks, originales reales,
PG y Chromium con TLS confiado, runtime/manifest y cola entre incrementos. El
controlador activo E:/IA/projects/local-ai-lab no se modificó. Se conservó la
incertidumbre histórica age50-handoff-r5-api-20261001 sin reenviar/consumirla.
Ensayo de carrera de perfil sigue en discrepancia con presupuesto3ejecuciones;
ningún presupuesto reiniciado. AGE32/50 In Progress; no merge ni SaaS completo.
