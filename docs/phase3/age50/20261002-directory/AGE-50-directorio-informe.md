# AGE-50 — ejecución del primer directorio conectado

02/10/2026. Propuesta en [PR10](https://github.com/gastonortigoza/agentapp/pull/10),
sobre la base integrada AGE-30 (`7e20c4426663d265b2d2a70e7af0aa4bed9e5e4b`)
y el contrato técnico congelado de AGE-31. El controlador activo en
`E:/IA/projects/local-ai-lab` y sus cambios locales se preservaron.

## Resultado concreto

El ejecutor general incorpora una entrada para un directorio público sintético
local. La ejecución `age50-directory-execution-r9d-20261001` terminó **completed**:
instalación offline, migración PostgreSQL, comprobación de tipos de API, paquete
frontend, **5 pruebas de unidad/integración y 4 pruebas reales en Chromium**.
El recorrido usa Vite → Fastify → PostgreSQL, no respuestas simuladas en el caso
principal. Comprueba ambos planes, elegibilidad, filtros y cursor firmado;
descripción escapada, error/reintento, respuesta vacía, imágenes y anchos 360/1280.
Los escenarios de error/vacío interceptan respuestas explícitamente para verificar
los estados de UI; el catálogo y la lectura normal usan el backend real.

La suite del controlador obtuvo **546 passed, 1 skipped** en 65,74 segundos.
Identidad local ligada a fuentes, contrato y runtime:
`79038c9511aabc56b04bc81a0080fd87a99c113a5abd153bc4931d2d086336fe`. No se reutiliza ese recibo para otro checkout/runtime.
La CI del repositorio prueba el piloto de raíz; no sustituye esta suite o Chromium.

Manifiesto concreto: `4cd10ce6a3619d244cdaf930f255b4c2d53d7614b6da779e9b04e2a261b11868`.
La ejecución consumió cinco revisiones documentales actuales y la revisión del
App.tsx exacto; capturó hashes de todos los archivos y locks. Reanudar con la misma
identidad devolvió el original sin nuevos comandos. Una alteración del lock fue
rechazada (`Component lock drift`) y el checkpoint original permaneció intacto.
Los tres contenedores y las dos imágenes temporales se retiraron con código cero.
Los servicios existentes AGE-34 y Qdrant continuaron en funcionamiento.

## Salida estructurada y autoría

Plan/API/datos/suscripciones/manifiesto son objetos tipados, con referencias y
defectos comprobables, contexto por criterio y citas ligadas al valor completo.
El redactor sustituye la sección completa. Se preservan semilla, solicitud,
respuesta, corrección, revisión y estados del diario. Límites: hasta cinco
llamadas/dos correcciones y 600 segundos en las secciones; una operación incierta
no se reenvía. Studio distingue fixtures, revisión documental, revisión de código
y resultado del producto, sin contabilizar tokens dos veces.

CrewAI/Ollama local, qwen3.8:27b-q4_K_M, produjo y corrigió App.tsx en r8; r9
revisó esa propuesta sobre el controlador vigente. Codex preparó el backend,
scaffold, entorno y pruebas; ajustó tres nombres accesibles y solicitó revisión
local nueva de ese archivo completo en r9d. No se atribuye esa última modificación
al redactor. La sustitución automática del redactor permanece demostrada por los
originales r7/r8 y por las regresiones del controlador.

Esta continuación contabiliza **27 llamadas completas**,
123915 tokens de entrada y 11855 de salida,
666.124 segundos de modelo. Los ensayos de ejecución r8b/r9b/
r9c/r9d reutilizaron revisiones vigentes: cero inferencias en el ejecutor.
No incluye el consumo histórico anterior r1–r6.

## Evaluación independiente y fallos conservados

Se congelaron cuatro candidatos antes de consultar al modelo, una inferencia por
caso, sin correcciones ni reajuste posterior: **2/4 para el revisor asesor**.
Aprobó incorrectamente Photo sin created_at/ruta privada pública y una FK hacia
una columna existente no única. Los validadores deterministas sí registraron los
defectos; todos los IDs de fixtures son rechazados por el gate del ejecutor.
Los alias descriptivos válidos y enteros JSON escritos como floats matemáticamente
enteros fueron aceptados. Esto no califica al modelo en general. Se conservan las
respuestas incorrectas originales, sin reemplazarlas por ejecuciones posteriores.

Los resultados fallidos también permanecen: r7 instalación sin permiso de ejecutar
tsx en tmpfs; r8 error de tipos en la prueba API; r8b ruta de Node incorrecta en
la imagen de navegador; r9/r9b carga de UI; r9c nombres de controles. Se corrigieron
la política necesaria y los archivos afectados con nuevas identidades, hasta r9d.
No se modificaron las pruebas para eliminar los escenarios que fallaban.

## Límites de la propuesta

Es el **primer corte de directorio**, no aceptación de la vertical SaaS completa.
Pendientes: autenticación y registro funcional, edición del dueño, subida/validación
de fotos, activación e idempotencia del producto, catálogo oficial, prueba de
persistencia/reinicio, cobertura completa de los 36 criterios y aceptación final.
Las asociaciones de criterios en el plan son documentales, no implementación.
La foto es un PNG sintético publicado; la comprobación telefónica aquí utiliza
fixtures y no certifica validación móvil completa. La API se comprueba con
`tsc --noEmit`, sin afirmar un artefacto backend distribuible.

La política concreta es local y fija: imágenes por digest, no mounts/puertos del
host, credenciales del controlador ni salida de red; permisos mínimos de lectura
para la API, fuentes de solo lectura, caché verificada y scripts de instalación
deshabilitados. Hasta 900 segundos/100 operaciones, 2 CPU y menos de 4 GiB.
La caché tiene 94 tarballs únicos/22.232.331 bytes; digest actual del archivo:
`e7dc26045c6604ab5b70353965e51a13750f271f0e3ca77d66f205d02b80ad69`. El recibo de adquisición r7 conserva el
digest histórico del lanzador original; la ejecución vincula el archivo actual.
El namespace se destruye al terminar: no es entorno persistente de desarrollo.

La política documental AGE-31 sigue deshabilitada. La nueva entrada no habilita
comandos SaaS arbitrarios, merge, despliegue ni cobros. La implementación permanece
en PR borrador para revisión/integración; AGE-50 y AGE-32 continúan In Progress.

## Evidencia

`AGE-50-ejecucion-directorio-r9d.json` contiene comandos, resultados y cleanup;
`AGE-50-directorio-resultado-r9d.json`, reanudación/deriva; `AGE-50-holdout-resultado-r9.json`
y los cuatro originales, evaluación independiente; `AGE-50-directorio-auditoria-final.json`,
veto determinista y gate; `AGE-50-directorio-consumo-confirmado.json`, consumo;
`AGE-50-suite-controlador-r9.txt`, suite; `AGE-50-directorio-vista-final.jpg`, UI real.
El catálogo SHA256 en la carpeta publicada protege los bytes de estas evidencias.
