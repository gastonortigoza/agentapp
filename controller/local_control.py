"""Operaciones del piloto real; sólo el runtime de confianza usa esta API."""
import json
import hashlib
from pathlib import Path
import uuid

from controller import Store, Conflict, TERMINAL, fingerprint
import controller_gate
import manifest
from local_pilot import digest, TESTS

EXPECTED = {'planner':('model','planning'), 'tester':('model','planning'),
            'developer':('model','implementing'), 'materialize':('files','implementing'),
            'tests':('tests','validating'), 'reviewer':('model','awaiting_review')}


def artifact(db, run_id, name):
    row=db.execute('SELECT body,sha256 FROM artifacts WHERE run_id=? AND name=?',(run_id,name)).fetchone()
    if row is None:return None
    data=json.loads(row['body'])
    if manifest.identity(data)!=row['sha256']:raise ValueError('Artefacto durable alterado')
    return data


def sandbox(run_id):
    from executor import WORKSPACE
    # ID del controlador, nunca una ruta del modelo.
    if str(uuid.UUID(run_id))!=run_id:raise ValueError('ID no canónico')
    return manifest.relative_path('local-'+run_id,WORKSPACE)


def require_evidence(db,row,target):
    manifest.validate(json.loads(row['manifest']),row['workspace'])
    candidate=artifact(db,row['id'],'materialize')
    if not candidate:raise ValueError('Falta candidato identificado')
    path=sandbox(row['id'])
    for name,key in [('solution.py','code_sha256'),('test_solution.py','tests_sha256')]:
        file=manifest.relative_path(name,path)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=candidate[key]:
            raise ValueError('Artefacto o pruebas cambiaron')
    from local_pilot import pilot_spec
    if candidate['tests_sha256']!=digest(pilot_spec(json.loads(row['manifest']))[1]):raise ValueError('Pruebas diferentes al contrato')
    if target in {'awaiting_review','delivered'}:
        tests=artifact(db,row['id'],'tests')
        if not tests or tests.get('status')!='pass' or tests.get('exit_code')!=0 or tests.get('timed_out') is not False:
            raise ValueError('Falta evidencia real de tests aprobados')
        if any(tests.get(k)!=candidate[k] for k in ('code_sha256','tests_sha256')):
            raise ValueError('Tests de otro artefacto')
        if tests.get('image')!=json.loads(row['manifest'])['pipeline']['docker_image']:
            raise ValueError('Imagen distinta de la autorizada')
    if target=='delivered':
        review=artifact(db,row['id'],'reviewer')
        if not review or review.get('approved') is not True or review.get('code_sha256')!=candidate['code_sha256']:
            raise ValueError('Revisión ausente, negativa o de otro artefacto')


class LocalStore(Store):
    def output(self,run_id,name):
        with self.transaction() as db:
            self._row(db,run_id)
            return artifact(db,run_id,name)

    def prepare_local(self,run_id,name):
        controller_gate.require_green()
        if name not in EXPECTED:raise ValueError('Operación no permitida')
        with self.transaction() as db:
            row=self._row(db,run_id)
            kind,state=EXPECTED[name]
            if row['execution_kind']!='local' or row['state']!=state:
                raise Conflict('Estado no autoriza esta operación')
            pending=db.execute("SELECT * FROM operations WHERE run_id=? AND state IN ('prepared','in_flight','uncertain')",(run_id,)).fetchall()
            if pending:
                previous=json.loads(row['fingerprint'])
                manifest.validate(json.loads(row['manifest']),row['workspace'])
                if fingerprint(row['workspace'],previous)!=previous:
                    raise Conflict('Fuentes cambiaron; pausar y revalidar')
                op=pending[0]
                if (len(pending)==1 and op['state']=='prepared' and op['logical_key']==name
                        and op['role']==name and op['kind']==kind
                        and op['manifest_hash']==row['manifest_hash']
                        and op['fingerprint_hash']==manifest.identity(previous)):
                    # Aún no hubo despacho: conservar identidad y reserva originales.
                    return op['id']
                raise Conflict('Operación pendiente; no se duplica')
            if db.execute('SELECT 1 FROM operations WHERE run_id=? AND logical_key=?',(run_id,name)).fetchone():
                raise Conflict('Operación ya intentada; no se repite silenciosamente')
            contract=json.loads(row['manifest'])
            manifest.validate(contract,row['workspace'])
            role=contract['roles'].get(name)
            reserved_input=role['context_tokens'] if kind=='model' else 0
            reserved_output=role['output_tokens'] if kind=='model' else 0
            reserved_ms=(contract['pipeline']['model_timeout_seconds']*1000 if kind=='model'
                         else 80000 if kind=='tests' else 5000)
            budget=contract['budgets']
            if any(row[used]+reserved>cap for used,reserved,cap in [
                ('input_tokens',reserved_input,budget['input_tokens']),('output_tokens',reserved_output,budget['output_tokens']),
                ('active_ms',reserved_ms,budget['active_seconds']*1000)]):
                self._event(db,row,'blocked_budget','reservation_denied',{'operation':name})
                return None
            previous=json.loads(row['fingerprint'])
            if fingerprint(row['workspace'],previous)!=previous:
                raise Conflict('Fuentes cambiaron; pausar y revalidar')
            op_id=str(uuid.uuid4())
            db.execute('INSERT INTO operations(id,run_id,logical_key,state,manifest_hash,fingerprint_hash,kind,role,reserve_input,reserve_output,reserve_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (op_id,run_id,name,'prepared',row['manifest_hash'],manifest.identity(previous),kind,name,reserved_input,reserved_output,reserved_ms))
            self._event(db,row,row['state'],'local_prepared',{'operation_id':op_id,'role':name,'reserved_input_tokens':reserved_input,'reserved_output_tokens':reserved_output})
        return op_id

    def dispatch_local(self,op_id):
        controller_gate.require_green()
        with self.transaction() as db:
            op=db.execute('SELECT * FROM operations WHERE id=?',(op_id,)).fetchone()
            if not op or op['state']!='prepared' or op['role'] not in EXPECTED:raise Conflict('Operación no despachable')
            row=self._row(db,op['run_id'])
            if row['execution_kind']!='local' or row['state']!=EXPECTED[op['role']][1]:
                raise Conflict('Pausa o bloqueo impide despacho')
            manifest.validate(json.loads(row['manifest']),row['workspace'])
            previous=json.loads(row['fingerprint'])
            if fingerprint(row['workspace'],previous)!=previous:raise Conflict('Cambió la revisión de ejecución')
            db.execute("UPDATE operations SET state='in_flight' WHERE id=?",(op_id,))
            self._event(db,row,row['state'],'local_dispatched',{'operation_id':op_id,'role':op['role']})
            return dict(op)

    def finish_local(self,op_id,result,body=None,uncertain=False):
        # Resultados producidos por el adaptador, no por la afirmación del modelo.
        encoded=manifest.canonical(body) if body is not None else None
        if encoded is not None and len(encoded.encode('utf-8'))>128000:raise ValueError('Artefacto demasiado grande')
        for key in ('elapsed_ms','input_tokens','output_tokens'):
            if type(result.get(key)) is not int or result[key]<0:raise ValueError('Métrica inválida')
        with self.transaction() as db:
            op=db.execute('SELECT * FROM operations WHERE id=?',(op_id,)).fetchone()
            if not op or op['state']!='in_flight':raise Conflict('No hay llamada en curso')
            row=self._row(db,op['run_id'])
            if row['execution_kind']!='local' or row['state'] in TERMINAL:raise Conflict('Run no confirmable')
            budget=json.loads(row['manifest'])['budgets']
            totals={key:row[key]+result[metric] for key,metric in [('active_ms','elapsed_ms'),('input_tokens','input_tokens'),('output_tokens','output_tokens')]}
            exhausted=totals['active_ms']>=budget['active_seconds']*1000 or totals['input_tokens']>budget['input_tokens'] or totals['output_tokens']>budget['output_tokens']
            exceeded=result['input_tokens']>op['reserve_input'] or result['output_tokens']>op['reserve_output'] or result['elapsed_ms']>op['reserve_ms']
            ok=result.get('status') in {'complete','pass'} and not uncertain and not exceeded and not exhausted
            state='uncertain' if uncertain else 'confirmed' if ok else 'failed'
            db.execute('UPDATE operations SET state=?,result=? WHERE id=?',(state,manifest.canonical(result),op_id))
            db.execute('UPDATE runs SET active_ms=?,input_tokens=?,output_tokens=? WHERE id=?',
                (totals['active_ms'],totals['input_tokens'],totals['output_tokens'],row['id']))
            # Conservar también respuestas truncadas/fallos: nunca se convierten en aprobación.
            if encoded is not None:
                stored=body if ok else {'failed':True,'evidence':body}
                db.execute('INSERT INTO artifacts(run_id,name,body,sha256) VALUES(?,?,?,?)',
                    (row['id'],op['role'],manifest.canonical(stored),manifest.identity(stored)))
            target='uncertain_operation' if uncertain else 'blocked_budget' if exhausted or exceeded else row['state'] if ok else 'blocked_evidence'
            if row['state']=='paused':
                if target!='paused':db.execute('UPDATE runs SET resume_state=? WHERE id=?',(target,row['id']))
                target='paused'
            self._event(db,row,target,'local_result',{'operation_id':op_id,'role':op['role'],'status':state,
                'elapsed_ms':result['elapsed_ms'],'input_tokens':result['input_tokens'],'output_tokens':result['output_tokens'],
                'artifact_hash':manifest.identity(body) if body is not None else None})

    def block(self,run_id,reason):
        with self.transaction() as db:
            row=self._row(db,run_id)
            if row['state'] in TERMINAL:raise Conflict('Run cerrado')
            if row['state']=='paused':
                db.execute("UPDATE runs SET resume_state='blocked_evidence' WHERE id=?",(run_id,))
                target='paused'
            else:target='blocked_evidence'
            self._event(db,row,target,'validation_blocked',{'reason':reason})
