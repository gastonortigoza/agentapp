# Coordinación entre tareas — AGE-32/50

La autonomía requerida abarca el avance entre tareas: los operadores intervienen
ante discrepancias persistentes, decisiones fuera del contrato o fallos externos.
El primer incremento de esta coordinación cubre el directorio y el perfil público
sintéticos. La vertical completa conserva sus flujos pendientes; no se declara
autonomía de todo el proyecto ni aceptación del SaaS.

`phase3-workflow` encadena los roles CrewAI/Ollama existentes: plan → API/datos/
suscripciones/manifiesto → tres módulos de código → materialización → sandbox.
SQLite conserva un expediente padre, hijos, presupuestos, transiciones y originales.
Los pasos ordinarios no requieren instrucciones nuevas de Codex. Un fallo de
build/unidad/E2E con salida completa y recursos limpiados devuelve evidencia
acotada a los redactores de los tres módulos y exige otra revisión y pruebas.
Los tests, scaffolds, lockfiles y entradas del contrato quedan congelados.

Si los tres redactores devuelven exactamente las fuentes del último sandbox
fallido, el controlador eleva discrepancia antes de reservar otra ejecución.
Un cambio no certifica una reparación: todavía requiere gates y pruebas. El
agotamiento agregado de ejecuciones se comprueba antes de otra ronda de redactores,
incluyendo consumo heredado. El prompt exige diagnosticar el fallo confirmado
aunque la revisión documental previa no haya generado findings.

Una respuesta de revisión completa con citas/formato inválidos admite una sola
revisión adicional por tarea. La nueva identidad referencia el original y consume
el presupuesto agregado. Truncación, identidad/consumo no verificables y transporte
incierto no habilitan esa reparación. No se reenvían operaciones inciertas.
Las discrepancias persistentes terminan en `awaiting_discrepancy`; problemas de
infraestructura/cleanup, en `needs_external_help`. Drift y límites tienen estados
explícitos. El expediente de atención conserva el hijo, resultados y feedback.

Límites agregados:60 llamadas,1966080 tokens de entrada,300000 de salida,7200s
activos y3 ejecuciones de producto. Cada hijo conserva sus límites más estrechos.
Se reservan máximos antes del despacho y se reconcilia sólo consumo registrado;
la incertidumbre conserva la reserva. Una continuación explícita `--parent-id`
después de una reparación verificada conserva inputs, operaciones y consumo del
padre; sólo permite un sucesor. Un padre incierto/activo no admite continuación.
Todos los hijos de la continuación vuelven a calificarse sobre la suite actual.

## Operación

Con Docker Linux, runtime/dependencias bloqueados y caché adquirida por el
controlador, ejecutar `agent.py self-test`. Para empezar:

```text
python agent.py phase3-workflow --run-id IDENTIDAD --workspace INPUT \
  --plan plan.json --cache CACHE --export evidencia.json
```

Para consultar/reanudar, conservar ID y omitir workspace/plan/cache. Un estado
terminal devuelve su resultado vigente sin nuevos pasos. El comando usa Studio
local si existe/configura su store; un fallo de proyección no decide el avance.
El padre no duplica los tokens registrados en los hijos.

### Etapa con auditorías cada tres rondas

Los nuevos arranques CLI usan `--review-every 3`. Una ronda es un intento completo
de corrección/revisión de una tarea, con resultado confirmado; no una llamada
individual ni un comando Docker. Las operaciones heredadas del padre no se cuentan
otra vez. Al cerrar3 intentos el worker termina en `awaiting_flow_audit`, sin
inferencias en vuelo. El paquete ligado al binding conserva identidades/hashes de
originales, alcance, presupuesto y estado; el checkpoint no pide nueva aprobación
de negocio ni instrucciones para la siguiente tarea.

Codex inspecciona el paquete y sus originales, registra una nota con
`phase3-flow-audit --run-id ID --packet-digest SHA --decision continue|discrepancy
--summary NOTA --export EVIDENCIA` y continúa la misma identidad. La auditoría
no reemplaza revisión/validadores/pruebas, ni habilita salidas rechazadas, ni
reinicia consumo. Repetir la misma decisión/nota es idempotente; cambiarla,
usar un paquete antiguo, modificar originales o saltar el contador se rechaza.
El modo posterior `--review-every 0` se fija al iniciar una identidad y permite
que los pasos soportados continúen hasta aceptación/discrepancia/límite, sin
auditorías periódicas. No se altera una política vigente dentro de un expediente.

En esta etapa el chat tiene seguimiento nativo cada10min. Su revisión se basa
en rondas cerradas, no en el reloj: despierta, inspecciona checkpoints pendientes,
registra la auditoría y continúa lo autorizado. El equipo y la aplicación local
deben estar encendidos. Las revisiones periódicas son de Codex; no se atribuyen
a CrewAI/Ollama. Fuera del modo provisional, se mantienen las discrepancias
persistentes y decisiones externas como motivos de intervención.

Una reparación del controlador requiere suite verde nueva y una continuación
explícita con ID nuevo, mismos inputs y `--parent-id ANTERIOR`. No constituye un
reset de presupuesto ni una aprobación de evidencias inválidas.

Si la discrepancia demuestra un error del operador en una prueba fija, el padre
terminado conserva su entrada y resultado. `--input-amendment enmienda.json`
permite una continuación con entrada nueva, mismo plan y consumo heredado. El
JSON declara exactamente `reason`, `paths` y `parent_fingerprint_sha256`; sólo
admite tests de aceptación existentes y fixtures, nunca las fuentes del producto
ni dependencias. La comparación de hashes exige declarar todos y sólo los archivos
cambiados. Los agentes siguen sin permiso para editar las pruebas.

## Estado de calificación

Las pruebas del supervisor inyectan fallos para comprobar avance automático,
feedback, límites, reanudación, deriva, consumo y atención. Son simulaciones de
coordinación; no sustituyen un ensayo real ni certifican el producto.
El primer ensayo real del padre `age50-autonomous-20261002` terminó en
`awaiting_discrepancy`:1 llamada,1617/793 tokens y71828ms. Conserva una cita
inventada y el falso rechazo de P03 con explicación contradictoria del propio
revisor. No ejecutó comandos del producto. Se reparó el protocolo para proporcionar
cobertura calculada, citas cortas disponibles y recuperación acotada; las
evaluaciones independientes históricas y sus fallos no se recalifican.

El revisor volvió a inventar una cita en la continuación r2. Se corrigió el
formato tipado de revisión del plan: cada regla admite pares pointer/quote exactos
tomados del candidato; el voto sigue siendo del revisor y el veto determinista
permanece. La cita vacía sólo permite rechazo. Las citas son fragmentos, no prueba
de cumplimiento por sí solas. Los registros anteriores no cambian de calificación.

Continuación final `age50-autonomous-r4-20261002`: `completed_slice`. Ocho tareas
encadenadas, dos módulos completados por los redactores, 7 pruebas unidad/PG y8
Chromium verdes, builds y cleanup completos. El recorrido final hizo12 llamadas;
el agregado con los tres padres conserva15 llamadas,52427/9763 tokens y513000ms.
La suite final del controlador obtuvo579 passed/1 skipped. La reanudación devolvió
el expediente idéntico sin nuevas operaciones. La devolución automática de un
fallo de producto se comprobó con fallos inyectados; este ensayo real pasó las
pruebas en su primera ejecución, por lo que no certifica esa rama con modelo real.

Registro de originales y resultados: `docs/phase3/age50/20261002-autonomy`.
La integración en el controlador activo y la calificación de toda la vertical
siguen pendientes de revisión. La preparación del controlador/pruebas es de Codex;
las propuestas/revisiones del producto se atribuyen sólo a sus originales locales.
