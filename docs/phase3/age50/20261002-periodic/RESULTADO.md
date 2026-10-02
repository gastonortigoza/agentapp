# AGE-32/50 — autonomía con auditoría cada tres rondas

Los agentes locales encadenan las tareas ordinarias. Durante esta etapa Codex
audita cada tres intentos completos de revisión/corrección, consulta originales,
citas, pruebas y consumo, y continúa lo autorizado. Las discrepancias persistentes
se conservan para intervención. El modo posterior `--review-every 0` permite
continuar hasta aceptación/discrepancia/límite sin checkpoints periódicos.
Seguimiento nativo activo del mismo chat cada10 minutos, sujeto a equipo/app encendidos.

## Verificación

Suite del ensayo real:589 passed,1 skipped,547 warnings. Suite tras la
discrepancia y nueva prevención:592 passed,1 skipped. Los recibos se distinguen
en el binding del ensayo y controller-suite-receipt.json; no se recalifica el ensayo.
Las pruebas del controlador
incluyen checkpoint3/6, reanudación sin llamadas duplicadas, replay idempotente,
rechazo de auditorías alteradas y enmienda explícita de tests sin reiniciar consumo.
Son pruebas de coordinación con fallos inyectados; no aceptación del SaaS.

Ensayo original `age50-periodic-20261002`: auditorías reales1-3,4-6 y7-9.
Primer sandbox:7 unidad/PG y8 de9 Chromium pasaron; falló el test nuevo antes de
liberar la respuesta obsoleta. Codex había esperado Promovido B para ID0002,
pero el seed define Básico A. Este error de la prueba no demuestra defecto del
agente. El supervisor devolvió automáticamente ese log al redactor y el checkpoint
permitió detectar la prueba errónea antes de adoptarla. Se detuvo con discrepancia,
con10 llamadas/1 ejecución consumidas. Ningún original se recalificó.

Codex corrigió sólo dos etiquetas esperadas del test. La continuación requiere
el hash exacto de la entrada del padre, mismos26 paths/plan y todos los cambios
declarados. Conserva operaciones y consumo, admite un sucesor por padre y prohíbe
cambiar fuentes/dependencias mediante esa enmienda. Los agentes no editan tests.

Continuación `age50-periodic-r2-20261002`: **awaiting_discrepancy**.
Motivo: Operator flow audit: Discrepancia Codex rondas10-12: las propuestas r1 de App/API/PublicProfile resultaron idénticas por candidate_sha256 a las fuentes del sandbox fallido. El revisor de PublicProfile aprobó V02 citando el retry sin AbortSignal, aunque el E2E ya había demostrado que una respuesta obsoleta reemplaza el nuevo perfil. Las citas existen pero no demuestran cumplimiento. La ejecución posterior repitió el mismo fallo en línea21; no se adopta producto ni se recalifican revisiones. Leí los originales API/perfil y App r2 y revalidé su procedencia, no su conducta incorrectamente aprobada. Las3 ejecuciones agregadas ya están consumidas, incluida la prueba inicialmente errónea del operador; detengo antes de otra inferencia/ejecución. Se conservan26 llamadas,129180/15326 tokens y920515ms. Repararé el controlador/prompt para exigir atención explícita al fallo, detectar ausencia total de cambios antes de repetir sandbox y elevar presupuesto agotado antes de otro ciclo. Esa reparación exige suite nueva y no aumenta ni reinicia el presupuesto de este expediente.
.
Consumo agregado: {"active_ms": 920515, "calls": 26, "executions": 3, "input_tokens": 129180, "output_tokens": 15326}.
Auditorías nuevas registradas: 4.
Pruebas ejecutadas: True.
Aceptación de todo el SaaS: False.
El sandbox corregido reprodujo el defecto real después de liberar el retry,
en línea21 del test: una respuesta del perfil anterior reemplazó el nuevo. El
supervisor devolvió automáticamente el fallo confirmado a los tres redactores.
La reparación/aceptación posterior sigue pendiente; el fallo real no se declara resuelto.
En r1 los tres redactores devolvieron los mismos candidatos por hash. El revisor
del perfil aprobó V02 citando el retry sin signal pese al fallo confirmado. La
tercera ejecución agregada volvió a fallar el mismo test; la auditoría10-12
detuvo el expediente con discrepancia y presupuesto conservado, antes de más
inferencias. El operador no arregló la fuente del producto para ocultar ese fallo.

Se mejoró el protocolo posterior: el prompt exige diagnosticar el fallo aun con
findings documentales vacíos; el controlador compara las tres fuentes con los
originales del sandbox fallido y eleva discrepancia si todas son idénticas antes
de reservar otra ejecución. El consumo agregado agotado impide abrir otra ronda.
Estos cambios tienen pruebas nuevas con fallos inyectados; no se ejecutó otra
prueba de producto ni se reinició este presupuesto. La carrera y la calificación
real de la reparación del modelo quedan pendientes para una nueva asignación.
Se conserva el producto previamente aceptado. El resultado nuevo no se convierte en aprobación.
Los exports conservan solicitudes/respuestas/rechazos/logs y cleanup completos.

## Alcance pendiente

La autonomía implementada cubre directorio/perfil público sintético y una cola
fija. La integración con el controlador activo, registro/auth, edición del dueño,
uploads, activación/idempotencia, catálogo oficial y36 criterios siguen pendientes.
AGE32/50 permanecen In Progress; PR10 borrador. La evaluación independiente
histórica2/4 sigue intacta: estos ensayos de desarrollo no son otra evaluación.
Controlador/tests/auditorías: Codex. Propuestas/revisiones: sus originales locales.
CI raíz prueba el piloto y no sustituye suite controller ni sandbox del producto.

`input-original` conserva la prueba errónea; `input-corrected` conserva la entrada
corregida. SHA256SUMS.json cataloga todos los bytes salvo el catálogo mismo.
El diario SQLite local sigue siendo autoridad de reanudación y no se publica.
