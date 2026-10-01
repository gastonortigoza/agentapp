"""Contenido externo como datos y mediación acotada de propuestas de edición.

Fixtures de 1B: no autentica conectores ni habilita herramientas de red, secretos,
aprobación o despliegue. El modelo propone; el controlador valida antes del sink.
"""
from dataclasses import dataclass
import hashlib
import re
from publication_policy import check_paths


@dataclass(frozen=True)
class ExternalText:
    source_id: str
    origin: str
    text: str
    parents: tuple = ()

    def __post_init__(self):
        if not isinstance(self.source_id,str) or not re.fullmatch('[A-Za-z0-9_:/.-]{1,160}',self.source_id):
            raise ValueError('Procedencia inválida')
        if self.origin not in {'github_issue','notion_page','tool_result','agent_summary'}:
            raise ValueError('Origen inválido')
        if not isinstance(self.text,str) or not 1<=len(self.text.encode('utf-8'))<=64000:
            raise ValueError('Contenido externo fuera de límites')
        if not isinstance(self.parents,tuple) or any(not isinstance(p,ExternalText) for p in self.parents):
            raise ValueError('Linaje inválido')
        if (self.origin=='agent_summary') != bool(self.parents):
            raise ValueError('Un resumen debe conservar sus fuentes')

    @property
    def trusted(self):return False

    @property
    def sha256(self):return hashlib.sha256(self.text.encode('utf-8')).hexdigest()


def summarize(source,source_id,text):
    if not isinstance(source,ExternalText):raise ValueError('Fuente no tipada')
    return ExternalText(source_id,'agent_summary',text,(source,))


class EditBoundary:
    """Política definida por el controlador; una propuesta no puede ampliarla."""
    def __init__(self,allowed_paths):
        check_paths(allowed_paths)
        self._allowed=frozenset(allowed_paths)
        if not self._allowed:raise ValueError('Sin rutas autorizadas')

    @property
    def allowed_paths(self):return self._allowed

    def process(self,source,actions,sink):
        if not isinstance(source,ExternalText):raise ValueError('Fuente externa sin procedencia')
        if not isinstance(actions,list) or len(actions)>20:raise ValueError('Propuestas fuera de límites')
        events=[]
        for action in actions:
            allowed=False
            kind=action.get('action') if isinstance(action,dict) else None
            reason='action_not_authorized'
            if isinstance(action,dict) and set(action)=={'action','path','description'} and kind=='propose_edit':
                try:
                    check_paths([action['path']])
                    if action['path'] not in self._allowed:raise ValueError('Ruta fuera de alcance')
                    if not isinstance(action['description'],str) or not 1<=len(action['description'])<=4000:
                        raise ValueError('Descripción inválida')
                    allowed=True
                except (ValueError,TypeError):reason='path_or_schema_denied'
            event={'source_id':source.source_id,'source_sha256':source.sha256,
                   'untrusted':True,'decision':'allowed_proposal' if allowed else 'denied',
                   'reason':'within_authorized_scope' if allowed else reason}
            events.append(event)
            if allowed:
                # No reenvía claves adicionales, aprobaciones ni instrucciones como herramientas.
                sink.prepare_edit(path=action['path'],description=action['description'],source=source)
        return events
