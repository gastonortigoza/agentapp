# Preparación estructurada de AGE-32

La base integrada de AGE-30 es `7e20c4426663d265b2d2a70e7af0aa4bed9e5e4b` (PR8, equivalente de PR5, y PR6). Esta revisión añade `agent controller phase3-prepare` al controlador existente. Consume únicamente el contrato técnico v1 aceptado de AGE-31 y prepara un plan de archivos y tareas. El piloto de fase 2 conserva sus límites.

El comando exige un recibo verde de la suite del controlador sobre sus fuentes actuales. Verifica los ocho archivos de entrada por SHA256, el esquema de API/datos/manifiesto y las referencias a contrato, política y decisiones. Los siete archivos originales de PR7 se copiaron byte a byte desde el commit `970aa9085cc4df0b1ed314babf666cee6e050cd7`; `business-source.json` conserva los bytes originales del registro del usuario cuyo hash referencia el manifiesto. `business-decisions.json` es su serialización de PR7, semánticamente equivalente. No se modifica ninguno.

El plan enumera archivos de frontend, backend y scripts/lockfiles de los tres directorios, siete etapas propuestas y los 36 criterios aceptados. Puede validarse una salida JSON del planificador con `--plan`. El esquema del plan rechaza código, comandos, herramientas, rutas protegidas, escapes, colisiones de Windows, criterios inventados y comprobaciones omitidas. Las referencias a criterios comprueban identidad y cobertura; no prueban que la tarea descrita implemente su contenido. Los textos `purpose` permanecen datos sin autoridad ni ejecución.

La preparación y los archivos originales se conservan en `.state/phase3/snapshots/<identidad>`. Repetir la misma preparación no reemplaza evidencia alterada. El recibo de suite incluye el lock de entrada y los archivos de contrato, por lo que sus cambios invalidan el verde. Los hashes de entrada rechazan drift; esta revisión todavía no implementa invalidación de checks de producto al reanudar.

`SOURCE-MANIFEST.json` se conserva como evidencia del snapshot original de fase 2, no como inventario de esta revisión nueva. La identidad actual de fuentes/runtime se registra mediante `controller_gate` y el recibo de suite. Validación de esta revisión:43 pruebas nuevas y suite completa416 aprobadas/1 omitida/547 advertencias en68.84s. La omisión continúa siendo el permiso de symlinks en Windows; se reutilizó el entorno aislado instalado de forma bloqueada para AGE-30, con el mismo uv.lock. La invocación real del CLI produjo el snapshot con ejecución deshabilitada.

## Uso local

Desde el directorio del controlador, con el entorno bloqueado:

```text
python -c "import controller_gate; controller_gate.self_test()"
python -c "import controller_cli; raise SystemExit(controller_cli.main(['phase3-prepare']))"
```

El resultado indica `execution_authorized=false`, `product_tests_executed=false` y `limits_claimed_enforced=false`. Ningún argv propuesto se despacha. El contrato congelado conserva `manifest.enabled=false`, `deployment=disabled` y la política `generated_code_execution=false`. Se registra que AGE-30 está resuelto; sus antiguos bloqueos se conservan en la entrada histórica.

## Pendientes del ticket

AGE-32 continúa In Progress. Faltan scripts y lockfiles reales, adquisición acotada de dependencias, servicios/configuración/recursos verificados, aislamiento de código generado, ejecución FE/BE/PostgreSQL/E2E/build, reanudación por drift y pruebas de presupuesto/check fallido de ese ejecutor. AGE-50 conserva pendientes de salidas/correcciones de agentes, evaluación independiente, continuidad durable y Studio. Esta preparación fue implementada y probada por Codex; no acredita trabajo del agente local ni autocorrección autónoma.

El CI del repositorio ejecuta las 22 pruebas raíz del piloto. La suite del controlador se verifica localmente por separado; no se atribuye al check `pilot-tests`. No se habilitan despliegues, cobros o permisos nuevos.
