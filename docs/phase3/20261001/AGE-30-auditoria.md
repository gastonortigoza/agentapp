# AGE-30 — revisión de base, 01/10/2026

La revisión y la instalación limpia están verificadas. La integración permanece pendiente de aprobación humana. Main observado `9c02be1d1ce9465eb2338ac3981d21f2a2c1009c`; no se presenta un SHA integrado inexistente.

| PR | Head exacto | Estado observado | Alcance |
|---|---|---|---|
| [5](https://github.com/gastonortigoza/agentapp/pull/5) | 0a0a6e44b93c80c90eacdedae32ee8e7af0c7054 | borrador abierto/sin revisiones | snapshot controller,94 archivos cambiados |
| [6](https://github.com/gastonortigoza/agentapp/pull/6) | 1ffbfe0f6928c5496ce40c84e4bf58be9f21450f | borrador abierto/sin revisiones | remote_summary.py y tests,2 archivos |
| [7](https://github.com/gastonortigoza/agentapp/pull/7) | 970aa9085cc4df0b1ed314babf666cee6e050cd7 | borrador abierto/sin revisiones | contrato técnico v1,documentación/validador |
| [8](https://github.com/gastonortigoza/agentapp/pull/8) | 0a0a6e44b93c80c90eacdedae32ee8e7af0c7054 | borrador de mantenimiento por App | mismo código de5,revisión humana posible |

Base de5/6/7:main indicado arriba. #8 preserva ese head y no incorpora #6 por suposición. PR anteriores1/2/4 fueron inventariados en la evidencia del ticket: documentación/revisión y attention_summary; no se incluyen sin requisito. El snapshot del #5 está íntegro:92 hashes de SOURCE-MANIFEST.json coinciden con sus fuentes. No se tocaron fuentes del controlador activo con cambios de otros chats ni la evidencia histórica de AGE-21.

Instalación nueva con uv0.12.16 usando `sync --locked` sobre pyproject.toml/uv.lock del snapshot, Python3.12.14.154 paquetes resueltos. Suite desde ese entorno: **373 passed,1 skipped,547 warnings,82.42s**. Omisión por host Windows sin permiso para crear symlinks. Las advertencias se conservan; tests focales usan simuladores de adaptadores cuando corresponde y no acreditan despliegue ni SaaS. Archivos `AGE-30-instalacion-bloqueada.txt` (nombre indica lockfile,no fallo), `AGE-30-controller-suite-frozen.txt/xml`.

Combinación aislada del piloto raíz #5 con los dos archivos exactos del #6: **22 unittest aprobadas**. No se modificó ningún checkout existente ni main. El #6 procesa execution_status,cuenta attention y valida contratos acotados del piloto. No añade la vertical.

CI observado de5/6/7 en los SHA indicados:pilot-tests completed/success. Workflow actual ejecuta unittest raíz en Ubuntu24.04/Python3.12.14; checkout/setup-python fijados por SHA,permissions contents:read y persist-credentials:false. No ejecuta controller/ ni pruebas PostgreSQL/FE/BE. Suite Windows del controlador y CI del piloto se registran por separado.

Revisión focalizada del controlador: manifiestos de pilotos fijos sin comandos arbitrarios; ejecución Docker aislada y sin credenciales; fuente/modelo/dependencias ligados al gate; publicación por App con token limitado a repo/permissions y claves DPAPI fuera del árbol; recibos de intención/operación incierta y validaciones del contenido remoto. Estos controles no equivalen a habilitar un proyecto SaaS general. Lockfile y configuración pública forman parte del snapshot; no se incluyen claves, tokens ni bases de runtime. Búsqueda limitada de patrones de claves privadas y tokens sin candidatos; no es una garantía absoluta de ausencia de secretos ni una auditoría de seguridad completa.

[Ruleset23855599](https://github.com/gastonortigoza/agentapp/rules/23855599) activo sin bypass: una aprobación,CODEOWNER,aprobación del último push,CI estricto GitHub Actions y squash. CODEOWNERS contiene únicamente @gastonortigoza, autor del #5. El #8 usa la App existente y el mismo head para que Gastón pueda revisar/aprobar sin cambiar esas reglas. No se cierra #5 ni se declara aceptado automáticamente. #6 admite revisión humana por ser autor App; #7 conserva el bloqueo de autoaprobación si se decide integrar esa documentación.

Faltan revisión/aprobación y autorización de merge de8/6, actualización de la base si main cambia y verificación de checks del head definitivo. AGE-30 sigue In Progress y AGE-32/50 no se habilitan. Publicar borradores no autoriza integración, despliegue,cobros ni cambios de permisos.
