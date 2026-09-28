# IA local verificable

## Fase 2: base local e inventario de conexiones (24/09/2026)

Se incorporaron `agent.cmd integration-status`, `agent.cmd integration-login linear`
(también Notion) e `agent.cmd integration-probe <notion|linear|coolify>`.
La consulta de estado informa disponibilidad local sin leer valores
de credenciales. El segundo sólo inicializa MCP e inventaría nombres y huellas de
esquemas; no ejecuta herramientas remotas. Usa destinos HTTPS fijados en
`config/phase2.json`, no sigue redirecciones y exige la suite vigente. La
configuración nueva forma parte de la identidad de la suite y está protegida
contra publicación como código de aplicación.

Los recursos de Notion, Linear y Coolify fueron seleccionados y registrados; los
conectores siguen desactivados hasta autenticar y aceptar el runtime local.
La sesión de navegador/conversación no se transfiere al controlador.
`integration-login` prepara consentimiento OAuth explícito con callback loopback,
PKCE y comprobación de state. Los tokens y metadatos se cifran con DPAPI CurrentUser
fuera del repositorio, en `E:/IA/credentials/phase2-oauth`. El controlador conserva
el vencimiento entre procesos y puede renovar una concesión existente. La prueba
de renovación usa proveedor simulado; su aceptación real sigue pendiente.
La operación no interactiva nunca abre consentimiento nuevo ni amplía permisos.
Linear solicita exclusivamente `read` y usa el endpoint de sólo lectura. Los hosts
OAuth se restringen por servicio, sin redirecciones HTTP ni uso de proxies del
entorno. La sonda también admite un bearer inyectado por variable de entorno.
No confundir una clave REST de Notion con un token válido de su MCP remoto.

El consentimiento se prepara en `.state/phase2-auth/<servicio>-authorization.json`
durante cinco minutos. Ese archivo contiene URL de consentimiento y parámetros
públicos, no tokens ni el verificador PKCE; se elimina al cerrar el proceso.
No copiar tokens, códigos de callback ni credenciales en el chat. Cambios en la
concesión requieren nuevo consentimiento; un refresh rechazado bloquea.

`integration_sync.py` contiene un registro SQLite de intenciones, versiones,
pausas y resultados, ejercitado exclusivamente mediante `SimulatedAdapter`.
Mantiene identidad por servicio/recurso/ID externo/acción, impide cambiar una
intención existente y conserva operaciones inciertas sin volver a enviarlas.
Los simuladores representan anexar una nota y crear una tarea; no implementan
todavía los adaptadores reales de Notion o Linear. La preservación de contenido,
conflictos, expiración, concurrencia y recuperación están probados en simulación.
La caída se inyecta con una excepción terminal y reapertura del almacén; no se
presenta como una prueba de muerte de proceso con un servicio real.

Pendientes de aceptación: OAuth/renovación no interactiva,
esquemas MCP reales y operaciones por servicio; mapeo de estados y automatismos
nativos de Linear; límites de escritura concurrente en Notion; tarea real con
enlaces verificables y reintento desde el runtime; inventario Coolify por MCP.
Ya se creó un proyecto vacío exclusivo AgentApp en Coolify y se inventarió por
navegador su servidor. La documentación de Notion y las tareas AGE-5/6/7 en Linear
son recursos reales preparados desde la conversación, no evidencia de ejecución
automática del controlador. No se habilitaron escrituras automáticas ni despliegues.

## Controlador incremental del plan v1.7

Desde PowerShell en esta carpeta:

```powershell
.\agent.cmd manifest-check
.\agent.cmd self-test
.\agent.cmd run-stub
.\agent.cmd run-local
.\agent.cmd status --json
.\agent.cmd log <run_id> --limit 20
```

`run-stub` ejecuta simulaciones identificadas con `scope=stub_only`: `delivered` allí
significa simulación terminada. `run-local` usa los roles existentes de CrewAI y Ollama
en el piloto fijo `summarize_runs`: planner y tester antes de la solución, developer,
pruebas de contrato en Docker y reviewer. Su alcance es `local_agents_docker`.
Una entrega real contiene código, diff y evidencia para revisión; no integra código
automáticamente en el repositorio ni publica. El flujo anterior `flow.py` sigue separado.

Para probar pausa y reanudación, iniciar con `run-stub --until planning`, consultar la
versión con `status --json` y usar `pause <run_id> --version <n>` seguido de
`resume <run_id> --version <nueva_version>`. Continuar con `run-stub --run-id <run_id>`.
La versión evita aplicar una orden sobre un estado que otro proceso ya cambió.
Los runs locales se continúan con `run-local --run-id <id>`; aceptan los mismos puntos
de parada `--until`. Un timeout de inferencia queda incierto: matar el cliente no prueba
que Ollama haya dejado de consumir. No hay repetición automática ni resolución falsa.

SQLite conserva estado/eventos, contratos congelados, huellas, operaciones y contadores
en `.state/controller.sqlite`. Los eventos y el estado se confirman juntos. Una pausa
impide nuevos despachos, pero no revierte los ya autorizados. Cambios en archivos
observados durante la pausa fuerzan nueva planificación; el contrato original conserva
los permisos. Operaciones inciertas requieren reconciliación del adaptador: todavía no
hay adaptadores externos ni comando público para resolverlas.

La suite aprobada queda ligada a hashes de fuentes, tests, esquema, dependencias y
runtime. Cambiar esos archivos exige repetir `self-test`; estado/logs y pausa siguen
disponibles con suite roja. El recibo es un control local entre componentes de confianza,
no una firma resistente a otro programa que corra bajo la misma cuenta Windows.

Los esquemas 1.0 (stub) y 1.1 (piloto local) admiten JSON/YAML estricto y cierran los
conectores, secretos y despliegues. Los manifiestos son `config/pilot-manifest.json` y
`config/local-pilot.json`. La versión 1.1 acepta sólo el comando concreto del ejecutor,
los límites verificados y los hashes del requisito/tests. No es un ejecutor de comandos
arbitrarios ni un contrato de proyecto SaaS general.

Métricas de simulación: `.state/metrics/<run_id>.json`. Entregas locales:
`.state/deliveries/<run_id>/`, con métricas por rol, evidencia, eventos y, si se aprueba,
`status_summary.py` y `proposal.diff`. Los tokens reales se obtienen de Ollama; las
reservas pendientes se descuentan del presupuesto visible. Una respuesta truncada,
un digest distinto, un fallo de tests o revisión negativa bloquean la entrega.
Una ejecución aislada no constituye un benchmark de calidad.
La configuración actual de `backup.py` excluye `.state`: estos nuevos datos todavía
requieren la política de backup y restauración de DEV-020.

El ejecutor anterior ahora fija la imagen por digest y usa `--pull never`: si falta
la imagen validada, falla en lugar de descargar otra revisión silenciosamente.

El piloto local tiene una ronda, 600 segundos acumulados de operaciones, llamadas con
timeout de 120 segundos, contexto de 8192 tokens por rol y topes de salida por rol.
Los agentes no reciben herramientas ni secretos y el modelo se verifica por digest antes
y después de cada llamada. El código generado pasa un filtro AST limitado al piloto y
se ejecuta sólo dentro del sandbox Docker. Los tests confiables se fijan antes de generar
código. El filtro AST complementa el sandbox y no pretende servir para Python arbitrario.

Los límites de CPU/memoria/procesos del manifiesto se aplican al sandbox. Ollama es el
servicio local preexistente: no se impone aquí una cuota dura a su RAM/VRAM. El control
de tokens combina reserva conservadora, límites de generación y verificación del consumo
devuelto; no reemplaza un tokenizer exacto del modelo. Estado `uncertain_operation`
conserva reservas y señala consumo incompleto en vez de declarar costo nulo.

El cierre de 1A se evalúa contra el piloto local fijo: inventario, entrega real,
requisito positivo/negativo, fallo sembrado, reinicio conservando checkpoints,
manifiestos inválidos bloqueados, límites efectivos del sandbox y recuperación de
la revisión validada. La ampliación a otras tareas y la reconciliación de efectos
externos pertenecen a la evolución posterior; esta última es obligatoria antes de 1B.
La persistencia usa SQLite explícito; la migración v1→v2 conserva runs previos.

El sandbox admite hasta 1 MiB de archivos de entrada, con raíz y entrada de sólo
lectura; el espacio temporal escribible `/dev/shm` está limitado a 16 MiB. No se
persisten logs Docker, la salida capturada es acotada y no se permite swap adicional.
El límite de disco de 128 MiB del piloto es un techo, no espacio reservado. No es
una cuota de almacenamiento global del servicio Ollama ni retención de todo el historial.

Studio fue inspeccionado y probado con su código instalado y una base sintética bajo
la misma cuenta Windows: permite altas, modificaciones y bajas sin autenticación
en su API local. Su lanzador configura localhost y SQLite propio; no se lo considera
de sólo lectura. En la prueba sus ediciones no cambiaron los archivos CLI observados.
Studio no está habilitado como fuente operativa de este piloto; no hay sincronización
validada ni se lo utiliza para otorgar aprobaciones.

Instalación en E:/IA para Ollama, Open WebUI, CrewAI, Docker, Qdrant y MCP.

Abrí `E:\IA\abrir-ia.cmd` para usar el menú. Ollama, Docker Desktop y Open WebUI deben estar activos. El chat está en http://127.0.0.1:8080. El menú ejecuta tareas reales y muestra su resultado; todavía no hay una interfaz web propia para los agentes.

## Qué funciona

- Developer → Reviewer → tests reales en Docker → Tester, con hasta tres rondas.
- Recuperación de 20 documentos resumidos del plan y de ingeniería social. Piloto de 60 preguntas curadas; no sustituye una evaluación con documentos reales del usuario.
- Investigación con Planner, Researcher, Analyst, Verifier y Synthesizer sobre fuentes seleccionadas. Conserva evidencia y límites; no hace búsqueda web abierta autónoma.
- GitHub conectado a gastonortigoza/agentapp. Lectura validada con el modelo local. Publicación revisada de archivos explícitos en una rama nueva `crewai/*` mediante `github_publish.py`.
- Servidor MCP local de lectura, respaldos cifrados con DPAPI y logs por ejecución.

## Archivos principales

- `config/agents.yaml`: roles, objetivos, prompts, thinking y límites editables.
- `flow.py`: circuito de desarrollo y decisión con pruebas reales.
- `rag.py`: indexar, evaluar y preguntar al corpus piloto.
- `osint_flow.py`: investigación y verificación de evidencia.
- `github_connection.py` y `github_publish.py`: acceso a GitHub y publicación revisada.
- `config/mcp.json`: conexión stdio para clientes MCP; no cambia configuraciones globales.
- `smoke.py --stage H`: validación acumulativa A–H.
- `backup.py`: respaldo cifrado y comprobación de recuperación en memoria.

Usá `.venv\Scripts\python.exe` para ejecutar los scripts desde esta carpeta. Dependencias fijadas en `pyproject.toml` y `uv.lock`; configuración de versiones completa en `E:/IA/config/versions.json`.

## Resumen de ejecuciones

`agent.cmd status --summary` muestra totales por estado y separa agentes locales,
simulaciones y registros anteriores. Agregá `--json` para obtener datos estructurados.
Los avisos de evidencia incompleta se conservan; el estado registrado no prueba que
un proceso siga activo. `status --json` mantiene su formato de lista detallada.

La función `status_summary.py` se incorporó desde el piloto revisado
`f9487c8b-199b-422d-9032-79e9a51ca5c0`, después de sus pruebas en Docker.
La incorporación es explícita: nuevas entregas de agentes siguen siendo propuestas.

Una pausa entre la preparación y el despacho conserva la operación pendiente.
Al reanudar, el controlador reutiliza su identificador y su reserva si contrato y
fuentes siguen coincidiendo. Una operación ya despachada o incierta no recibe este
tratamiento y no se reintenta automáticamente. La reconciliación de resultados
inciertos sigue pendiente; no equivale a reanudar una operación aún no enviada.

El flujo local admite un solo proceso ejecutor por base e identificador de ejecución.
Un segundo `run-local --run-id` concurrente devuelve `busy: true` y código 2 sin
recuperar ni alterar las operaciones del primero. El bloqueo lo mantiene el sistema
operativo durante el flujo y se libera al terminar el proceso, incluso ante una caída.
Liberarlo no demuestra que una llamada externa haya terminado: las operaciones
despachadas pendientes siguen requiriendo reconciliación. Este mecanismo es local;
no es coordinación distribuida entre máquinas ni protección contra cambios manuales
de archivos de bloqueo o escrituras directas en SQLite.

## Límites actuales

### Recuperación de publicación de ramas (avance de fase 1B)

El publicador manual `github_publish.py` conserva ahora intención, intentos y
eventos en `publication.sqlite`, fuera del checkout. Requiere la aprobación del
manifiesto exacto y la suite vigente del controlador. Una caída posterior al push
se reconcilia comparando repositorio, rama y SHA; repetir la llamada devuelve el
resultado confirmado sin otro push. La creación usa un lease que exige una rama
inexistente, nunca sobrescribe una referencia existente.

```powershell
.\agent.cmd publication-status <job_id>
.\agent.cmd publication-pause <job_id> --version <version>
.\agent.cmd publication-resume <job_id> --version <version>
.\agent.cmd publication-resolve <job_id> --version <version> --decision confirm-applied
.\agent.cmd publication-resolve <job_id> --version <version> --decision confirm-not-applied
.\agent.cmd publication-resolve <job_id> --version <version> --decision defer
```

Las resoluciones consultan el remoto y registran la identidad local del operador.
`confirm-not-applied` sólo habilita otro intento cuando la rama está ausente: una
solicitud tardía sigue siendo posible, pero el lease de creación evita sobrescribir
o duplicar la referencia. Esta garantía es específica de Git; no se aplica a crear
PRs. Hay como máximo tres despachos por trabajo, sin reintentos automáticos.
La pausa impide un nuevo despacho; un push ya despachado puede completarse y se
conserva su confirmación. Dos publicadores del mismo trabajo no envían simultáneamente.

El recibo durable pasa a ser SQLite; `published.json` de publicaciones anteriores
queda como evidencia histórica y no se convierte automáticamente en autorización.
La reconciliación no ejecuta código del candidato ni registra respuestas remotas
arbitrarias. Los estados del piloto local y las publicaciones se consultan por separado.

Este avance está probado con repositorios Git descartables locales, incluyendo una
caída real de proceso. Todavía faltan aceptación de PRs reales, CI remoto, identidad revisora compatible,
protecciones y casos adversarios de conectores para cerrar 1B. No habilita publicación
automática y no resuelve resultados inciertos de llamadas a Ollama.

### PR borrador y vínculo con la entrega del controlador

`delivery-prepare` prepara una nueva propuesta desde un run local `delivered` del
piloto `status_summary_v1`. Comprueba el candidato, pruebas y revisión durables;
exporta sólo `status_summary.py` y las doce pruebas con su import adaptado. El
manifiesto de publicación incluye el run padre y hashes de evidencia. No reabre
el run, no cambia sus permisos y no publica: requiere una aprobación propia.

```powershell
.\agent.cmd delivery-prepare <run_id> --branch crewai/nombre
.\agent.cmd pr-prepare <job_id> --title "Descripción breve" --body-file descripcion.md
.\agent.cmd pr-open <job_id> --approved-digest <digest-del-PR>
.\agent.cmd pr-status <job_id>
.\agent.cmd pr-pause <job_id> --version <version>
.\agent.cmd pr-resume <job_id> --version <version>
.\agent.cmd pr-resolve <job_id> --version <version> --decision confirm-applied
.\agent.cmd pr-resolve <job_id> --version <version> --decision defer
```

La rama debe estar publicada y confirmada antes de `pr-prepare`; el repositorio
debe tener una rama base distinta. La aprobación de la rama no aprueba el PR:
la segunda propuesta incluye título, cuerpo, destino, SHAs y marcador estable.
`pr-open` crea exclusivamente un borrador, sin aprobar, fusionar ni desplegar.

Cada PR tiene su registro `pull-request.sqlite`. Tras una caída o timeout se
buscan PRs abiertos y cerrados comparando marcador, repositorio, ramas y SHAs.
La ausencia no demuestra que un POST no pueda llegar tarde: se permite un solo
despacho y `confirm-not-applied` está bloqueado para este adaptador. Si aparecen
dos coincidencias, se bloquea por ambigüedad. Las respuestas se reducen a evidencia
de identidad; texto externo que afirme aprobaciones no modifica la política.

La comparación de base SHA es conservadora: un avance de la rama base o la edición
del marcador puede impedir reconciliar automáticamente. Se conserva la operación
incierta y no se crea otro PR. No hay garantía de exactamente una vez en GitHub.
Los tests incluyen una muerte real de proceso tras un efecto remoto simulado,
persistencia fallida, dos workers, pausa, identidades ajenas y respuestas tardías.
El adaptador HTTP se aceptó contra GitHub real con el PR borrador #1: una caída
después de crear el PR se reconcilió sin duplicarlo.

### Verificación de CI del commit publicado

`agent.cmd ci-check <job_id>` consulta únicamente GitHub y requiere la suite
vigente, incluida `config/ci-policy.json`. El job de publicación debe estar
confirmado. Se validan el commit y la rama actuales, un workflow de evento `push`,
su intento actual, los jobs requeridos y sus checks de GitHub Actions. El emisor
se identifica por ID, no por un nombre o texto que afirme aprobación.

El workflow y los tests del piloto deben conservar sus blobs Git confiables.
Una prueba omitida, cancelada, ausente, ambigua, de otro intento o de otro SHA
bloquea la verificación. No se acepta un verde anterior para ignorar una ejecución
posterior del workflow. Las URL de checks se validan antes de consultar y nunca
se sigue un destino arbitrario recibido de una herramienta.

El resultado sólo verifica CI de `push` para ese commit; conserva
`merge_authorized=false`. No sustituye aprobación humana, protección de ramas,
revisión de credenciales ni pruebas de producción. CI real fue verificado para
el commit publicado del PR #1. `main` tiene el ruleset activo
`phase-1b-main-reviewed-ci`, con revisión humana, check `pilot-tests` del emisor
GitHub Actions, sin bypass y con prohibición de borrado y force push.

### Frontera de contenido externo y controles protegidos

`publication_policy.py` excluye rutas del controlador, infraestructura, secretos,
workflows y pruebas de control del publicador ordinario. Las pruebas del piloto
deben conservar exactamente el contenido confiable. Se comprueba al preparar
archivos y se vuelven a comprobar las rutas antes de publicar.

`external_content.py` conserva texto, origen y linaje como datos no confiables.
Sólo admite propuestas de edición de rutas autorizadas mediante un esquema
cerrado; no ofrece capacidades de aprobación, secretos, red o despliegue.
Los cinco casos adversarios del plan se probaron con acciones y adaptadores
simulados. Esta frontera todavía no está integrada con conectores reales ni
constituye una evaluación de ataques contra un modelo en vivo.

La fase 1B sigue abierta: falta registrar e instalar la identidad publicadora,
validar sus credenciales reales y verificar una revisión humana independiente sobre
un PR creado por ella. El PR #1 fue creado por la cuenta humana y sigue borrador.

`github_app_auth.py provision --app-id <id> --pem <ruta>` verifica la identidad,
instalación y permisos y solicita un token limitado al repositorio configurado.
Guarda la clave con DPAPI CurrentUser en `E:/IA/credentials/github-app`, fuera del
proyecto y del workspace de agentes. `config/github-app.json` sólo contiene IDs;
su presencia activa la identidad App en Git de red y el adaptador de PR/CI.
Después de provisionar hay que ejecutar `agent.py self-test` porque cambia la
configuración confiable. `github_app_auth.py check` muestra evidencia sin tokens.

Una App configurada que falla bloquea la operación y no vuelve a la cuenta
humana. Se verifica el alcance del token emitido y se entrega sólo al entorno
del subproceso; la clave y los tokens no se pasan a los modelos. El PEM original
permanece donde el usuario lo guardó. DPAPI requiere la misma cuenta Windows y
no protege de otro proceso que ya ejecute con esa cuenta. La lectura general de
GitHub conserva la sesión humana existente. Este soporte se prueba con API
simulada y DPAPI real; la aceptación con una App real permanece pendiente.

La investigación emplea un paquete de fuentes guardado, no todo Internet. Algunos intentos de descarga de fuentes fallaron y se conservan resúmenes previos identificados como tales. Las fuentes y resultados del modelo requieren juicio humano. Las credenciales permanecen fuera del repositorio. Los backups locales cifrados requieren la misma cuenta de Windows y sus claves DPAPI; todavía no hay copia externa.

No se habilitó routing a modelos pequeños: falta evidencia comparativa que lo justifique. MTP se midió en tareas cortas y no se adoptó como predeterminado. PostgreSQL y Notion requieren elegir destinos concretos; no se configuraron.


### Propuestas de tickets de Linear (fase 2)

El controlador dispone de `agent.cmd linear-ticket prepare <propuesta.json>`,
`linear-ticket status <operation>` y `linear-ticket execute <operation>`.
La propuesta exige external_id, title, description, requirement, run_id y pr
(`pending` cuando todavía no hay PR). El destino está fijado al proyecto
AgentApp de fase 2 y al equipo AGE; el agente no puede proporcionar herramientas,
proyectos, credenciales ni permisos adicionales en la propuesta.

`linear-ticket login` solicita un consentimiento separado de lectura/escritura,
con credenciales DPAPI independientes del lector existente. El login no crea
tickets. Ejecutar requiere la suite vigente y un intento previamente registrado.
La creación usa MCP, comprueba workspace/proyecto/estado, lee el ticket remoto y
conserva su ID. Después de timeout o interrupción, solo reconcilia: si la evidencia
es ausente, duplicada o modificada, mantiene `uncertain` y no vuelve a crear.
El escaneo incluye tickets archivados, con un límite de 5000 tickets por intento;
si no puede completarse, bloquea. No garantiza exactamente una vez frente a la
pérdida de la base local o a escritores externos no coordinados.

Esta entrada permite al planificador presentar tickets al controlador; no activa
un agente periódico ni actualizaciones de estado. La creación real de AGE-8 y
su reintento sin duplicados fueron verificados con el consentimiento del usuario.


### Evidencia Notion y propuestas del planificador

`agent.cmd notion-note prepare <id-estable> <archivo.txt>` registra una nota para
la página autorizada de evidencia. `notion-note execute <operacion>` verifica
la revisión, anexa mediante reemplazo de un ancla que conserva el texto original
y comprueba el resultado. Los resultados inciertos solo se reconcilian.
No reescribe la página completa. El formato de párrafos y los escapes del marcador
se normalizan para comparar la representación devuelta por Notion.

El contrato 1.2 permite exclusivamente el proyecto AgentApp y su página de
evidencia. El Flow local prepara y despacha una propuesta por paso del planner al
salir de planning cuando el contrato autoriza el proyecto. Las identidades son estables por run/paso, y un cambio de propuesta
entra en conflicto. Los contratos 1.0/1.1 conservan sus restricciones originales.
El piloto predeterminado sigue en 1.1 y sin red externa. `agent.cmd run-local
--connected` crea un contrato 1.2 nuevo para este piloto y el proyecto autorizado;
no amplía permisos de ejecuciones existentes. Cada envío revalida el checkpoint
y comprueba pausas/cambios de versión antes de escribir. Un resultado incierto
bloquea el avance y conserva las propuestas para reconciliación. Las escrituras
solo las realiza el controlador con OAuth; el modelo nunca recibe credenciales.

La política vigente de estados es report_only: el controlador observa el estado
humano de Linear, pero no lo sobrescribe porque el MCP no dispone de comparación
atómica. El avance de ejecución se registra mediante notas durables en Notion.
`agent.cmd linear-sync-states <run-id>` observa/reconcilia sin nuevas escrituras
de estado. Los recibos anteriores permanecen históricos.

`agent.cmd run-local --connected --requirement-id <id>` lee un requisito JSON
acotado de la página Notion autorizada. El contrato 1.3 captura ID, revisión,
parámetros y hash como snapshot inmutable; no permite que el contenido remoto
amplíe permisos. Los campos input_key y attention_states definen la función
remote_summary y sus pruebas se fijan antes de generar código.

`agent.cmd closeout <job-id>` verifica PR y CI del commit exacto, agrega enlaces
de regreso a los tickets y anexa la evidencia de cierre. Cada efecto conserva
intención y recibo; un resultado incierto se reconcilia, nunca se reenvía a ciegas.

`agent.cmd run-local --connected --pilot attention-summary` ejecuta el segundo
piloto fijo: añade needs_attention al resumen, contando failed, uncertain,
blocked_evidence, blocked_budget y uncertain_operation. Su requisito y ocho
pruebas se fijan por hash antes del modelo. Se exporta como attention_summary,
sin reemplazar status_summary ni cambiar contratos anteriores.

### Inventario Coolify

`agent.cmd coolify-inventory` lee únicamente el equipo, proyecto AgentApp,
entorno production y servidor autorizados. Abre un túnel SSH propio y lo cierra
al terminar. Verifica clave de host y dueño del socket antes de enviar el token
de lectura almacenado mediante DPAPI. No permite herramientas de escritura ni
recursos arbitrarios, aunque el servidor anuncie esas capacidades.
Requiere suite vigente, la clave SSH temporal autorizada y Windows OpenSSH.
La autorización vence el 26/09/2026 a las 20:55:59 UTC; después bloquea sin
renovar accesos. La configuración genérica integration-probe permanece separada.
