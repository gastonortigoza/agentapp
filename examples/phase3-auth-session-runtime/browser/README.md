# Chromium HTTPS — fixture de infraestructura

El fixture usa Chromium141.0.7390.37/Playwright1.56.1 en la imagen previamente
fijada. No implementa registro/login. El servidor devuelve una cookie sintética,
no una credencial de producto. El contexto y el HOME/NSS son nuevos y descartables.

El driver verifica InRelease con gpgv y el keyring Ubuntu del digest del browser,
liga Packages.xz por SHA256 y libnss3-tools2:3.98-1build1 por SHA256/size. Extrae
certutil sin dpkg install ni hooks y lo copia a una imagen readonly. CA/cert son
públicos del fixture anterior; key se entrega sólo por stdin a tmpfs0400, nunca
a la imagen, Git o logs. El CAprivatekey no existe guardado. NSS recibe sólo la
CA específica para SSL C,, en /tmp/trusted-browser/.pki/nssdb. No modifica stores
Windows ni el navegador personal. Baseline separado sin CA rechaza la conexión.

Ocho comprobaciones:CAno confiada rechazada; HTTPS200/securecontext con CA;
Secure/HttpOnly/Lax/Path/TTL;document.cookie vacío en ruta coincidente;cookie
enviada por fetchHTTPS;no enviada fuera de /api/auth;hostname erróneo rechazado;
ausencia de flags que deshabilitan validación. No se activa ignoreHTTPSErrors,
ignore-certificate-errors, allow-insecure-localhost ni SPKI bypass.

No se afirma rechazo de Secure cookies sobre HTTP localhost: los browsers pueden
darle tratamiento especial. Tampoco se acredita SameSite cross-site, login,
rotación/revocation DB o HttpOnly de una implementación auth todavía ausente.
El executor de producto tendrá que aplicar este aislamiento y repetir los casos
contra originales revisados; eso será una ejecución de producto nueva y acotada.

Ningún puerto/bind/red externa;root readonly/UID1000/capsdropALL/no-new-privileges,
CPU0.8/1.5GiB/128pids, /tmp512MiB noexec. Cuatro recursos se limpiaron con nonce.
El intent durable prohíbe repetir el driver de calificación ya completado.

Método de confianza basado en [documentación oficial de Chromium](https://chromium.googlesource.com/chromium/src.git/+/master/docs/linux/cert_management.md).
