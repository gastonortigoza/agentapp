# Operación de fase 2 — AGE-21

El controlador conserva el aislamiento, presupuesto y lenguaje reducido del piloto. La entrada remota es un requisito estructurado, no un ejecutor de instrucciones arbitrarias de una página.

## Requisitos remotos

En la página Notion autorizada, colocar un bloque JSON entre `agentapp-requirement-<id>` y `agentapp-end-<id>`. Campos admitidos: `input_key` y `attention_states`; no se admiten comandos, rutas ni permisos. Ejecutar:

```powershell
agent.cmd self-test
agent.cmd run-local --connected --requirement-id age21-followup-v1
```

Se captura la revisión del documento, el ID de sección, los parámetros y su hash. El contrato guarda un snapshot inmutable: una edición remota posterior requiere otro run. Los tests se derivan de un generador confiable antes de invocar al modelo. El alcance actual es una función pura `summarize_runs`, exportada como `remote_summary`.

## Estados y conflictos

La política vigente es `report_only`: los estados de ejecución permanecen en el controlador y se anexan a Notion. Los estados de workflow de Linear pertenecen a la persona; el controlador los observa y no los cambia. Esto elimina la escritura que podía pisar una edición concurrente sin compare-and-swap. Las descripciones y adjuntos existentes se conservan. Los recibos históricos siguen siendo válidos para sus operaciones originales.

## Publicación y cierre

Preparar la entrega con `delivery-prepare`, revisar el diff y publicar el SHA revisado con el publicador existente. Preparar/abrir el PR en borrador con su digest exacto. Luego:

```powershell
agent.cmd ci-check <job-id>
agent.cmd closeout <job-id>
```

`closeout` verifica el run entregado, el PR actual y el CI del commit exacto; añade enlaces de regreso a los tickets y una nota final en Notion. Registra intención antes de escribir, verifica por lectura y reconcilia resultados inciertos sin reenviarlos. Repetir el mismo job no debe crear enlaces ni notas adicionales. Una edición externa incompatible bloquea y requiere revisión.

## Nota incierta histórica

`notion-note resolve-formatting <operation>` solo reconcilia una nota incierta del destino autorizado y registra identidad local, hash de observación, hash de intención y regla aplicada. Admite exclusivamente llaves escapadas y el autolink exacto de `agent.py`. Un destino de enlace distinto o contenido diferente bloquea. No envía ninguna escritura remota.

## Alcance

No hay merge ni despliegue. Coolify conserva el inventario previo y su autorización SSH vencida. Las pruebas locales de expiración y las renovaciones reales históricas tienen el alcance documentado en la revisión; no se afirma revocación real de credenciales.
