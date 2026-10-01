# Piloto agentapp: resumen de ejecuciones

Entrega local validada en fase 1A. `summarize_runs(records)` cuenta los registros
por estado, normaliza espacios y trata estados ausentes como `unknown`.
Rechaza entradas que no sean listas de diccionarios y conserva la entrada.

Requiere Python 3.12.14, sin dependencias externas para este piloto.

```shell
python -m unittest discover -s tests -v
```

La CI propuesta ejecuta las doce pruebas del piloto en cada push a main o
crewai/** y en cada pull request. No publica paquetes ni despliega aplicaciones.
Este paquete no contiene el controlador local, sus credenciales o historiales.

El [contrato del piloto](docs/pilot-contract.md) describe las entradas, la salida,
los errores y el alcance de los estados resumidos, con un ejemplo de uso.
