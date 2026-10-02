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

def create(journal,run_id,workspace,plan,cache,parent_id=None):
    if not isinstance(run_id,str) or not re.fullmatch(r'[a-z0-9-]{1,55}',run_id):raise ValueError('Bad workflow ID')
    suite=controller_gate.require_green();lock,docs,_=preparation.load_bundle()
    preparation.validate_plan(plan,lock,docs)
    fingerprint,files=sandbox.capture(workspace,plan)
    if not set(source.PATHS.values())<=set(files):raise ValueError('All source modules must be planned')
    for path in source.PATHS.values():files[path].decode('utf-8')
    cache_data,receipt=execution.cache_archive(files,cache)
    parent=get(journal,parent_id) if parent_id else None
    if parent:
        verify_accounting(parent)
        if parent['state'] not in ('awaiting_discrepancy','needs_external_help','blocked_drift') or any(op['state']!='confirmed' for op in parent['operations']):raise ValueError('Uncertain/active/budget-limited parent cannot be restarted')
        if parent['binding']['limits']!=LIMITS or parent['binding']['workspace']!=fingerprint or parent['binding']['plan']!=plan:raise ValueError('Continuation must preserve parent budget and original inputs')
    binding={'suite_identity':suite,'input_identity':manifest.identity(lock),'policy_sha256':manifest.identity(sandbox.POLICY),
        'workspace':fingerprint,'plan':plan,'cache':str(Path(cache).resolve()),
        'cache_sha256':hashlib.sha256(cache_data).hexdigest(),'receipt_sha256':manifest.identity(receipt),'limits':LIMITS}
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
        'recoveries':{},'recovery_feedback':{},'overrides':{}}
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

def verify(row):
    binding=row['binding']
    if manifest.identity(binding)!=row['binding_sha256'] or binding['limits']!=LIMITS:raise ValueError('Workflow binding drift')
    verify_accounting(row)
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

def feedback(child):
    failures=[op for op in child['operations'] if op.get('state')=='confirmed' and op.get('exit_code') not in (None,0)
              and op.get('stage') in ('be_build','fe_build','unit','e2e') and not op.get('timed_out') and not op.get('truncated')]
    if not failures or child['state']!='failed' or any(x.get('exit_code',1) for x in child['cleanup']):return None
    op=failures[-1]
    if op['exit_code'] in (125,126,127):return None
    output=(op.get('stdout','')+'\n'+op.get('stderr','')).encode()[-6000:].decode('utf-8','replace')
    return source.validate_feedback({'execution_id':child['id'],'execution_sha256':manifest.identity(child),'stage':op['stage'],'output':output})

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
            if row['round']>=LIMITS['executions']-1:return stop(journal,row,'awaiting_discrepancy','Product checks still fail after bounded agent corrections',child_id)
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
