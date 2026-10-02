# Directorio público sintético — AGE-32/50

Primer corte local FE/API/PostgreSQL para verificar que el controlador consume
salidas revisadas y ejecuta comprobaciones del producto. No implementa la vertical
SaaS completa. La interfaz `frontend/src/App.tsx` proviene del redactor local
CrewAI/Ollama, con sustitución completa de sección y revisión posterior. Codex
preparó el backend, los validadores, el entorno, las pruebas y la configuración;
añadió tres nombres accesibles explícitos después del ensayo de navegador y pidió
una nueva revisión local del archivo completo.

## Comprobaciones

El backend aplica elegibilidad, filtros geográficos a ambos planes, orden y cursor
firmado ligado a los filtros; devuelve DTO públicos sin campos privados. Las
pruebas de integración usan PostgreSQL real con ocho perfiles sintéticos y un rol
de lectura. Cuatro escenarios Chromium comprueban el directorio conectado, ambos
planes, imágenes, filtros dependientes, error/reintento, descripción escapada,
respuesta vacía y ancho de 360/1280 píxeles. El backend se comprueba con
`tsc --noEmit`; Vite sí produce un paquete frontend.

La foto es un PNG publicado de prueba. No demuestra subida, decodificación o
validación de archivos. Registro es un enlace; autenticación, edición del dueño,
activación, galería, cobro real, despliegue y aceptación de los 36 criterios siguen
pendientes. Tampoco se prueba persistencia al reiniciar PostgreSQL en este corte.
Las referencias de criterios del plan son asociaciones documentales, no cobertura
de implementación.

## Ejecución mediante el controlador

Se necesita Docker Linux local y las imágenes fijadas por digest en
`controller/phase3_sandbox.py`. No ejecutar los comandos del modelo en el host.
El controlador construye imágenes temporales con COPY de fuentes capturadas,
sin RUN; no monta directorios del host ni publica puertos. Los tres contenedores
comparten solamente un namespace con loopback, sin salida a Internet. El ejecutor
instala desde caché verificada con `npm ci --offline --ignore-scripts`.

1. Ejecutar `controller/agent.py self-test` con el entorno Python bloqueado.
2. Obtener las cinco revisiones originales vigentes de plan/API/datos/suscripciones/
   manifiesto y la revisión de la sección tipada `application` del App.tsx exacto.
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
6. Ejecutar `phase3-run-sandbox --run-id ID --reviews IDS --code-review CODE_ID
   --workspace WORKSPACE --manifest MANIFEST --bound-digest SHA256 --cache CACHE
   --export RESULT`. IDS contiene las cinco identidades originales del mismo
   contrato/base/suite, no IDs de fixtures.

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
