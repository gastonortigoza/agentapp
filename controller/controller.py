"""Controlador local: SQLite transaccional y efectos exclusivamente simulados.

La API es código de confianza, no una herramienta disponible para modelos.
No ejecuta comandos del manifiesto ni llama adaptadores externos.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

import manifest as contracts
import controller_gate

TERMINAL = {'delivered', 'rejected'}
EDGES = {
    'received': {'contract_pending'}, 'contract_pending': {'planning'},
    'planning': {'implementing'}, 'implementing': {'validating'},
    'validating': {'awaiting_review','blocked_evidence'},
    'awaiting_review': {'delivered','rejected','awaiting_human_approval'},
    'awaiting_human_approval': {'awaiting_review','rejected'},
    'blocked_evidence': {'planning','rejected'}, 'blocked_budget': {'rejected'},
    'uncertain_operation': {'planning','rejected'},
}


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(root, files):
    result = {}
    root = Path(root)
    source_names = sorted(str(p.relative_to(root)).replace('\\','/') for p in
        {*root.glob('*.py'), *root.glob('*.cmd'), *(root/'tests').glob('*.py'), *(root/'schemas').glob('*.json'), *(root/'pilot').rglob('*.py')})
    for name in sorted((set(files) | set(source_names)) - {'_source_inventory'}):
        path = contracts.relative_path(name, root)
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            result[name] = None
    # Detectar archivos fuente nuevos además de cambios a los existentes.
    result['_source_inventory'] = contracts.identity(source_names)
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            schema_sql='''
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, resume_state TEXT,
                  active_ms INTEGER NOT NULL DEFAULT 0,
                  rounds INTEGER NOT NULL DEFAULT 0,
                  input_tokens INTEGER NOT NULL DEFAULT 0,
                  output_tokens INTEGER NOT NULL DEFAULT 0,
                  parent_id TEXT REFERENCES runs(id), workspace TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='stub'));
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                  version INTEGER NOT NULL, old_state TEXT, new_state TEXT NOT NULL,
                  actor TEXT NOT NULL, timestamp TEXT NOT NULL, policy_revision TEXT NOT NULL,
                  kind TEXT NOT NULL, refs TEXT NOT NULL, UNIQUE(run_id,version));
                CREATE TABLE IF NOT EXISTS operations (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                  logical_key TEXT NOT NULL, state TEXT NOT NULL,
                  manifest_hash TEXT NOT NULL, fingerprint_hash TEXT NOT NULL,
                  result TEXT, UNIQUE(run_id,logical_key));
                CREATE TABLE IF NOT EXISTS meta (version INTEGER NOT NULL);
                INSERT INTO meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM meta);
            '''
            for statement in schema_sql.split(';'):
                if statement.strip():db.execute(statement)
            version=db.execute('SELECT version FROM meta').fetchone()[0]
            if version==1:
                db.execute("ALTER TABLE runs ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'stub' CHECK(execution_kind IN ('stub','local'))")
                for name,definition in [('kind',"TEXT NOT NULL DEFAULT 'stub'"),('role',"TEXT NOT NULL DEFAULT ''"),('reserve_input','INTEGER NOT NULL DEFAULT 0'),('reserve_output','INTEGER NOT NULL DEFAULT 0'),('reserve_ms','INTEGER NOT NULL DEFAULT 0')]:
                    db.execute(f'ALTER TABLE operations ADD COLUMN {name} {definition}')
                db.execute('CREATE TABLE artifacts (run_id TEXT NOT NULL REFERENCES runs(id), name TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(run_id,name))')
                db.execute('UPDATE meta SET version=2')
            elif version != 2:
                raise ValueError('Versión de base no soportada')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _row(self, db, run_id, expected=None):
        row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Ejecución inexistente')
        if expected is not None and row['version'] != expected:
            raise Conflict('Versión obsoleta')
        return row

    def _event(self, db, row, new_state, kind, refs=None, actor='controller'):
        version = row['version'] + 1
        stamp = now()
        policy = json.loads(row['manifest'])['approval_policy']['revision']
        db.execute('UPDATE runs SET state=?,version=?,updated_at=? WHERE id=?',
                   (new_state,version,stamp,row['id']))
        db.execute('INSERT INTO events(run_id,version,old_state,new_state,actor,timestamp,policy_revision,kind,refs) VALUES(?,?,?,?,?,?,?,?,?)',
                   (row['id'],version,row['state'],new_state,actor,stamp,policy,kind,contracts.canonical(refs or {})))

    def create(self, contract, workspace, files, parent_id=None):
        suite_identity=controller_gate.require_green()
        contract = contracts.validate(contract, workspace)
        contracts.verify_base(contract, workspace)
        kind='local' if contract['schema_version'] in {'1.1','1.2','1.3'} else 'stub'
        if kind=='stub' and contract['approval_policy']['actions'] != ['local_stub']:
            raise ValueError('Sólo está habilitada simulación local')
        # Registrar el contrato no habilita ejecución real ni afirma enforcement.
        run_id = str(uuid.uuid4())
        stamp = now()
        snapshot = fingerprint(workspace, files)
        with self.transaction() as db:
            if parent_id and self._row(db, parent_id)['state'] not in TERMINAL:
                raise ValueError('El padre debe estar cerrado')
            db.execute('INSERT INTO runs(id,state,version,created_at,updated_at,manifest,manifest_hash,fingerprint,parent_id,workspace,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id,'received',0,stamp,stamp,contracts.canonical(contract),contracts.identity(contract),contracts.canonical(snapshot),parent_id,str(Path(workspace).resolve()),'stub'))
            db.execute('UPDATE runs SET execution_kind=? WHERE id=?',(kind,run_id))
            row = self._row(db, run_id)
            self._event(db,row,'received','created',{'mode':kind,'parent_id':parent_id,'suite_identity':suite_identity})
        return self.get(run_id)

    def get(self, run_id):
        with self.transaction() as db:
            return dict(self._row(db,run_id))

    def transition(self, run_id, expected, target):
        if target != 'rejected':
            controller_gate.require_green()
        with self.transaction() as db:
            row = self._row(db,run_id,expected)
            if target not in EDGES.get(row['state'],set()):
                raise Conflict('Transición no permitida')
            if db.execute("SELECT 1 FROM operations WHERE run_id=? AND state IN ('in_flight','uncertain')",(run_id,)).fetchone():
                raise Conflict('Operación pendiente de reconciliación')
            if target in {'validating','awaiting_review','delivered'}:
                previous = json.loads(row['fingerprint'])
                if fingerprint(row['workspace'],previous) != previous:
                    raise Conflict('Artefacto modificado; pausar y revalidar')
                if row['execution_kind']=='local':
                    from local_control import require_evidence
                    require_evidence(db,row,target)
                else:
                    operation = db.execute("SELECT * FROM operations WHERE run_id=? AND state='confirmed' AND manifest_hash=? AND fingerprint_hash=?", 
                    (run_id,row['manifest_hash'],contracts.identity(json.loads(row['fingerprint'])))).fetchone()
                    if not operation or json.loads(operation['result']).get('status') != 'pass':
                        raise ValueError('Falta evidencia durable de simulación')
            if target == 'implementing':
                if row['rounds'] >= json.loads(row['manifest'])['budgets']['rounds']:
                    self._event(db,row,'blocked_budget','rounds_exhausted')
                    return dict(self._row(db,run_id))
                db.execute('UPDATE runs SET rounds=rounds+1 WHERE id=?',(run_id,))
            self._event(db,row,target,'transition')
        return self.get(run_id)

    def pause(self, run_id, expected):
        with self.transaction() as db:
            row = self._row(db,run_id,expected)
            if row['state'] in TERMINAL or row['state'] == 'paused':
                raise Conflict('No se puede pausar')
            db.execute('UPDATE runs SET resume_state=? WHERE id=?',(row['state'],run_id))
            self._event(db,row,'paused','paused',actor='operator')
        return self.get(run_id)

    def resume(self, run_id, expected, workspace):
        controller_gate.require_green()
        with self.transaction() as db:
            row = self._row(db,run_id,expected)
            if row['state'] != 'paused':
                raise Conflict('La ejecución no está pausada')
            if db.execute("SELECT 1 FROM operations WHERE run_id=? AND state IN ('in_flight','uncertain')",(run_id,)).fetchone():
                raise Conflict('Operación pendiente de reconciliación')
            contract = json.loads(row['manifest'])
            if str(Path(workspace).resolve()) != row['workspace']:
                raise ValueError('Workspace diferente al registrado')
            contracts.verify_base(contract,workspace)
            contracts.validate(contract,workspace)
            previous = json.loads(row['fingerprint'])
            current = fingerprint(workspace,previous)
            changed = [name for name in previous if previous[name] != current[name]]
            target = row['resume_state']
            if changed:
                # El contrato congelado conserva permisos; jamás se adopta política nueva.
                target = 'planning'
                db.execute('UPDATE runs SET fingerprint=? WHERE id=?',(contracts.canonical(current),run_id))
                db.execute("UPDATE operations SET state='failed' WHERE run_id=? AND state='prepared'",(run_id,))
            db.execute('UPDATE runs SET resume_state=NULL WHERE id=?',(run_id,))
            self._event(db,row,target,'drift_detected' if changed else 'resumed',{'changed_paths':changed},actor='operator')
        return self.get(run_id)

    def prepare(self, run_id, expected, logical_key):
        controller_gate.require_green()
        if not isinstance(logical_key,str) or not 1 <= len(logical_key) <= 100:
            raise ValueError('Identidad lógica inválida')
        with self.transaction() as db:
            row = self._row(db,run_id,expected)
            if row['execution_kind']!='stub':raise Conflict('Usar adaptador local autorizado')
            if row['state'] != 'implementing':
                raise Conflict('No se autoriza una operación en este estado')
            op_id = str(uuid.uuid4())
            db.execute('INSERT INTO operations(id,run_id,logical_key,state,manifest_hash,fingerprint_hash) VALUES(?,?,?,?,?,?)',
                       (op_id,run_id,logical_key,'prepared',row['manifest_hash'],contracts.identity(json.loads(row['fingerprint']))))
            self._event(db,row,row['state'],'operation_prepared',{'operation_id':op_id})
        return op_id

    def claim(self, op_id):
        controller_gate.require_green()
        with self.transaction() as db:
            op = db.execute('SELECT * FROM operations WHERE id=?',(op_id,)).fetchone()
            if op is None or op['state'] != 'prepared':
                raise Conflict('Operación ya despachada o inexistente')
            row = self._row(db,op['run_id'])
            if row['execution_kind']!='stub':raise Conflict('Operación no simulada')
            if row['state'] != 'implementing':
                raise Conflict('Pausa/bloqueo impide despacho')
            previous = json.loads(row['fingerprint'])
            if fingerprint(row['workspace'],previous) != previous:
                raise Conflict('Workspace modificado; pausar y revalidar')
            budget = json.loads(row['manifest'])['budgets']
            if row['active_ms'] >= budget['active_seconds']*1000:
                raise Conflict('Presupuesto agotado')
            db.execute("UPDATE operations SET state='in_flight' WHERE id=?",(op_id,))
            self._event(db,row,row['state'],'operation_dispatched',{'operation_id':op_id})
        return dict(op)

    def confirm_stub(self, op_id, elapsed_ms=0):
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            raise ValueError('Duración inválida')
        with self.transaction() as db:
            op = db.execute('SELECT * FROM operations WHERE id=?',(op_id,)).fetchone()
            if op is None or op['state'] not in {'in_flight','uncertain'}:
                raise Conflict('Operación no reconciliable')
            row = self._row(db,op['run_id'])
            if row['execution_kind']!='stub':raise Conflict('Una simulación no certifica trabajo real')
            if row['state'] in TERMINAL:
                raise Conflict('Run cerrado')
            result = {'status':'pass','scope':'stub_only','operation_id':op_id}
            db.execute("UPDATE operations SET state='confirmed',result=? WHERE id=?",(contracts.canonical(result),op_id))
            active = row['active_ms'] + elapsed_ms
            db.execute('UPDATE runs SET active_ms=? WHERE id=?',(active,row['id']))
            exhausted = active >= json.loads(row['manifest'])['budgets']['active_seconds']*1000
            target = 'blocked_budget' if exhausted and row['state'] != 'paused' else row['state']
            if exhausted and row['state'] == 'paused':
                db.execute("UPDATE runs SET resume_state='blocked_budget' WHERE id=?",(row['id'],))
            self._event(db,row,target,'operation_confirmed',{'operation_id':op_id,'scope':'stub_only','elapsed_ms':elapsed_ms})

    def recover(self, run_id):
        """Tras parada del worker: marcar pendientes; nunca volver a prepared."""
        with self.transaction() as db:
            row = self._row(db,run_id)
            if row['state'] in TERMINAL:
                raise Conflict('Run cerrado')
            pending = db.execute("SELECT id FROM operations WHERE run_id=? AND state='in_flight'",(run_id,)).fetchall()
            if pending:
                db.execute("UPDATE operations SET state='uncertain' WHERE run_id=? AND state='in_flight'",(run_id,))
                target = 'paused' if row['state'] == 'paused' else 'uncertain_operation'
                self._event(db,row,target,'recovery_required',{'operations':[p['id'] for p in pending]})

    def events(self, run_id):
        with self.transaction() as db:
            self._row(db,run_id)
            return [dict(r) for r in db.execute('SELECT * FROM events WHERE run_id=? ORDER BY seq',(run_id,))]

    def metrics(self, run_id):
        with self.transaction() as db:
            row = self._row(db,run_id)
            events = [dict(r) for r in db.execute('SELECT * FROM events WHERE run_id=? ORDER BY seq',(run_id,))]
            end = row['updated_at'] if row['state'] in TERMINAL else now()
            durations = {}
            for index,event in enumerate(events):
                next_stamp = events[index+1]['timestamp'] if index+1<len(events) else end
                milliseconds = max(0,round((datetime.fromisoformat(next_stamp)-datetime.fromisoformat(event['timestamp'])).total_seconds()*1000))
                durations[event['new_state']] = durations.get(event['new_state'],0)+milliseconds
            roles={}
            if row['execution_kind']=='local':
                for op in db.execute("SELECT role,result FROM operations WHERE run_id=? AND kind='model'",(run_id,)):
                    result=json.loads(op['result']) if op['result'] else {}
                    roles[op['role']]={k:result.get(k) for k in ('status','elapsed_ms','input_tokens','output_tokens','model_digest','usage_complete','load_duration_ns','total_duration_ns')}
            return {'run_id':run_id,'mode':row['execution_kind'],'result':row['state'],
                'total_wall_ms':max(0,round((datetime.fromisoformat(end)-datetime.fromisoformat(row['created_at'])).total_seconds()*1000)),
                'state_duration_ms':durations,'active_operation_ms':row['active_ms'],
                'rounds':row['rounds'],'input_tokens':row['input_tokens'],'output_tokens':row['output_tokens'],
                'role_metrics':roles,'limitation':'Una ejecución no constituye un benchmark; tiempo de operaciones, no CPU del controlador'}


def read_runs(path, run_id=None):
    """Consultas sin crear base, tomar lock de escritura ni iniciar CrewAI."""
    path = Path(path)
    if not path.exists():
        return []
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        if run_id:
            return [dict(r) for r in db.execute('SELECT seq,run_id,old_state,new_state,actor,timestamp,kind,refs FROM events WHERE run_id=? ORDER BY seq',(run_id,))]
        rows = []
        for row in db.execute('SELECT * FROM runs ORDER BY created_at DESC'):
            budget = json.loads(row['manifest'])['budgets']
            last = db.execute('SELECT kind,timestamp FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1',(row['id'],)).fetchone()
            reserved={'input_tokens':0,'output_tokens':0,'active_ms':0}
            if 'execution_kind' in row.keys() and row['execution_kind']=='local':
                pending=db.execute("SELECT COALESCE(SUM(reserve_input),0),COALESCE(SUM(reserve_output),0),COALESCE(SUM(reserve_ms),0) FROM operations WHERE run_id=? AND state IN ('prepared','in_flight','uncertain')",(row['id'],)).fetchone()
                reserved=dict(zip(('input_tokens','output_tokens','active_ms'),pending))
            rows.append({'run_id':row['id'],'recorded_status':row['state'],'version':row['version'],
                'mode':row['execution_kind'] if 'execution_kind' in row.keys() else 'stub','activity':'controller_state_only',
                'created_at':row['created_at'],'updated_at':row['updated_at'],
                'elapsed_ms':max(0,round((datetime.fromisoformat(row['updated_at'] if row['state'] in TERMINAL else now())-datetime.fromisoformat(row['created_at'])).total_seconds()*1000)),
                'last_event':dict(last) if last else None,
                'pending_reservations':reserved,
                'active_ms':row['active_ms'],'remaining_budget':{
                    'rounds':max(0,budget['rounds']-row['rounds']),
                    'active_ms':max(0,budget['active_seconds']*1000-row['active_ms']-reserved['active_ms']),
                    'input_tokens':max(0,budget['input_tokens']-row['input_tokens']-reserved['input_tokens']),
                    'output_tokens':max(0,budget['output_tokens']-row['output_tokens']-reserved['output_tokens'])},
                'next_action': {'blocked_budget':'Revisar presupuesto; no se amplía automáticamente',
                    'uncertain_operation':'Reconciliar operación antes de repetir',
                    'paused':'Reanudar para comparar huella del workspace',
                    'blocked_evidence':'Corregir evidencia y revalidar'}.get(row['state'])})
        return rows
    finally:
        db.close()
