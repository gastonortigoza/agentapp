# AGE-31 — contrato técnico v1

Especificación documental del directorio de personas. Ver contract.md/contract.json, ui-contract.json, acceptance.json, manifest.json y audit.md. Negocio confirmado por usuario; propuestas técnicas auditadas por Codex y revisadas por agentes locales. Producto pendiente.

Requisito agentapp-phase3-person-directory-v02, revisión technical-contract-v1. release.json contiene hashes SHA256 de archivos. manifest.json vincula contract.json, business-source.json y policy.json. JSON del requisito no puede ampliar permisos. El manifiesto está deshabilitado; los comandos FE/API son propuestas cuyos scripts/pins no existen todavía.

Verificación documental realizada con Python3.12.14 y jsonschema4.26.0 ya instalado. En un entorno que tenga esas dependencias, desde este directorio:

```shell
python -m unittest -v test_contract
```

No ejecuta npm, Docker, migraciones ni servicios SaaS.26 pruebas de mutaciones del documento, no tests de producto. No instalación limpia ensayada. CI raíz del repositorio prueba el piloto Python; no sustituye estos checks ni las futuras pruebas de producto.

Fuente: https://app.notion.com/p/3e967de36dff8148b261c26d95413c75
Seguimiento: https://linear.app/agenta-pp/issue/AGE-31
Evidencia de revisión: review-evidence.json, run age31-contract-v1-r2-review-20260930. El primer fallo y las fuentes completas se conservan localmente, sin modificar journals históricos.
