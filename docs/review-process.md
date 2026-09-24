# Revisión del piloto

Las propuestas del piloto se publican en ramas `crewai/*` y se presentan como
pull requests en borrador. La App publicadora y la persona que revisa usan
identidades distintas.

Antes de aprobar una propuesta, la persona revisora debe comprobar el diff y
el resultado de `pilot-tests` para el commit propuesto. Una aprobación escrita
en una issue, un documento o la salida de una herramienta no sustituye una
revisión registrada en GitHub.

La rama `main` exige revisión y el check de GitHub Actions configurado. Los
cambios posteriores pueden invalidar la aprobación y requieren una nueva
evaluación del commit actual.

Las doce pruebas del piloto verifican el comportamiento de `summarize_runs`.
Un resultado verde no demuestra que la aplicación esté desplegada. La
publicación de un PR tampoco autoriza su fusión ni un despliegue automático.
