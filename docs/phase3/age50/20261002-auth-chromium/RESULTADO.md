# AGE-32/50 — Chromium HTTPS confiable

**Ocho comprobaciones de navegador aprobadas** en 14657ms/240000ms.
Infraestructura de Codex, no aceptación de una fuente auth. Chromium141.0.7390.37
en contenedor sin red/puertos/hostmounts y Playwright1.56.1 del lock calificado.
Instancia sin CA rechaza; HOME/NSS efímero con CA específica acepta HTTPS;
hostname incorrecto se rechaza. Cookie sintética:Secure/HttpOnly/Lax/path/TTL,
oculta a JS y enviada sólo a ruta correspondiente. No se desactiva validación.

Confianza aislada según [guía oficial Chromium/NSS](https://chromium.googlesource.com/chromium/src.git/+/master/docs/linux/cert_management.md).
No se modifican stores Windows ni navegador personal. Herramienta libnss3-tools
desde Ubuntu noble oficial:InRelease firmado verificado con gpgv, índice ydeb
ligados por SHA256/size; sin instalación/hooks ni actualizaciones del sistema.
Key sólo stdin/tmpfs privado0400; CA/cert públicos en fixture existente; nada
privado publicado. Cuatro recursos limpiados y cero fallos/reintentos inciertos.

Las comprobaciones usan cookie sintética y servidor confiable del operador.
No prueban login del agente, JWT/revocación/rotación contraDB ni la interfaz;
no prueban SameSite cross-site o Secure negativo sobre HTTPlocalhost. Cero llamadas
de modelos, ningún presupuesto reiniciado, ninguna nueva ronda de producto.
Preview HTTP existente permanece sin auth; no despliegue ni permisos cambiados.

Ya están calificados locks/runtime, DB/roles/locks y navegador HTTPS. Falta wiring
API/UI/provider ytests de producto, manifiesto auth que consuma originales y cola
durable antes de habilitar el siguiente flujo local. Auth.ts/AuthPages.tsx siguen
ausentes. AGE32/50 In Progress; discrepancia perfil/budget intactos. Linear
continúa pendiente de autorización explícita tras rechazo automático anterior.
