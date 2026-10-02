# Evidencia prospectiva del transporte local

Los nuevos expedientes de fuente auth fijan `transport_protocol` al protocolo
durable. Antes de ejecutar el worker se registra el request en SQLite y se crea
un expediente local exclusivo por runID/sequence. Cambiar payload/binding no concede
otra operación en ese namespace. Un reinicio sólo inspecciona archivos, sin POST,
sin tags, sin otro proceso y sin aprobación implícita.

El worker stdlib usa sólo `127.0.0.1:11434`, sin proxies, redirects, herramientas
o delegación. Valida el modelo fijado antes y después, pide stream con think=false,
guarda cada línea original con flush/fsync, y separa el final de los fragmentos.
Conserva los límites originales: contexto32768, salida5000 como máximo y150s.
El padre mata/espera su worker en el límite; una interrupción conserva el prefijo
durable. Captura acotada a2MiB/8192frames/8192bytes por línea/24000bytes de texto.
Los roles auth de corrección/review siguen solicitando sus límites menores originales.

`request.json` liga binding/model/messages/schema/budget, `started.json` impide
otro worker, `dispatch.json` registra intención previa a POST, `before.json` y
`after.json` acreditan el pin observado, `frames.ndjson` conserva bytes recibidos,
`result.json` conserva resultado y digests. Se rechazan enlaces, archivos ajenos,
drift, JSON duplicado, modelos distintos y contadores fuera de reserva. El padre
puede consumir un resultado completo guardado aunque pierda stdout, sin repetir
inferencia. La inspección es lectura local, sin mutaciones del diario.

Un final sin verificación posterior o sin resultado durable no se acepta. Texto
parcial va en `partial_text`, nunca en `text` ni con uso inventado. `done_reason=length`
conserva truncación; las puertas de fuente/producto siguen rechazándolo. Conservar
evidencia de transporte no acredita corrección del código ni pruebas del SaaS.

Esto es una capacidad prospectiva. No reconstruye la llamada auth_api v1 que
usó el transporte anterior y tuvo TimeoutError/sent=true. Su binding, incertidumbre
y reserva íntegra permanecen. No se modifica ese expediente para reenviarlo o
aceptarlo; tampoco se activa una migración general de la cola. Los originales de
bindings anteriores conservan su transporte anterior. El controlador activo E:/IA
no se modifica. Los artefactos locales `.state` no se exportan automáticamente:
requests/text son datos no confiables y pueden contener contexto privado.

Pruebas: servidor y procesos simulados, explícitamente sin inferencias reales.
Verifican reanudación sin envío, captura parcial, final sin pin, deriva, truncación,
cutoff total, límites y selección del nuevo protocolo. Falta calificar el protocolo
con una operación futura autorizada y diseñar unidades de trabajo que quepan en el
tiempo del modelo, sin repetir incertidumbres ni reiniciar budgets.
