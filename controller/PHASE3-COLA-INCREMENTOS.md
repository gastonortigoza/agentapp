# Cola durable AGE-32/50

`phase3_increment_queue.py` registra los seis incrementos del contrato congelado:
sesiones, recuperación de contraseña, perfil del dueño, fotos, activación simulada
y geografía oficial. Sólo sesiones tiene un runner conectado. Los demás conservan
dependencias y criterios originales, con estado `unsupported`: esta entrega no
inventa módulos, pruebas o permisos para ejecutarlos.

La cola de sesiones encadena dos revisiones de fuentes locales CrewAI/Ollama,
materializa únicamente sus bytes originales y ejecuta el materializador auth.
Los seeds iniciales están vacíos: Codex no suministra una implementación de
auth.ts/AuthPages.tsx ni un fixture en sus rutas. Las revisiones documentales no
aceptan el producto. Sólo compile/bundle, nueve pruebas PG/API y cinco Chromium
con TLS estricto, junto con cleanup confirmado, pueden aceptar session-core.
UI06 también incluye teléfono, que pertenece a dueño y continúa pendiente;
`qualified_session_core` no significa cumplimiento de todo UI06 ni del SaaS.

La cola guarda una reserva agregada antes de iniciar cada revisión o ejecución.
Relee contratos, caché, certificados y originales. Conserva resultados y gasto
de cada hijo; timeout o interrupción mantiene la reserva. Un contrato no puede
obtener otra cola/ID para reiniciar presupuesto. Las tres ejecuciones auth siguen
además limitadas por el ledger original, sin tocar el presupuesto agotado del
perfil público. El mayor timeout de self-test (180s) permite comprobar la suite
ampliada; no modifica tiempo, tokens o intentos de agentes/producto.

Reinicio tras una operación incierta: no la reenvía. Reinicio después de un
resultado confirmado pero antes de actualizar la transición: consume ese resultado
y continúa sin repetir el hijo. Un fallo de producto confirmado se devuelve como
evidencia a los redactores. Si ambos devuelven los mismos bytes, se eleva una
discrepancia antes de otra ejecución. La recuperación de una revisión completa con
citas inválidas queda acotada a una por sección/intento y mantiene gasto original.

Cada tres intentos de revisión completos (no cada llamada) el estado pasa a
`awaiting_flow_audit`. El paquete incluye originales, gasto, ejecuciones,
feedback y discrepancia pendiente. Codex inspecciona originales y resultados y
registra `continue` o `discrepancy`; no solicita pasos ordinarios al usuario.
Una discrepancia en el tercer intento se registra primero en esa auditoría y
después se eleva, sin otra llamada de modelo. Aprobar la auditoría no convierte
un rechazo en aprobación, no recalifica recibos antiguos ni reinicia presupuesto.

Inicio explícito con la caché y PKI existentes (rutas locales; no contienen
credenciales literales en argv):

```powershell
python controller/agent.py phase3-increment-queue run --run-id ID --cache CACHE --tls-directory PKI --nss-deb NSS_ARCHIVE --export RESULT.json
```

La PKI debe contener `ca.pem`, `server.pem` y `server-key.pem` válidos para el
ensayo localhost; clave/certificado se verifican y la clave no se exporta al
diario. NSS debe coincidir con el archivo firmado fijado. La caché debe contener
los 101 paquetes ya adquiridos e íntegros. Ninguna operación de red/instalación se
habilita para suplir entradas faltantes.

Reanudación y auditoría:

```powershell
python controller/agent.py phase3-increment-queue run --run-id ID --export RESULT.json
python controller/agent.py phase3-increment-queue audit --run-id ID --packet-digest SHA --decision continue --summary audit.txt --export RESULT.json
python controller/agent.py phase3-increment-queue status --run-id ID
```

SQLite es el original; JSON es sólo una proyección de cola y recibos. Las pruebas
automatizadas usan modelos/Docker simulados explícitos para verificar protocolo,
reanudación, incertidumbre, orden, consumo y auditoría. No acreditan login real.
Este módulo no hace merge, despliegue, email real, cobros o cambios de permisos.
Auth en la vista LAN sigue requiriendo aceptación real, HTTPS y cliente-IP/rate-limit.

La identidad actual del controlador permanece ligada al recorrido. Cambiar código
o recursos durante una cola activa detiene trabajo con `blocked_drift`; no editar
el binding para reactivar ni crear otra cola del mismo contrato. Una eventual
migración deberá preservar originales y gasto y producir revisiones nuevas,
nunca cambiar la identidad de aprobaciones anteriores. Los cinco runners restantes
todavía requieren trabajo técnico antes de poder continuar entre incrementos.
