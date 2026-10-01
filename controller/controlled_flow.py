"""Prototipo CrewAI Flows; adaptador stub, sin modelos ni comandos externos."""
import os
import time
import json
import manifest

os.environ['OTEL_SDK_DISABLED'] = 'true'
os.environ['CREWAI_TRACING_ENABLED'] = 'false'
os.environ['CREWAI_TELEMETRY_DISABLED'] = 'true'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'

from crewai.flow.flow import Flow, start
from pydantic import BaseModel
from controller import Store, Conflict


class StubState(BaseModel):
    run_id: str = ''
    database: str = ''
    until: str = 'delivered'


class ControlledStubFlow(Flow[StubState]):
    @start()
    def execute(self):
        store = Store(self.state.database)
        while True:
            row = store.get(self.state.run_id)
            state = row['state']
            if state == self.state.until or state in {'delivered','rejected','paused','blocked_budget','blocked_evidence','uncertain_operation','awaiting_human_approval'}:
                return {'run_id':row['id'],'state':state,'scope':'stub_only'}
            if state == 'implementing':
                key = 'stub-'+str(row['version'])
                # Una única operación por avance: la reanudación consulta lo durable.
                with store.transaction() as db:
                    op = db.execute("SELECT * FROM operations WHERE run_id=? AND state != 'failed' ORDER BY rowid DESC LIMIT 1",(row['id'],)).fetchone()
                if op and op['state'] in {'in_flight','uncertain'}:
                    raise Conflict('Reconciliar antes de continuar')
                if not op or op['fingerprint_hash'] != manifest.identity(json.loads(row['fingerprint'])):
                    op_id = store.prepare(row['id'],row['version'],key)
                    op = {'id':op_id,'state':'prepared'}
                if op['state'] == 'prepared':
                    store.claim(op['id'])
                    started = time.monotonic()
                    # Sin efecto externo: evidencia sintética identificada como tal.
                    store.confirm_stub(op['id'],max(1,round((time.monotonic()-started)*1000)))
                row = store.get(row['id'])
                if row['state'] != 'implementing':
                    continue
            target = {'received':'contract_pending','contract_pending':'planning',
                      'planning':'implementing','implementing':'validating',
                      'validating':'awaiting_review','awaiting_review':'delivered'}.get(row['state'])
            if target is None:
                raise Conflict('Estado sin ruta automática')
            store.transition(row['id'],row['version'],target)
