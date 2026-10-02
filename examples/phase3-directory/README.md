# Directorio y perfil público sintéticos — AGE-32/50

Primer corte local FE/API/PostgreSQL para verificar que el controlador consume
salidas revisadas y ejecuta comprobaciones del producto. No implementa la vertical
SaaS completa. CrewAI/Ollama produjo la interfaz original, añadió el endpoint
de detalle al backend y reemplazó el stub de PublicProfile.tsx. El supervisor
encadenó las ocho revisiones, correcciones de fuentes y pruebas sin nuevas
instrucciones entre tareas. Codex preparó el controlador, scaffold, navegación,
entorno y pruebas; los tres módulos completos aceptados se copiaron con
verificación byte a byte del ensayo real.

## Comprobaciones

El backend aplica elegibilidad, filtros geográficos a ambos planes, orden y cursor
firmado ligado a los filtros; devuelve DTO públicos sin campos privados. Las
pruebas de integración usan PostgreSQL real con ocho perfiles sintéticos y un rol
de lectura. Siete pruebas unitarias/integración y ocho escenarios Chromium comprueban
el directorio conectado, ambos
planes, imágenes, filtros dependientes, error/reintento, descripción escapada,
respuesta vacía, detalle público, galería ordenada, elegibilidad, WhatsApp,
404/reintento y ancho de 360/1280 píxeles. El backend se comprueba con
`tsc --noEmit`; Vite sí produce un paquete frontend.

La foto es un PNG publicado de prueba. No demuestra subida, decodificación o
validación de archivos. Registro es un enlace; autenticación, edición del dueño,
activación, cobro real, despliegue y aceptación de los 36 criterios siguen
pendientes. Tampoco se prueba persistencia al reiniciar PostgreSQL en este corte.
Las referencias de criterios del plan son asociaciones documentales, no cobertura
de implementación.

## Ejecución mediante el controlador

El comando `phase3-workflow` inicia el recorrido completo con un plan, workspace
y caché preparados; consultar `controller/PHASE3-AUTONOMIA.md`. Conserva entradas
y tests, encadena roles locales, devuelve fallos del producto a los redactores y
termina ante aceptación del corte, discrepancia persistente o límite. Los pasos
de abajo describen también la operación individual de sus componentes.

Se necesita Docker Linux local y las imágenes fijadas por digest en
`controller/phase3_sandbox.py`. No ejecutar los comandos del modelo en el host.
El controlador construye imágenes temporales con COPY de fuentes capturadas,
sin RUN; no monta directorios del host ni publica puertos. Los tres contenedores
comparten solamente un namespace con loopback, sin salida a Internet. El ejecutor
instala desde caché verificada con `npm ci --offline --ignore-scripts`.

1. Ejecutar `controller/agent.py self-test` con el entorno Python bloqueado.
2. Obtener las cinco revisiones originales vigentes de plan/API/datos/suscripciones/
   manifiesto y tres revisiones de fuentes: `application` (App.tsx), `public_api`
   (backend/src/app.ts) y `public_profile` (frontend/src/PublicProfile.tsx).
   Los JSON exportados son evidencia; no sustituyen el diario SQLite original.
3. Copiar únicamente los archivos del plan revisado a un workspace nuevo bajo el
   directorio permitido. Conservar las tres copias idénticas del lock raíz.
4. Preparar la caché con `phase3_dependencies.acquisition(files, cache_directory)`
   y verificarla con `cache_archive(files, cache_directory)`, donde files contiene
   los bytes capturados de los archivos del plan:
   adquisición HTTPS de tarballs npm con SHA512, sin ejecutar scripts. Esta es una
   acción previa del controlador, fuera de la red aislada del producto.
5. Crear `phase3-build-manifest --plan PLAN --workspace WORKSPACE --export MANIFEST`.
   Calcular el digest con `manifest.identity` sobre el JSON parseado, no sobre su
   formato textual. Revisar ese manifiesto concreto.
6. Ejecutar `phase3-run-sandbox --run-id ID --reviews IDS --code-reviews CODE_IDS
   --workspace WORKSPACE --manifest MANIFEST --bound-digest SHA256 --cache CACHE
   --export RESULT`. IDS contiene las cinco identidades originales del mismo
   contrato/base/suite, no IDs de fixtures. CODE_IDS es un JSON con las tres claves
   `application`, `public_api`, `public_profile` y sus identidades originales.
   Las rutas y los bytes completos deben coincidir con cada candidato revisado.
   El argumento anterior con una sola revisión ya no habilita ejecuciones nuevas.

Las entradas y las imágenes se verifican antes de los comandos. Un ID terminado
con las mismas entradas devuelve la evidencia original sin repetir operaciones;
un cambio la rechaza. Una interrupción incierta no se reenvía. La recuperación
`phase3-cleanup-sandbox --run-id ID` elimina solamente recursos con la identidad y
etiquetas de esa ejecución. Conservar `.state/phase3/review.sqlite` como diario
local: no se publica ni contiene una autorización para operaciones remotas.

El manifiesto congelado de AGE-31 conserva `enabled: false`. La política concreta
del directorio es separada, limitada a datos sintéticos locales, 900 segundos,
100 operaciones, 2 CPU y menos de 4 GiB. No habilita el ejecutor SaaS genérico.

La evaluación independiente de r9 obtuvo 2/4 para el modelo asesor. Los dos falsos
aprobados tienen hallazgos deterministas; no prueban calificación general del
modelo. Consultar la evidencia inmutable en `docs/phase3/age50/20261002-directory`.

Ensayo autónomo final `age50-autonomous-r4-20261002`: completed_slice, 7+8 pruebas
verdes, ocho etapas y cleanup completo. Suite del controlador579 passed/1 skipped.
Preserva los bloqueos de formato anteriores y su consumo; no es una evaluación
independiente nueva. Evidencia: `docs/phase3/age50/20261002-autonomy/RESULTADO.md`.
