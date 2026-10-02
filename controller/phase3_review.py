"""Bounded local correction of file plans. Never materializes or executes code."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time

from jsonschema import Draft202012Validator
import controller_gate
import manifest
import phase3_prepare as preparation
from worker_lock import worker_lock

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT/'.state/phase3/review.sqlite'
LIMITS = {'calls':4, 'corrections':2, 'active_seconds':600,
          'input_tokens':131072, 'output_tokens':20000,
          'context_tokens':32768, 'timeout_seconds':150}
RULES = {
    'P01':'Return exactly the file-plan schema, accepted requirement ID, revision, contract hash and seven stage names.',
    'P02':'Use canonical allowed application paths, unique even on Windows; no protected files, traversal or dependency paths.',
    'P03':'Reference only accepted criterion IDs, without duplicates within a file, and cover all 36 criteria across the plan.',
    'P04':'Include package.json and package-lock.json at root, frontend and backend.',
    'P05':'Each file contains only path, purpose and criteria; purpose is bounded descriptive data, never code, commands or execution authority.'}
CODE_RULE = {'plan_shape':'P01','plan_identity':'P01','stage_denied':'P01',
             'plan_files':'P01','path_denied':'P02','duplicate_path':'P02',
             'criterion_reference':'P03','criteria_missing':'P03',
             'scaffold_missing':'P04','file_shape':'P05','purpose_shape':'P05'}


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def plan_schema(lock, documents):
    text = {'type':'string','minLength':1,'maxLength':1000}
    return obj({'schema':{'const':'agentapp.phase3-file-plan/1'},
        'requirement_id':{'const':lock['requirement_id']}, 'revision':{'const':lock['revision']},
        'contract_sha256':{'const':lock['files']['contract.json']},
        'files':{'type':'array','minItems':1,'maxItems':200,'items':obj({
            'path':{'type':'string','minLength':1,'maxLength':240}, 'purpose':text,
            'criteria':{'type':'array','uniqueItems':True,'items':{'enum':list(preparation.criteria(documents))}}})},
        'stages':{'const':list(preparation.STAGES)}})


def review_schema():
    text = {'type':'string','minLength':1,'maxLength':1000}
    check = obj({'passed':{'type':'boolean'}, 'pointer':{'type':'string','maxLength':300},
                 'quote':{'type':'string','maxLength':160}})
    finding = obj({'rule':{'enum':list(RULES)},'source':{'const':'planning-rules/1'},
                   'rule_quote':text,'pointer':{'type':'string','maxLength':300},'issue':text,'fix':text})
    return obj({'checks':obj({rule:check for rule in RULES}),
                'findings':{'type':'array','maxItems':10,'items':finding}})


def resolve_pointer(document, pointer):
    if pointer=='':return document
    if not pointer.startswith('/') or re.search(r'~(?![01])',pointer):raise ValueError('Bad evidence pointer')
    value=document
    for part in pointer[1:].split('/'):
        part=part.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not re.fullmatch(r'0|[1-9][0-9]*',part):raise ValueError('Bad list pointer')
            value=value[int(part)]
        elif isinstance(value,dict):value=value[part]
        else:raise ValueError('Bad evidence pointer')
    return value


def validate_review(review, candidate):
    Draft202012Validator(review_schema()).validate(review)
    for check in review['checks'].values():
        value=resolve_pointer(candidate,check['pointer'])
        content=value if isinstance(value,str) else manifest.canonical(value)
        if check['passed'] and not check['quote']:raise ValueError('Approval requires evidence')
        if check['quote'] and check['quote'] not in content:raise ValueError('Invented evidence quote')
    rejected={r for r,c in review['checks'].items() if not c['passed']}
    if rejected!={f['rule'] for f in review['findings']}:raise ValueError('Findings/checks disagree')
    for finding in review['findings']:
        if finding['rule_quote']!=RULES[finding['rule']]:raise ValueError('Unverified rule citation')
        # A rejection can point to a missing member, so only its format is required.
        if finding['pointer'] and not finding['pointer'].startswith('/'):raise ValueError('Bad finding pointer')
    return review


def defects(candidate, lock, documents):
    errors=sorted(Draft202012Validator(plan_schema(lock,documents)).iter_errors(candidate),
                  key=lambda e:str(list(e.absolute_path)))
    if errors:
        # Preserve every schema error within the bounded candidate, not a lossy prose summary.
        return [{'rule':'P01','source':'planning-rules/1','rule_quote':RULES['P01'],
                 'pointer':'/'+ '/'.join(str(p).replace('~','~0').replace('/','~1') for p in e.absolute_path),
                 'issue':e.validator,'fix':'Replace this member with the required typed plan value.'} for e in errors]
    try:preparation.validate_plan(candidate,lock,documents)
    except preparation.PreparationError as exc:
        finding=exc.finding
        rule=CODE_RULE[finding['code']]
        return [{'rule':rule,'source':'planning-rules/1','rule_quote':RULES[rule],
                 'pointer':finding['pointer'],'issue':finding['code'],'fix':finding['fix']}]
    return []


def contract_mode(row):return row['binding'].get('section') is not None


def output_schema(row,op,lock,documents):
    if row['binding'].get('section')=='application':
        import phase3_application as application
        return application.writer_schema() if op['role']=='developer' else application.review_schema()
    if contract_mode(row):
        import phase3_contract as contract
        section=row['binding']['section']
        return (contract.writer_schema(section,documents) if op['role']=='developer' else
                contract.review_schema(row['candidate'],section,documents))
    return plan_schema(lock,documents) if op['role']=='developer' else review_schema()


def candidate_defects(row,lock,documents):
    if row['binding'].get('section')=='application':
        import phase3_application as application
        return application.defects(row['candidate'])
    if contract_mode(row):
        import phase3_contract as contract
        return contract.defects(row['candidate'],row['binding']['section'],documents)
    return defects(row['candidate'],lock,documents)


def checked_review(row,value,documents):
    if row['binding'].get('section')=='application':
        import phase3_application as application
        return application.validate_review(value,row['candidate'])
    if contract_mode(row):
        import phase3_contract as contract
        return contract.validate_review(value,row['candidate'],row['binding']['section'],documents)
    return validate_review(value,row['candidate'])


def now():return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self,path=DATABASE):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS plan_runs(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS plan_ops(run_id TEXT,seq INTEGER,body TEXT NOT NULL,PRIMARY KEY(run_id,seq))')
            db.execute('CREATE TABLE IF NOT EXISTS plan_events(run_id TEXT,seq INTEGER,body TEXT NOT NULL,PRIMARY KEY(run_id,seq))')

    @contextmanager
    def transaction(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def get(self,run_id):
        with self.transaction() as db:
            record=db.execute('SELECT body FROM plan_runs WHERE id=?',(run_id,)).fetchone()
        if record is None:raise ValueError('Unknown plan run')
        return manifest.parse(record[0])

    def records(self,run_id,table):
        if table not in {'plan_ops','plan_events'}:raise ValueError('Unknown evidence table')
        with self.transaction() as db:
            return [manifest.parse(r[0]) for r in db.execute(f'SELECT body FROM {table} WHERE run_id=? ORDER BY seq',(run_id,))]

    def save(self,db,row,event):
        row['updated_at']=now()
        db.execute('UPDATE plan_runs SET body=? WHERE id=?',(manifest.canonical(row),row['id']))
        seq=db.execute('SELECT COUNT(*) FROM plan_events WHERE run_id=?',(row['id'],)).fetchone()[0]
        body={'seq':seq,'at':row['updated_at'],'state':row['state'],'calls':row['calls'],
              'corrections':row['corrections'],**event}
        db.execute('INSERT INTO plan_events VALUES(?,?,?)',(row['id'],seq,manifest.canonical(body)))

    def change(self,run_id,state,reason):
        with self.transaction() as db:
            row=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(run_id,)).fetchone()[0])
            row.update(state=state,reason=reason)
            self.save(db,row,{'kind':'state.changed','reason':reason})
        return row

    def create(self,run_id,candidate,bundle=preparation.FIXTURE,section=None,expected_checks=None):
        if not re.fullmatch('[a-z0-9-]{1,100}',run_id):raise ValueError('Bad run ID')
        if len(manifest.canonical(candidate).encode())>24000:raise ValueError('Candidate too large')
        suite=controller_gate.require_green()
        lock,documents,_=preparation.load_bundle(bundle)
        # Generated desktop workspace paths are long on Windows. Keep run identity
        # in the journal and use a short deterministic directory component.
        folder=ROOT/'.state/p3r'/manifest.identity({'run_id':run_id})[:16]
        # Candidate may be defective; snapshot the trusted bundle using its valid default plan.
        snapshot=preparation.snapshot(bundle,folder)
        limits,rules=LIMITS,RULES
        if section is not None:
            if section=='application':
                import phase3_application as application
                limits,rules=application.LIMITS,application.RULES
            else:
                import phase3_contract as contract
                if section not in contract.SECTIONS:raise ValueError('Unsupported contract section')
                limits,rules=contract.LIMITS,contract.rules(section)
        cfg={'suite_identity':suite,'input_identity':snapshot['input_identity'],
             'model':documents['policy.json']['local_model'],
             'digest':documents['policy.json']['local_model_digest'],'limits':limits,
             'rules':rules,'seed_sha256':manifest.identity(candidate)}
        if section is not None:cfg['section']=section
        if expected_checks is not None:
            if section is None or not isinstance(expected_checks,dict) or set(expected_checks)!=set(rules) or any(type(v) is not bool for v in expected_checks.values()):
                raise ValueError('Fixture expectations must match every section rule')
            cfg['expected_checks']=expected_checks
        row={'id':run_id,'state':'active','reason':'','role':'reviewer','candidate':candidate,
             'candidate_sha256':manifest.identity(candidate),'findings':[], 'calls':0,'corrections':0,
             'input_tokens':0,'output_tokens':0,'active_ms':0,'created_at':now(),'updated_at':now(),
             'binding':cfg,'binding_sha256':manifest.identity(cfg),
             'snapshot_directory':snapshot['snapshot_directory'], 'execution_authorized':False,
             'product_tests_executed':False,'scope':'contract_fixture_evaluation' if expected_checks is not None else
             'application_file_review' if section=='application' else 'contract_section_document_review' if section else 'file_plan_structural_review'}
        with self.transaction() as db:
            db.execute('INSERT INTO plan_runs VALUES(?,?)',(run_id,manifest.canonical(row)))
            self.save(db,row,{'kind':'run.created','seed_sha256':cfg['seed_sha256']})
        return row

    def reserve(self,run_id):
        with self.transaction() as db:
            row=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(run_id,)).fetchone()[0])
            if row['state']!='active':raise ValueError('Run is stopped')
            limits=row['binding']['limits']
            tokens=5000 if row['role']=='developer' else 2000
            if (row['calls']>=limits['calls'] or row['input_tokens']+limits['context_tokens']>limits['input_tokens']
                or row['output_tokens']+tokens>limits['output_tokens'] or row['active_ms']>=1000*limits['active_seconds']
                or (row['role']=='developer' and row['corrections']>=limits['corrections'])):
                row.update(state='blocked_budget',reason='Conservative call/round/time/token limit')
                self.save(db,row,{'kind':'budget.blocked'});return None
            seq=row['calls'];row['calls']+=1
            if row['role']=='developer':row['corrections']+=1
            timeout=min(limits['timeout_seconds'],max(1,(1000*limits['active_seconds']-row['active_ms'])//1000))
            op={'seq':seq,'role':row['role'],'state':'in_flight','candidate':row['candidate'],
                'candidate_sha256':row['candidate_sha256'],'started_at':now(),
                'reserve_input':limits['context_tokens'],'reserve_output':tokens,'timeout_seconds':timeout}
            row['input_tokens']+=op['reserve_input'];row['output_tokens']+=tokens
            db.execute('INSERT INTO plan_ops VALUES(?,?,?)',(run_id,seq,manifest.canonical(op)))
            self.save(db,row,{'kind':'agent.started','agent':op['role']})
        return op

    def record_request(self,run_id,seq,payload):
        with self.transaction() as db:
            op=manifest.parse(db.execute('SELECT body FROM plan_ops WHERE run_id=? AND seq=?',(run_id,seq)).fetchone()[0])
            if op['state']!='in_flight' or 'request' in op:raise ValueError('Request already recorded')
            op['request']=payload
            op['request_sha256']=manifest.identity(payload)
            db.execute('UPDATE plan_ops SET body=? WHERE run_id=? AND seq=?',(manifest.canonical(op),run_id,seq))

    def finish(self,run_id,seq,result,elapsed_ms,lock,documents):
        with self.transaction() as db:
            row=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(run_id,)).fetchone()[0])
            op=manifest.parse(db.execute('SELECT body FROM plan_ops WHERE run_id=? AND seq=?',(run_id,seq)).fetchone()[0])
            if op['state']!='in_flight' or row['state']!='active':raise ValueError('Operation already stopped or finished')
            op.update(result=result,elapsed_ms=elapsed_ms,state='confirmed')
            row['active_ms']+=max(0,elapsed_ms)
            if not result.get('ok'):
                row.update(state='blocked_evidence' if result.get('error_type')=='drift_after_dispatch' else
                           'uncertain_operation' if result.get('sent',True) else 'blocked_evidence',
                           reason='Transport did not return a complete verified result')
            else:
                counts=all(type(result.get(k)) is int and 0<=result[k]<=op['reserve_'+k.split('_')[0]]
                           for k in ('input_tokens','output_tokens'))
                verified=(counts and result.get('model_digest')==row['binding']['digest']
                          and result.get('done') is True and result.get('done_reason')=='stop'
                          and not result.get('tool_calls') and isinstance(result.get('text'),str)
                          and len(result['text'].encode())<=24000)
                if counts:
                    row['input_tokens']+=result['input_tokens']-op['reserve_input']
                    row['output_tokens']+=result['output_tokens']-op['reserve_output']
                if not verified:
                    row.update(state='blocked_evidence',reason='Truncated result, unverifiable usage or model identity')
                elif row['active_ms']>1000*row['binding']['limits']['active_seconds']:
                    row.update(state='blocked_budget',reason='Active time exceeded')
                else:
                    try:
                        parsed=manifest.parse(result['text'])
                        if op['role']=='reviewer':
                            checked=checked_review(row,parsed,documents)
                            op['validated_review']=checked
                            deterministic=candidate_defects(row,lock,documents)
                            row['findings']=deterministic+checked['findings']
                            if 'expected_checks' in row['binding']:
                                actual={k:c['passed'] for k,c in checked['checks'].items()}
                                row.update(state='evaluated_fixture',fixture_passed=actual==row['binding']['expected_checks'],
                                           reason='Fixture evaluation only; not contract or product acceptance')
                            elif row['findings']:row['role']='developer'
                            else:row.update(state='reviewed_application_file' if row['binding'].get('section')=='application' else 'reviewed_contract_section' if contract_mode(row) else 'reviewed_plan',
                                            reason='Scoped source file accepted; sandbox tests pending' if row['binding'].get('section')=='application' else 'Documentary section accepted; no product execution' if contract_mode(row) else 'Structural plan accepted; no product execution')
                        else:
                            Draft202012Validator(output_schema(row,op,lock,documents)).validate(parsed)
                            row.update(candidate=parsed,candidate_sha256=manifest.identity(parsed),role='reviewer')
                            row['findings']=candidate_defects(row,lock,documents)
                    except (ValueError,KeyError,IndexError,TypeError) as exc:
                        row.update(state='blocked_evidence',reason='Invalid structured result or evidence citation: '+type(exc).__name__)
                    except Exception as exc:
                        # jsonschema ValidationError also preserves the original output.
                        row.update(state='blocked_evidence',reason='Invalid output schema: '+type(exc).__name__)
            db.execute('UPDATE plan_ops SET body=? WHERE run_id=? AND seq=?',(manifest.canonical(op),run_id,seq))
            self.save(db,row,{'kind':'agent.finished','agent':op['role'],'elapsed_ms':elapsed_ms,
                             'input_tokens':result.get('input_tokens'),'output_tokens':result.get('output_tokens'),
                             'candidate_sha256':row['candidate_sha256'],'reason':row['reason']})
        return row


def verify_binding(row):
    if manifest.identity(row['binding'])!=row['binding_sha256']:raise ValueError('Binding changed')
    limits,rules=LIMITS,RULES
    if contract_mode(row):
        if row['binding']['section']=='application':
            import phase3_application as application
            limits,rules=application.LIMITS,application.RULES
        else:
            import phase3_contract as contract
            limits,rules=contract.LIMITS,contract.rules(row['binding']['section'])
    if row['binding']['limits']!=limits or row['binding']['rules']!=rules:raise ValueError('Policy drift')
    if controller_gate.require_green()!=row['binding']['suite_identity']:raise ValueError('Controller drift')
    lock,documents,_=preparation.load_bundle(Path(row['snapshot_directory']))
    if manifest.identity(lock)!=row['binding']['input_identity']:raise ValueError('Input identity drift')
    if (row['binding']['model']!=documents['policy.json']['local_model']
        or row['binding']['digest']!=documents['policy.json']['local_model_digest']):raise ValueError('Model binding drift')
    if manifest.identity(row['candidate'])!=row['candidate_sha256']:raise ValueError('Candidate drift')
    return lock,documents


def observe(journal,row,observer):
    if observer:
        try:observer(journal,row)
        except Exception as exc:
            # Projection failure cannot change acceptance or interrupt dispatch.
            # Preserve it locally so Studio can be reconciled from the journal.
            with journal.transaction() as db:
                current=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(row['id'],)).fetchone()[0])
                journal.save(db,current,{'kind':'observer.failed','error_type':type(exc).__name__})


def run(journal,run_id,caller=None,observer=None):
    with worker_lock(journal.path,run_id) as owned:
        if not owned:return journal.get(run_id)|{'busy':True}
        while True:
            row=journal.get(run_id)
            if row['state']!='active':return row
            if any(op['state']=='in_flight' for op in journal.records(run_id,'plan_ops')):
                row=journal.change(run_id,'uncertain_operation','Interrupted in-flight inference; never resend automatically')
                observe(journal,row,observer)
                return row
            try:lock,documents=verify_binding(row)
            except (ValueError,OSError):
                row=journal.change(run_id,'blocked_evidence','Source, input, policy, runtime or candidate drift')
                observe(journal,row,observer)
                return row
            op=journal.reserve(run_id)
            if not op:
                row=journal.get(run_id)
                observe(journal,row,observer)
                return row
            observe(journal,journal.get(run_id),observer)
            started=time.monotonic()
            try:
                op['_record_request']=lambda payload:journal.record_request(run_id,op['seq'],payload)
                if caller is None:
                    from phase3_roles import call_role
                    caller=call_role
                result=caller(journal.get(run_id),op,lock,documents)
            except Exception:
                result={'ok':False,'sent':True,'error_type':'unverified_transport'}
            # Recheck after dispatch. A changed controller/input invalidates acceptance.
            try:verify_binding(journal.get(run_id))
            except (ValueError,OSError):result={**result,'ok':False,'sent':True,'error_type':'drift_after_dispatch'}
            row=journal.finish(run_id,op['seq'],result,round((time.monotonic()-started)*1000),lock,documents)
            observe(journal,row,observer)


def export(journal,run_id,destination):
    row=journal.get(run_id)
    result={'run':row,'operations':journal.records(run_id,'plan_ops'),
            'events':journal.records(run_id,'plan_events')}
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return path
