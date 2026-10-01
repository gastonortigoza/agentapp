# AGE-33 — inventario y propuesta de staging, 01/10/2026

Propuesta pendiente de completar capacidad y aprobar recursos. No se aprovisionó staging.

## Evidencia vigente

Lectura autenticada mediante Browser de Coolify: [AgentApp](http://179.198.111.129:8000/project/ecoy01g724tjbobuz2dzikaw/environment/uwlwcitmahaeqcjzncgpr7bq), [servidor](http://179.198.111.129:8000/server/e5t1ifwljxwq8k0m79vjbzhq), métricas, destinos y listado de recursos. No se usó el túnel SSH ni se renovó su autorización vencida el 26/09.

Coolify 4.3.23. Proyecto exclusivo `ecoy01g724tjbobuz2dzikaw`: entorno **production vacío, 0 recursos**; no acredita un entorno staging. Servidor compartido localhost `e5t1ifwljxwq8k0m79vjbzhq`: Ubuntu 24.04.4 LTS, x86_64, kernel 6.8.0-137-generic, 4 núcleos, 15,6 GB RAM, Docker 29.7.1 y Compose 5.4.0. La pantalla muestra Ready; el indicador Attention required corresponde a **Proxy Update available**, con Sentinel In sync. No se actualizó ni validó la conexión desde botones de administración.

Las métricas están **deshabilitadas**. No se habilitaron. El inventario muestra un destino Standalone Docker llamado coolify y ocho recursos gestionados de otros proyectos: tres aplicaciones y un PostgreSQL de Event Manager System; tres aplicaciones y un PostgreSQL de ListaInvitados. Cinco figuran Running:healthy y tres Running:unknown. No se abrieron ni modificaron sus configuraciones. Su estado no demuestra consumo ni disponibilidad de CPU/RAM/disco.

Disco libre, capacidad/ocupación de volúmenes, límites y consumo reales, redes efectivas de contenedores, backups y destinos de recuperación siguen **sin verificar**. La interfaz de General mostró un aviso de cambios sin guardar aunque no se editaron campos; no se pulsó Save ni Reset. No se inspeccionaron claves, tokens ni variables compartidas.

## Propuesta concreta para revisión

Crear un entorno **staging nuevo dentro del mismo proyecto AgentApp**, sin duplicar el proyecto ni reutilizar production. Aplicación FE/API y PostgreSQL separados, red privada exclusiva para API/BD y recursos auxiliares, sin publicar 5432 ni dar acceso a bases ajenas. El proxy público existente sólo se usaría tras aprobar dominio/ruta/TLS y verificar cómo se conectará sin abrir la BD a otras aplicaciones. No modificar el proxy o redes globales para acomodar AgentApp.

Límites iniciales propuestos, sujetos a capacidad medida y prueba bajo carga:

| Componente | RAM máxima propuesta | CPU máxima propuesta |
|---|---:|---:|
| FE estático | 128 MiB | 0,25 |
| API Fastify | 512 MiB | 0,50 |
| PostgreSQL | 1024 MiB | 0,75 |
| Correo de pruebas privado | 128 MiB | 0,25 |
| Almacenamiento de fotos de pruebas | 256 MiB | 0,25 |

Total máximo propuesto: 2 GiB y 2 CPU. Son **topes**, no capacidad libre verificada ni reserva exclusiva. No ejecutar builds/E2E en el servidor compartido: publicar artefactos previamente comprobados. Imagen PostgreSQL local validada: 18.6, digest `sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722`; transporte al staging requiere comprobar compatibilidad y fijar configuración. Correo/objetos todavía requieren versiones y configuración verificadas en AGE-32/35.

Proponer 10 GiB para datos PostgreSQL y 5 GiB para fotos sintéticas, más almacenamiento de backups separado. El tamaño es una estimación inicial; no existe cuota implementada ni espacio aprobado. Verificar espacio libre, crecimiento, retención y carga de proyectos actuales antes de reservar. Medir carga prevista de AgentApp junto con carga habitual del servidor; si no cabe sin degradar proyectos existentes, presentar un host separado antes de aprovisionar. Precio adicional y capacidad no pueden afirmarse con este inventario.

Credenciales nuevas por entorno: rol API sin privilegios administrativos y migrador separado, JWT/session, correo y objetos privados; referencias autorizadas, sin copiar valores a tickets/repos. Ningún acceso a Docker socket o SSH desde código generado. Simulación de planes identificada, sin cobros ni tráfico Rebill. Protección contra datos reales y acceso público al correo de pruebas.

Backup PostgreSQL y objetos a destino separado autorizado; cifrado, acceso restringido y política de retención por aprobar. Antes del despliegue, restauración sintética en base descartable y comprobación de datos/versiones conforme AGE-36. Un rollback de imagen no restaura el esquema ni los datos.

## Bloqueos para AGE-35

Completar por un canal de lectura vigente el espacio/volúmenes/redes/backups y carga real. La sesión Browser alcanza para la información visible, pero no certifica capacidad del host. Si hace falta renovar SSH o conceder otro acceso, se requiere autorización nueva específica; no se reutiliza la autorización vencida.

Aprobar nombre/dominio/TLS, exposición al proxy compartido, límites y volúmenes, destino/costo de backups y referencias de secretos. Esta propuesta no autoriza DNS, puertos, servicios compartidos ni cambios productivos. AGE-33 permanece In Progress y AGE-35 no se aprovisiona.
