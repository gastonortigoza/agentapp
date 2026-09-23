# Contrato del resumen de ejecuciones

`summarize_runs(records)` recibe una lista de diccionarios y devuelve:

- `total`: cantidad de registros recibidos.
- `by_status`: conteo por estado, con claves ordenadas alfabéticamente.

El campo leído es `recorded_status`. Los strings se normalizan quitando espacios
al inicio y al final. Si el campo falta, no es string o queda vacío, se cuenta
como `unknown`. El campo `status` no es un alias. No se modifica la entrada.

```python
from status_summary import summarize_runs

records = [
    {"recorded_status": " delivered "},
    {"recorded_status": "blocked_evidence"},
    {},
]
assert summarize_runs(records) == {
    "total": 3,
    "by_status": {"blocked_evidence": 1, "delivered": 1, "unknown": 1},
}
```

La función lanza `TypeError` si la entrada no es una lista o si cualquiera de sus
elementos no es un diccionario. Una lista vacía produce `total: 0` y un diccionario
de conteos vacío. Los estados Unicode se conservan después de normalizar espacios.

Las doce pruebas en `tests/test_status_summary.py` cubren conteos, normalización,
orden, entradas inválidas, valores desconocidos, alias y conservación de la entrada.
La CI del repositorio las ejecuta con Python 3.12.14.

Este piloto resume estados registrados: no prueba que un proceso siga activo ni
que una aplicación haya sido desplegada. La aprobación de CI tampoco representa
una aprobación de merge o de despliegue.
