# AGE-50 — ejecución del 01/10/2026

Se amplió el controlador general en la propuesta PR10, sobre la base integrada AGE-30 y las entradas aceptadas de AGE-31. API, datos, suscripciones y manifiesto tienen salidas de sección tipadas, validadores deterministas, revisión por criterios con contexto relacionado y citas verificables. El redactor reemplaza el objeto completo; solicitudes, originales, correcciones, uso y eventos quedan en un diario durable. Continúa automáticamente hasta aceptación documental o bloqueo/límite y verifica la identidad al reanudar. Studio observa el mismo diario. Implementación y supervisión: Codex. Correcciones y revisiones reales: agentes CrewAI/Ollama locales, qwen3.8:27b-q4_K_M, digest congelado.

## Resultado sobre las fuentes finales

La suite del controlador pasó **480 pruebas, una omitida por symlinks Windows**, en93,15s. Añade33 pruebas de contrato, veto, citas, reemplazo, límites, interrupción/reanudación, contexto compacto y distinción de fixtures. El recibo se liga a las fuentes/runtime `be5c101333933f5ed32a0da7aa1d2b56aa3a4504f7c3bf695ff2b34c0fab62cd`. No se instalaron ni modificaron dependencias.

El ensayo `age50-contract-correction-r2-20261001` terminó **reviewed_contract_section**. El revisor detectó FK hacia una columna inexistente, unicidad global del hash idempotente e instrucción copiada en la cláusula de foto principal. El propio redactor reemplazó la sección; los validadores y otra revisión la aceptaron. El objeto final reproduce la sección de datos aceptada sin diferencias. Tres llamadas, una corrección,13.802 tokens de entrada,2.458 de salida y134,703s activos. Original, solicitudes, citas y respuesta íntegra están en `AGE-50-correccion-contractual-r2.json`. Esto es aceptación de una sección documental; no valida código ni producto.

## Evaluación congelada

Los casos y vectores esperados se fijaron antes de enviar inferencias r2. Una llamada por caso, sin corregir fixtures ni ajustar/repetir los fallidos. El vector esperado no llega al modelo. Hay **cuatro casos nuevos independientes y dos regresiones explícitas**, no seis casos nuevos independientes.

| Caso | Tipo | Resultado |
|---|---|---|
| Alias RFC3339 UTC de fecha del DTO Photo | Nuevo positivo | Correcto |
| Orden inverso de declaraciones FK, conservando los mappings | Nuevo positivo | Correcto |
| `$MissingPhoto[]\|null` en PublicProfile.photos | Nuevo negativo | El modelo aprobó incorrectamente; validador detectó `undefined_dto` y vetó la aprobación |
| FK uuid user_id hacia users.email con UNIQUE pero tipo distinto | Nuevo negativo | Correcto |
| TTL finito aceptado y decisiones/facturación diferida explícitas | Regresión positiva | Correcto |
| Una suite E2E integrada FE/API/DB, sin exigir otra para backend | Regresión positiva | Correcto |

**Revisor:3/4 nuevos y2/2 regresiones; conjunto5/6.** Todas las citas y respuestas del conjunto final fueron válidas; el fallo es sustantivo. El estado del caso fallido es `evaluated_fixture` y `fixture_passed=false`, con hallazgo determinista preservado. En un ciclo de corrección ordinario ese hallazgo pasa al redactor aunque el modelo apruebe; no concede permiso de ejecución. Los filtros para ambos planes y la expiración con gestión del dueño siguen comprobándose contra las cláusulas aceptadas y están cubiertos por mutaciones automatizadas. La muestra no demuestra una garantía general ni califica los36 casos sobre un producto.

La evaluación r2 usó seis llamadas,37.032 tokens de entrada,1.449 de salida y88,202s. Evidencia en `AGE-50-evaluacion-secciones-r2.json` y exportaciones por caso.

## Evidencia anterior conservada

El primer ciclo ya había terminado con aceptación documental en tres llamadas. Su primera evaluación obtuvo cuatro resultados correctos y dos bloqueos de API **antes del envío** por tamaño conservador del contexto. No hubo inferencias ni consumo confirmado en esos dos casos. Se conservaron sus originales/recibo/resultados; no se cuentan como respuestas del modelo. La reparación envía el contexto común una sola vez más un delta RFC6902 sin pérdida, validado con una prueba de reconstrucción y margen para CrewAI. Las fuentes reparadas obtuvieron su propio recibo y un nuevo ciclo aceptado; el recibo anterior no las certifica.

Total de esta ejecución: **16 inferencias completas**,95.878 tokens de entrada y7.796 de salida,450,809s activos; más dos operaciones detenidas antes de enviar. No quedan operaciones de esta ejecución en vuelo. Las reservas conservadoras no se reportan como tokens consumidos.

## Studio y alcance pendiente

Studio se verificó visualmente: el ciclo r2 muestra aceptación documental,3 llamadas,1 corrección y16.260 tokens. Los fixtures se etiquetan como evaluación; el caso de DTO desconocido muestra fallo de evaluación. Hay capturas del ciclo y del caso fallido junto a la evidencia.

El cambio está preparado para revisión en PR10, sin modificar el controlador activo. **AGE-50 sigue In Progress**: la evaluación independiente no fue totalmente correcta y falta la integración definitiva y el acoplamiento al ejecutor aislado de AGE-32, incluida revisión de código generado y checks/reanudación de producto. Los scripts, lockfiles, servicios y pruebas reales FE/API/PostgreSQL/E2E/build corresponden a la continuación de la vertical, no quedan sustituidos por estos ensayos documentales. El CI del PR sigue siendo el piloto raíz; la suite controller se acredita con el recibo local.

El siguiente trabajo concreto es conservar el veto de DTO indefinido al consumir propuestas y verificarlo en el ejecutor, completar su aislamiento y probar la vertical. No se reabre AGE-31 ni se hacen rondas ilimitadas para optimizar el prompt con este caso. No se habilitaron comandos del SaaS, merge, despliegue, cobros ni permisos.
