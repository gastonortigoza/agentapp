# AGE-32/50 — preparación de registro/sesiones

Se avanza con un alcance nuevo del contrato congelado, sin repetir el directorio
aceptado ni reiniciar el ensayo de perfil agotado. Esta preparación es de Codex;
no acredita propuestas CrewAI/Ollama ni implementación del SaaS.

## Dependencias verificadas

Pins exactos observados en el registro oficial: @node-rs/argon2 2.2.1,
jose6.2.12, libphonenumber-js1.13.14 y @fastify/cookie11.1.2, sobre Fastify5.6.1.
Node22.18.0/npm10.9.3 y lockfilev3. Metadata, tarballs/integrity y lock quedan
ligados por hashes. El host sólo resolvió el lock con package-lock-only e
ignore-scripts; no instaló ni ejecutó las librerías. Adquisición registry.npmjs.org
con redirects rechazados, tarballs acotados/verificados y filtro linux-x64-glibc.

La instalación efectiva fue offline con ignore-scripts en Docker sin interfaces,
puertos ni host mounts, UID1000, raíz de sólo lectura, sin capacidades, CPU0.8,
RAM1GiB y128pids. El loader nativo no puede mapear el binario desde tmpfs noexec:
el primer resultado conserva ese fallo. El segundo conserva el fallo de docker cp
sobre tmpfs; no es una incompatibilidad de librería. La corrección extrae los
paquetes con tar desde el proceso y los copia a una imagen derivada sin RUN/ADD;
ejecución nativa desde la imagen de sólo lectura, tmpfs/tmp aún noexec. Ningún
script de instalación ni conexión de red se habilitó para hacerlos pasar.

Resultado final: **9 comprobaciones de dependencias pasadas**: Argon2id
v19 m65536/t3/p1 con salts aleatorias y contraseña incorrecta; JWT con firma,
issuer/audience/sid/exp900 y rechazo de clave/issuer/audience/exp incorrectos;
metadatos completos clasifican el móvil AR sintético y distinguen línea fija;
cookie Fastify emite HttpOnly/Secure/SameSiteLax/Path/api/auth/MaxAge604800.

Tres expedientes de preparación conservados, consumo agregado43407ms frente a
límite240s. Cero llamadas de modelos y cero pruebas del producto. Cleanup de
2+2+4 recursos confirmado; AGE34-dev/test y Qdrant conservados. qualification.json
y sus dos originales conservan comandos, resultados y procedencia. No se
descargaron certificados ni se cambiaron permisos del controlador activo.

## Siguiente incremento

auth-session-core-input.json deriva rutas/register/login/refresh/logout,
Session/tablas/users/sessions y A01/A02/A03/A24/UI06/UI10 del contrato, con su
hash. Incluye prerequisitos todavía no verificados; **no está listo para despacho**.
next-increments.json ordena sesión, reset, dueño, fotos, activación y catálogo,
con dependencias/criterios originales. Es una preparación de la cola: aún no es
la cola durable ejecutable. Cada aceptación continúa specified_not_executed.

Faltan módulo tipado de auth, HTTPS local/cookies en navegador, aislamiento de
DB para nuevas operaciones, fixtures transaccionales/rate-limit/revocación y
pruebas FE/API/PG/Chromium antes de habilitar propuestas y ejecución de agentes.
La comprobación de cookie sólo valida serialización/parsing, no un browser HTTPS.
JWT de librería no acredita revocación persistida ni protección de rutas.
Las nueve pruebas no prueban registro/login y no cierran AGE32/50.

El fallo de carrera del perfil permanece registrado como discrepancia del
expediente anterior. No se ha reiniciado su presupuesto ni parcheado el producto.

## Fuentes primarias

- [Argon2 Rust bindings](https://github.com/napi-rs/node-rs/blob/main/packages/argon2/README.md)
- [jose JWT](https://github.com/panva/jose)
- [Metadatos completos de libphonenumber-js](https://github.com/catamphetamine/libphonenumber-js/blob/master/README.md)
- [Plugin oficial Fastify cookies](https://github.com/fastify/fastify-cookie)

SHA256SUMS.json cataloga los bytes del informe/entradas/evidencia. El lock y
qualification.test.mjs reproducible están en examples/phase3-auth-preparation.
