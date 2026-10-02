"""Durable cross-task supervision of the fixed local FE/API/PostgreSQL slice.

One start chains plan/contract/source review, materialization, sandbox checks and
bounded product corrections. The operator handles terminal disputes, not each
transition. This policy does not implement the remaining SaaS flows or authorize
remote writes. Frozen tests, dependencies and the disabled contract are preserved.
"""
import copy
import hashlib
from pathlib import Path
import re
import time

import controller_gate
import executor
import manifest
import phase3_contract as contract
import phase3_execution as execution
import phase3_handoff as handoff
import phase3_prepare as preparation
import phase3_review as review
import phase3_sandbox as sandbox
import phase3_source as source
from worker_lock import worker_lock

KINDS=('plan',*contract.SECTIONS,*source.SECTIONS)
LIMITS={'calls':60,'input_tokens':1966080,'output_tokens':300000,'active_ms':7200000,'executions':3}
SUCCESS={'reviewed_plan','reviewed_contract_section','reviewed_application_file'}

def get(journal,run_id):
    with journal.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS phase3_workflows(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        value=db.execute('SELECT body FROM phase3_workflows WHERE id=?',(run_id,)).fetchone()
    if not value:raise ValueError('Unknown workflow')
    return manifest.parse(value[0])

def save(journal,row,event=None):
    row['updated_at']=review.now()
    if event:row['events'].append({'seq':len(row['events']),'at':row['updated_at'],**event})
    with journal.transaction() as db:
        db.execute('UPDATE phase3_workflows SET body=? WHERE id=?',(manifest.canonical(row),row['id']))

def create(journal,run_id,workspace,plan,cache,parent_id=None,review_every=0,input_amendment=None):
    if not isinstance(run_id,str) or not re.fullmatch(r'[a-z0-9-]{1,55}',run_id):raise ValueError('Bad workflow ID')
    if type(review_every) is not int or not 0<=review_every<=100:raise ValueError('Bad periodic review interval')
    suite=controller_gate.require_green();lock,docs,_=preparation.load_bundle()
    preparation.validate_plan(plan,lock,docs)
    fingerprint,files=sandbox.capture(workspace,plan)
    if not set(source.PATHS.values())<=set(files):raise ValueError('All source modules must be planned')
    for path in source.PATHS.values():files[path].decode('utf-8')
    cache_data,receipt=execution.cache_archive(files,cache)
    parent=get(journal,parent_id) if parent_id else None
    if input_amendment is not None and not parent:raise ValueError('Input amendment requires a terminal parent')
    if parent:
        verify_accounting(parent)
        verify_audits(parent)
        if parent['state'] not in ('awaiting_discrepancy','needs_external_help','blocked_drift') or any(op['state']!='confirmed' for op in parent['operations']):raise ValueError('Uncertain/active/budget-limited parent cannot be restarted')
        if parent['binding']['limits']!=LIMITS or parent['binding']['plan']!=plan:raise ValueError('Continuation must preserve parent budget and plan')
        if input_amendment is None:
            if parent['binding']['workspace']!=fingerprint:raise ValueError('Continuation must preserve original inputs')
        else:validate_amendment(parent,fingerprint,input_amendment)
    binding={'suite_identity':suite,'input_identity':manifest.identity(lock),'policy_sha256':manifest.identity(sandbox.POLICY),
        'workspace':fingerprint,'plan':plan,'cache':str(Path(cache).resolve()),
        'cache_sha256':hashlib.sha256(cache_data).hexdigest(),'receipt_sha256':manifest.identity(receipt),'limits':LIMITS}
    binding['review_every']=review_every
    binding['audit_start']=len(parent['operations']) if parent else 0
    if input_amendment is not None:binding['operator_input_amendment']=copy.deepcopy(input_amendment)
    if parent:binding['parent']={'id':parent_id,'sha256':manifest.identity(parent)}
    # Freeze all inputs including tests; only the three registered source modules
    # can be replaced by model output. No test, lock or controller patch is accepted.
    frozen=review.ROOT/'.state/phase3/workflows'/manifest.identity({'workflow':run_id})[:16]
    frozen.mkdir(parents=True,exist_ok=True)
    for path,data in files.items():
        dst=manifest.relative_path(path,frozen);dst.parent.mkdir(parents=True,exist_ok=True)
        try:
            with dst.open('xb') as output:output.write(data)
        except FileExistsError:
            if dst.read_bytes()!=data:raise ValueError('Frozen workflow input conflict')
    row={'schema':'agentapp.phase3-workflow/1','id':run_id,'state':'active','reason':'',
        'binding':binding,'binding_sha256':manifest.identity(binding),'frozen':str(frozen),
        'created_at':review.now(),'updated_at':review.now(),'review_index':0,'round':0,'children':{},
        'operations':[],'events':[],'used':{k:0 for k in LIMITS},'product_feedback':None,
        'product_tests_executed':False,'full_product_acceptance':False,'scope':'local_directory_and_public_profile',
        'recoveries':{},'recovery_feedback':{},'overrides':{},'audits':[],
        'audit_start':len(parent['operations']) if parent else 0,'last_audited_round':0}
    if parent:
        row['used']=copy.deepcopy(parent['used']);row['operations']=copy.deepcopy(parent['operations'])
        prior_id=parent.get('attention',{}).get('child_id')
        if prior_id:
            recovered=recovery_evidence(journal,prior_id)
            if recovered:
                previous=journal.get(prior_id);kind=previous['binding'].get('section') or 'plan'
                row['recoveries'][kind]=1;row['recovery_feedback'][kind]=recovered;row['overrides'][kind]=previous['candidate']
    with journal.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS phase3_workflows(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS phase3_workflow_continuations(parent_id TEXT PRIMARY KEY,child_id TEXT UNIQUE NOT NULL)')
        if parent:db.execute('INSERT INTO phase3_workflow_continuations VALUES(?,?)',(parent_id,run_id))
        db.execute('INSERT INTO phase3_workflows VALUES(?,?)',(run_id,manifest.canonical(row)))
    save(journal,row,{'kind':'workflow.created'})
    return row


def validate_amendment(parent,fingerprint,value):
    """Explicit operator repair of a fixed test/fixture; keep all prior spend."""
    if (not isinstance(value,dict) or set(value)!={'reason','paths','parent_fingerprint_sha256'}
        or not isinstance(value['reason'],str) or not 1<=len(value['reason'])<=1000
        or not isinstance(value['paths'],list) or not 1<=len(value['paths'])<=10
        or any(not isinstance(p,str) for p in value['paths']) or len(set(value['paths']))!=len(value['paths'])
        or value['parent_fingerprint_sha256']!=manifest.identity(parent['binding']['workspace'])):
        raise ValueError('Invalid operator input amendment')
    old=parent['binding']['workspace']['files'];new=fingerprint['files']
    changed={p for p in old if old[p]!=new.get(p)}
    if set(old)!=set(new) or changed!=set(value['paths']):raise ValueError('Amendment must declare exactly all changed existing inputs')
    for path in changed:
        preparation.plan_path(path)
        if (path in source.PATHS.values() or not (re.fullmatch(r'frontend/e2e/[A-Za-z0-9_/-]+\.spec\.ts',path)
            or re.fullmatch(r'backend/tests/[A-Za-z0-9_/-]+\.test\.ts',path)
            or path.startswith('frontend/fixtures/'))):raise ValueError('Only fixed acceptance tests/fixtures may be amended by the operator')

def verify(row):
    binding=row['binding']
    if manifest.identity(binding)!=row['binding_sha256'] or binding['limits']!=LIMITS:raise ValueError('Workflow binding drift')
    every=binding.get('review_every',0)
    if type(every) is not int or not 0<=every<=100:raise ValueError('Audit policy drift')
    verify_accounting(row)
    verify_audits(row)
    if controller_gate.require_green()!=binding['suite_identity']:raise ValueError('Controller drift')
    if manifest.identity(preparation.load_bundle()[0])!=binding['input_identity'] or manifest.identity(sandbox.POLICY)!=binding['policy_sha256']:raise ValueError('Input/policy drift')
    fingerprint,files=sandbox.capture(binding['workspace']['root'],binding['plan'])
    if fingerprint!=binding['workspace']:raise ValueError('Original workspace drift')
    for path,data in files.items():
        p=manifest.relative_path(path,Path(row['frozen']))
        if p.is_symlink() or p.is_junction() or p.read_bytes()!=data:raise ValueError('Frozen source drift')
    cache_data,receipt=execution.cache_archive(files,binding['cache'])
    if hashlib.sha256(cache_data).hexdigest()!=binding['cache_sha256'] or manifest.identity(receipt)!=binding['receipt_sha256']:raise ValueError('Cache drift')
    return files

def verify_accounting(row):
    expected={k:0 for k in LIMITS}
    for seq,op in enumerate(row['operations']):
        if op['seq']!=seq or op['state'] not in ('confirmed','in_flight'):raise ValueError('Workflow operation drift')
        amount=op['actual'] if op['state']=='confirmed' else op['reserved']
        if any(k not in LIMITS or type(v) is not int or v<0 for k,v in amount.items()):raise ValueError('Workflow accounting drift')
        for k in LIMITS:expected[k]+=amount.get(k,0)
    if row['used']!=expected:raise ValueError('Workflow accounting drift')

def stop(journal,row,state,reason,child=None):
    row.update(state=state,reason=reason)
    row['attention']={'state':state,'reason':reason,'child_id':child,
        'review_ids':copy.deepcopy(row['children']),'operations':copy.deepcopy(row['operations']),
        'feedback':row['product_feedback'],'allowed_changes':'Operator resolves the discrepancy or supplies new input; original operations remain immutable.'}
    save(journal,row,{'kind':'workflow.attention','state':state,'child_id':child,'reason':reason})
    return row

def reserve(journal,row,child_id,kind,amount):
    if any(row['used'][k]+amount.get(k,0)>LIMITS[k] for k in LIMITS):
        stop(journal,row,'blocked_budget','Aggregate workflow budget exhausted before dispatch',child_id);return None
    for k in LIMITS:row['used'][k]+=amount.get(k,0)
    op={'seq':len(row['operations']),'kind':kind,'child_id':child_id,'state':'in_flight','reserved':amount}
    row['operations'].append(op);save(journal,row,{'kind':'workflow.child.started','child_id':child_id,'task':kind})
    return op

def settle(journal,row,op,child,actual):
    # Unknown consumption keeps the full reservation. Completed child journals
    # include reserved tokens for unverified transport, not invented zero usage.
    for k in LIMITS:row['used'][k]+=actual.get(k,0)-op['reserved'].get(k,0)
    op.update(state='confirmed',result_sha256=manifest.identity(child),child_state=child['state'],actual=actual)
    save(journal,row,{'kind':'workflow.child.completed','child_id':op['child_id'],'state':child['state']})

def verified_usage(journal,child):
    ops=journal.records(child['id'],'plan_ops')
    if len(ops)!=child['calls']:return False
    for op in ops:
        result=op.get('result',{})
        if (op['state']!='confirmed' or not result.get('ok') or result.get('done') is not True
            or result.get('done_reason')!='stop' or result.get('tool_calls') or result.get('model_digest')!=child['binding']['digest']
            or any(type(result.get(k)) is not int or not 0<=result[k]<=op['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens'))):return False
    return True


def closed_rounds(row):
    """One whole task correction/review attempt, not each call or shell command."""
    start=row.get('audit_start',0)
    if type(start) is not int or not 0<=start<=len(row['operations']):raise ValueError('Audit origin drift')
    return [op for op in row['operations'][start:] if op['kind']=='review' and op['state']=='confirmed']


def audit_packet(row):
    rounds=closed_rounds(row);start=row.get('last_audited_round',0)
    if type(start) is not int or not 0<=start<=len(rounds):raise ValueError('Audit counter drift')
    return {'schema':'agentapp.phase3-flow-audit/1','workflow_id':row['id'],
        'binding_sha256':row['binding_sha256'],'from_round':start+1,'through_round':len(rounds),
        'review_index':row['review_index'],'product_round':row['round'],'used':copy.deepcopy(row['used']),
        'children':copy.deepcopy(row['children']),
        'originals':[{'id':op['child_id'],'sha256':op['result_sha256'],'state':op['child_state']} for op in rounds[start:]],
        'product_tests_executed':row['product_tests_executed'],'full_product_acceptance':False}


def verify_audits(row):
    if row.get('audit_start',0)!=row['binding'].get('audit_start',0):raise ValueError('Audit origin drift')
    rounds=closed_rounds(row);last=0
    for audit in row.get('audits',[]):
        packet=audit['packet'];end=packet['through_round']
        if (audit['sha256']!=manifest.identity(packet) or packet['workflow_id']!=row['id']
            or packet['binding_sha256']!=row['binding_sha256'] or packet['from_round']!=last+1
            or type(end) is not int or not last<end<=len(rounds)
            or audit['decision'] not in ('continue','discrepancy')):raise ValueError('Audit record drift')
        expected=[{'id':op['child_id'],'sha256':op['result_sha256'],'state':op['child_state']} for op in rounds[last:end]]
        if packet['originals']!=expected:raise ValueError('Audit original identities drift')
        last=end
    if row.get('last_audited_round',0)!=last:raise ValueError('Audit counter drift')


def checkpoint(journal,row):
    every=row['binding'].get('review_every',0)
    if not every or len(closed_rounds(row))-row.get('last_audited_round',0)<every:return False
    if any(op['state']=='in_flight' for op in row['operations']):raise ValueError('Cannot audit an uncertain operation')
    packet=audit_packet(row)
    row.update(state='awaiting_flow_audit',reason='Periodic operator audit after completed agent rounds',
        pending_audit={'packet':packet,'sha256':manifest.identity(packet)})
    save(journal,row,{'kind':'workflow.periodic_audit','through_round':packet['through_round']})
    return True


def acknowledge_audit(journal,run_id,digest,decision,summary):
    """Operator inspection; cannot approve rejected code or reset any budget."""
    if decision not in ('continue','discrepancy') or not isinstance(summary,str) or not 1<=len(summary.encode())<=5000:
        raise ValueError('Invalid audit decision/summary')
    with worker_lock(journal.path,'workflow-'+run_id) as owned:
        if not owned:raise ValueError('Workflow worker busy; audit at the next safe checkpoint')
        row=get(journal,run_id)
        previous=next((a for a in row.get('audits',[]) if a['sha256']==digest),None)
        if previous:
            if previous['decision']!=decision or previous['summary']!=summary:raise ValueError('Audit replay conflict')
            return row
        if row['state']!='awaiting_flow_audit':raise ValueError('No periodic audit awaiting inspection')
        verify(row);packet=audit_packet(row)
        if row['pending_audit']!={'packet':packet,'sha256':manifest.identity(packet)} or digest!=manifest.identity(packet):
            raise ValueError('Stale or altered audit packet')
        for original in packet['originals']:
            if manifest.identity(journal.get(original['id']))!=original['sha256']:raise ValueError('Original review changed before audit')
        row.setdefault('audits',[]).append({'sha256':digest,'packet':packet,'decision':decision,
            'summary':summary,'at':review.now(),'operator':'Codex/user'})
        row['last_audited_round']=packet['through_round'];row.pop('pending_audit')
        if decision=='discrepancy':return stop(journal,row,'awaiting_discrepancy','Operator flow audit: '+summary)
        row.update(state='active',reason='')
        save(journal,row,{'kind':'workflow.audit.completed','through_round':packet['through_round']})
        return row

def feedback(child):
    failures=[op for op in child['operations'] if op.get('state')=='confirmed' and op.get('exit_code') not in (None,0)
              and op.get('stage') in ('be_build','fe_build','unit','e2e') and not op.get('timed_out') and not op.get('truncated')]
    if not failures or child['state']!='failed' or any(x.get('exit_code',1) for x in child['cleanup']):return None
    op=failures[-1]
    if op['exit_code'] in (125,126,127):return None
    output=(op.get('stdout','')+'\n'+op.get('stderr','')).encode()[-6000:].decode('utf-8','replace')
    return source.validate_feedback({'execution_id':child['id'],'execution_sha256':manifest.identity(child),'stage':op['stage'],'output':output})


def unchanged_product(journal,row):
    """Compare all reviewed sources with the last failed execution's originals."""
    previous=next((op for op in reversed(row['operations']) if op['kind']=='execution' and op['state']=='confirmed'),None)
    if not previous or previous['child_id']!=row['product_feedback']['execution_id']:raise ValueError('Missing failed execution lineage')
    originals={}
    for op in row['operations'][:previous['seq']]:
        if op['kind']!='review':continue
        child=journal.get(op['child_id']);section=child['binding'].get('section')
        if section in source.SECTIONS:
            if manifest.identity(child)!=op['result_sha256']:raise ValueError('Failed source original drift')
            originals[section]=child['candidate']
    if set(originals)!=set(source.SECTIONS):raise ValueError('Missing failed source originals')
    return all(journal.get(row['children'][section])['candidate']==originals[section] for section in source.SECTIONS)

def recovery_evidence(journal,child_id):
    """One re-evaluation of a known complete invalid REVIEW, never transport retry."""
    child=journal.get(child_id);ops=journal.records(child_id,'plan_ops')
    if child['state']!='blocked_evidence' or not ops or any(op['state']!='confirmed' for op in ops):return None
    op=ops[-1];result=op.get('result',{})
    if (op['role']!='reviewer' or 'Invalid structured result or evidence citation' not in child['reason']
        or not result.get('ok') or result.get('done') is not True or result.get('done_reason')!='stop' or result.get('tool_calls')
        or result.get('model_digest')!=child['binding']['digest']
        or any(type(result.get(k)) is not int or not 0<=result[k]<=op['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens'))):return None
    text=result['text'].encode()[:8000].decode('utf-8','ignore')
    return review.validate_recovery({'child_id':child_id,'result_sha256':manifest.identity(result),
        'validation_error':child['reason'],'previous_review':text})

def inputs(journal,row,files):
    plan=row['binding']['plan'];plan_id=row['children']['plan']
    accepted=journal.get(plan_id)['candidate']
    if set(p['path'] for p in accepted['files'])!=set(files):raise ValueError('Planner changed the supported file set')
    preparation.validate_plan(accepted,*preparation.load_bundle()[:2])
    for section in source.SECTIONS:
        child=journal.get(row['children'][section]);candidate=child['candidate']
        if candidate['path']!=source.PATHS[section]:raise ValueError('Source target drift')
        files[candidate['path']]=candidate['content'].encode()
    workspace=executor.WORKSPACE/('age50-flow-'+manifest.identity({'id':row['id']})[:16]+'-'+str(row['round']))
    workspace.mkdir(parents=True,exist_ok=True)
    for path,data in files.items():
        dst=manifest.relative_path(path,workspace);dst.parent.mkdir(parents=True,exist_ok=True)
        try:
            with dst.open('xb') as output:output.write(data)
        except FileExistsError:
            if dst.read_bytes()!=data:raise ValueError('Materialized workflow source drift')
    concrete=sandbox.build_manifest(workspace,accepted)
    row['artifact_workspace']=str(workspace);row['manifest_sha256']=manifest.identity(concrete)
    save(journal,row,{'kind':'workflow.materialized','round':row['round'],'workspace':str(workspace)})
    return workspace,concrete

def run(journal,run_id,caller=None,observer=None,review_observer=None,execution_observer=None,dispatcher=None):
    dispatcher=dispatcher or executor.run_phase3_sandbox
    with worker_lock(journal.path,'workflow-'+run_id) as owned:
        if not owned:return get(journal,run_id)|{'busy':True}
        while True:
            row=get(journal,run_id)
            try:files=verify(row)
            except (ValueError,OSError):
                if row['state']!='active':raise
                return stop(journal,row,'blocked_drift','Source, policy, runtime, input or cache changed')
            if row['state']!='active':return row
            if checkpoint(journal,row):return row
            started=time.monotonic()
            if observer:
                try:observer(row)
                except Exception as exc:
                    save(journal,row,{'kind':'observer.failed','error_type':type(exc).__name__})
            pending=next((op for op in row['operations'] if op['state']=='in_flight'),None)
            if row['review_index']<len(KINDS):
                kind=KINDS[row['review_index']];recovery=row.get('recoveries',{}).get(kind,0)
                child_id=row['id']+'-r'+str(row['round'])+'-'+kind.replace('_','-')+('-fix'+str(recovery) if recovery else '')
                # Documentary reviews remain current across source repair rounds.
                if row['round'] and kind not in source.SECTIONS:
                    row['review_index']+=1;save(journal,row);continue
                if kind=='plan':seed=row['binding']['plan']
                elif kind in contract.SECTIONS:seed=preparation.load_bundle()[1]['contract.json'][kind]
                else:
                    previous=row['children'].get(kind)
                    seed=journal.get(previous)['candidate'] if previous else {'path':source.PATHS[kind],'content':files[source.PATHS[kind]].decode()}
                seed=row.get('overrides',{}).get(kind,seed)
                try:child=journal.get(child_id)
                except ValueError:
                    child=journal.create(child_id,seed,section=None if kind=='plan' else kind,
                        product_feedback=row['product_feedback'] if kind in source.SECTIONS and kind not in row.get('recovery_feedback',{}) else None,
                        review_recovery=row.get('recovery_feedback',{}).get(kind))
                if pending and pending['child_id']!=child_id:return stop(journal,row,'uncertain_operation','Pending task identity mismatch',pending['child_id'])
                limits=child['binding']['limits']
                confirmed=next((item for item in row['operations'] if item['child_id']==child_id and item['state']=='confirmed'),None)
                op=pending or confirmed or reserve(journal,row,child_id,'review',{'calls':limits['calls'],'input_tokens':limits['input_tokens'],
                    'output_tokens':limits['output_tokens'],'active_ms':limits['active_seconds']*1000})
                if not op:return row
                try:child=review.run(journal,child_id,caller=caller,observer=review_observer)
                except Exception:return stop(journal,row,'uncertain_operation','Child could not be reconciled; no inference resent',child_id)
                if child['state']=='uncertain_operation':return stop(journal,row,'uncertain_operation','Inference uncertain; original request must be reconciled',child_id)
                if confirmed:
                    if manifest.identity(child)!=op['result_sha256']:return stop(journal,row,'blocked_drift','Confirmed review result changed',child_id)
                else:
                    actual={k:child[k] for k in ('calls','input_tokens','output_tokens','active_ms')} if verified_usage(journal,child) else copy.deepcopy(op['reserved'])
                    settle(journal,row,op,child,actual)
                row['children'][kind]=child_id
                if child['state'] not in SUCCESS:
                    recovered=recovery_evidence(journal,child_id)
                    if recovered and not recovery:
                        row.setdefault('recoveries',{})[kind]=1;row.setdefault('recovery_feedback',{})[kind]=recovered
                        row.setdefault('overrides',{})[kind]=child['candidate']
                        save(journal,row,{'kind':'workflow.review_recovery','child_id':child_id,'task':kind});continue
                    return stop(journal,row,'awaiting_discrepancy','Agent review/correction stopped: '+child['reason'],child_id)
                try:handoff.consume(journal,child_id,kind)
                except ValueError:return stop(journal,row,'blocked_drift','Accepted child evidence could not be consumed',child_id)
                row.get('overrides',{}).pop(kind,None);row.get('recovery_feedback',{}).pop(kind,None)
                row['review_index']+=1;save(journal,row);continue
            child_id=row['id']+'-exec-r'+str(row['round'])
            if row.get('product_feedback'):
                try:
                    if unchanged_product(journal,row):return stop(journal,row,'awaiting_discrepancy','Agents returned identical sources after confirmed product failure; no repeated execution',row['product_feedback']['execution_id'])
                except ValueError as exc:return stop(journal,row,'blocked_drift',str(exc),row['product_feedback']['execution_id'])
            if pending and pending['child_id']!=child_id:return stop(journal,row,'uncertain_operation','Pending execution identity mismatch',pending['child_id'])
            confirmed=next((item for item in row['operations'] if item['child_id']==child_id and item['state']=='confirmed'),None)
            op=pending or confirmed or reserve(journal,row,child_id,'execution',{'executions':1,'active_ms':sandbox.POLICY['total_seconds']*1000})
            if not op:return row
            try:
                workspace,concrete=inputs(journal,row,files)
                docids={k:row['children'][k] for k in handoff.KINDS};codeids={k:row['children'][k] for k in source.SECTIONS}
                child=dispatcher(journal,child_id,docids,codeids,workspace,concrete,manifest.identity(concrete),row['binding']['cache'],observer=execution_observer)
                verify(row)
            except ValueError as exc:return stop(journal,row,'blocked_drift',str(exc),child_id)
            except Exception:return stop(journal,row,'uncertain_operation','Dispatcher acknowledgement uncertain; no execution resent',child_id)
            if child['state']=='uncertain_operation':return stop(journal,row,'uncertain_operation','Product operation uncertain; no automatic replay',child_id)
            if confirmed:
                if manifest.identity(child)!=op['result_sha256']:return stop(journal,row,'blocked_drift','Confirmed execution result changed',child_id)
            else:settle(journal,row,op,child,{'executions':1,'active_ms':max(child['active_ms'],round((time.monotonic()-started)*1000))})
            row['product_tests_executed']|=child['product_tests_executed']
            if child['state']=='completed':
                row.update(state='completed_slice',reason='Autonomous local directory/profile workflow completed; other SaaS tasks remain pending',execution_id=child_id)
                save(journal,row,{'kind':'workflow.completed','execution_id':child_id})
                if observer:
                    try:observer(row)
                    except Exception:save(journal,row,{'kind':'observer.failed'})
                return row
            failed=feedback(child)
            if not failed:return stop(journal,row,'needs_external_help','Infrastructure, cleanup or unsupported failure requires operator help',child_id)
            if row['used']['executions']>=LIMITS['executions'] or row['round']>=LIMITS['executions']-1:return stop(journal,row,'awaiting_discrepancy','Product checks still fail after bounded agent corrections; aggregate execution budget exhausted',child_id)
            row.update(round=row['round']+1,review_index=0,product_feedback=failed)
            save(journal,row,{'kind':'workflow.product_feedback','execution_id':child_id,'stage':failed['stage']})

def export(journal,run_id,destination):
    row=get(journal,run_id);children={}
    for op in row['operations']:
        if op['kind']=='review':
            try:children[op['child_id']]={'run':journal.get(op['child_id']),'operations':journal.records(op['child_id'],'plan_ops'),'events':journal.records(op['child_id'],'plan_events')}
            except ValueError:continue
        else:children[op['child_id']]=execution.get(journal,op['child_id'])
    dst=Path(destination);dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(manifest.canonical({'workflow':row,'children':children})+'\n',encoding='utf-8')
    return dst
