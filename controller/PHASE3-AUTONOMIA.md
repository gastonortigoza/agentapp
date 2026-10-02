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

Una reparación del controlador requiere suite verde nueva y una continuación
explícita con ID nuevo, mismos inputs y `--parent-id ANTERIOR`. No constituye un
reset de presupuesto ni una aprobación de evidencias inválidas.

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
