# AGE-50 — continuación del 01/10/2026

Se conectó el consumo de revisiones al comando público del controlador y a la entrada del ejecutor en la propuesta [PR10](https://github.com/gastonortigoza/agentapp/pull/10). El formato del redactor API restringe método, path, acceso, estados de éxito y errores a los valores aceptados. Se conservaron las entradas AGE-31 y el controlador activo. **AGE-50 sigue In Progress**: esta continuación acredita corrección documental y bloqueo previo a ejecución; la integración definitiva y la vertical FE/API/PostgreSQL/E2E/build siguen pendientes.

## Fuentes finales y validación

Suite local: **515 passed, 1 skipped** por symlinks Windows, 547 warnings, 67,17s;35 pruebas netas más que el head14bf anterior. Recibo de fuentes/runtime `2a391fb67aa2355627edb04398875f54582ba72ff4deb1b34cc927ce976840da`. El CI raíz prueba el piloto; no sustituye esta suite ni pruebas del SaaS. Sin cambios de dependencias.

`agent phase3-check-execution` consume cinco IDs originales del diario: plan/API/datos/suscripciones/manifiesto. Exige revisión documental terminal, identidad vigente de suite/input/política/modelo, cadena completa desde la semilla a las correcciones, originales de solicitudes/respuestas, digest/consumo verificable y citas resueltas. Vuelve a aplicar validadores y comprueba las secciones ensambladas. Fixtures, aprobaciones falsas, operaciones inciertas, citas alteradas y recibos de fuentes anteriores se rechazan. Las comprobaciones nuevas cubren campos DTO/payload obligatorios, todas las tablas/columnas requeridas, referencias nullable/array y errores de ruta; mantienen aliases válidos y requests vacíos aceptados.

La salida del redactor usa `items.oneOf` para los hechos auditados de las rutas. No se cambiaron prompts para mejorar puntuaciones. El motor documenta una limitación de `prefixItems` ([referencia](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md#json-schemas--gbnf)); la respuesta real r6 acredita la alternativa en este entorno. La gramática no sustituye la revisión semántica ni los checks posteriores.

## Ensayo real final

CrewAI/Ollama local, qwen3.8:27b-q4_K_M y digest congelado. API r6 parte del resultado defectuoso del ciclo r4: POST /api/auth/register conservaba errors=[401] en lugar de [400,422,503]. El revisor volvió a aprobarlo; el validador vetó esa aprobación. El redactor reemplazó toda la sección usando el formato restringido y otra revisión la aceptó. **3 llamadas, 1 corrección,16.250 tokens de entrada,4.256 de salida y127,688s.** El candidato final coincide íntegramente con la API aceptada; las correcciones de DTO/campo obligatorio se conservaron. Requests, respuesta completa y citas están en `AGE-50-consumo-r6-api.json`.

Plan, datos, suscripciones y manifiesto tuvieron una revisión cada uno sobre las mismas fuentes. El conjunto final usó **7 llamadas confirmadas,35.925 tokens de entrada,5.617 de salida y171,218s activos**. El contrato ensamblado reproduce el contrato aceptado.

La entrada real CLI→ejecutor consumió esas cinco revisiones y devolvió `blocked_execution_policy`, exit2. La política/manifiesto congelados deshabilitan ejecución de código generado; el workspace vacío también informa los11 archivos faltantes. No se ejecutaron comandos de producto, Docker, instalación, migraciones, builds o E2E. El preflight guarda hashes de fuentes, propuestas, revisiones y todos los archivos/lockfiles del workspace, con límites de200 archivos/directorios y8 MiB. Repetirlo sin cambios devolvió exactamente el mismo resultado. Añadir package-lock.json invalidó el mismo ID; se preservó el registro original. Esto es invalidación de evidencia previa, no ejecución/repetición de checks de producto.

Studio se verificó visualmente: API r6 muestra aceptación documental,3 llamadas/1 corrección y20.506 tokens; el preflight muestra bloqueo explícito y cero inferencias propias, sin contar dos veces el consumo de las revisiones.

## Historial conservado y límites de la conclusión

| Ensayo | Resultado preservado |
|---|---|
| r3 | El redactor reparó DTO/campo obligatorio pero cambió errores de registro. Auditoría posterior fallida, con originales y registro separado en Studio. No califica las fuentes finales. |
| r4 | El validador nuevo vetó el error repetido hasta5 llamadas/2 correcciones y terminó blocked_budget. No llegó al ejecutor. |
| r5 | El primer formato estricto produjo HTTPError. Quedó uncertain_operation y no se reenvió.5 inferencias completas; la operación fallida no tiene consumo verificable. |
| r6 | Formato compatible, corrección aceptada, contrato ensamblado verificado y ejecución bloqueada por la política documental. |

Estos son ensayos de corrección/regresión conocidos y pruebas del consumo de evidencia, no nuevos casos independientes. La evaluación histórica r2 conserva3/4 nuevos y2/2 regresiones sobre sus fuentes anteriores. No se repitió ni recalificó su holdout fallido y no se afirma garantía general del revisor/redactor.

Consumo confirmado de esta continuación: **28 inferencias completas,143893 tokens de entrada y23239 de salida**; más una operación HTTPError de consumo desconocido. Las reservas32.768/5.000 permanecen separadas en `AGE-50-consumo-verificado-r5.json` y `AGE-50-consumo-confirmado-continuacion.json`; las cuentas crudas del controlador r5 incluyen esas reservas y no son consumo medido. Acumulado con la ejecución anterior:44 inferencias completas,239.771 tokens de entrada y31.035 de salida; más esta operación desconocida y los2 preflight anteriores no enviados. No quedan operaciones en vuelo ni se habilitó reenvío automático.

## Trabajo pendiente

El PR sigue en borrador sobre la base AGE-30 integrada. Falta integrar la propuesta y desarrollar el ejecutor SaaS aislado de AGE-32 con un nuevo manifiesto técnico concreto: scripts reales, locks/pins, imágenes/servicios, límites/red/secretos y pruebas de producto. El manifiesto AGE-31 aceptado es explícitamente documental; su aprobación no autoriza convertirlo en ejecutable. La barrera y el fingerprint de esta continuación no acreditan revisión de código generado ni los36 casos del producto. La regresión de reloj tras adquirir el lock del usuario sigue separada para AGE-37/vertical, sin nueva prueba PostgreSQL en este ensayo.

El controlador activo permanece sin editar. La evidencia se publica para revisión; no hubo merge, despliegue, cobros ni cambios de permisos. No se sustituyen los pendientes de la primera vertical por una puntuación documental.
