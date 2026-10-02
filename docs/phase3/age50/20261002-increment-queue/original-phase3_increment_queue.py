"""Durable contract increment queue with the supported session-core runner.

Only auth-session-core is dispatched. Other frozen contract scopes remain
explicit prerequisites, never invented implementations or deployment authority.
Original reviews and product executions reserve aggregate consumption before
dispatch. Restarts retain reservations; identical failed product bytes stop.
"""
import argparse
import copy
import json
from pathlib import Path
import re

import controller_gate
import executor
import manifest
import phase3_auth_execution as execution
import phase3_auth_materializer as materializer
import phase3_auth_sandbox as sandbox
import phase3_auth_source as auth
import phase3_handoff as handoff
import phase3_prepare as preparation
import phase3_review as review
import phase3_source as source
import phase3_workflow as workflow
from worker_lock import worker_lock

GROUPS=(('auth-session-core',(),('A01','A02','A03','A24'),('UI06','UI10')),
 ('auth-password-reset',('auth-session-core',),('A04','A05'),('UI10',)),
 ('owner-profile',('auth-session-core',),('A06','A07','A11','A13'),('UI06','UI09','UI10')),
 ('owner-photos',('owner-profile',),('A08','A09','A10'),('UI07','UI10')),
 ('simulated-activation',('owner-profile',),('A14','A15','A16','A17','A18','A19','A20','A21','A22'),('UI08','UI09','UI11')),
 ('official-geography',(),('A25',),('UI01','UI02')))
LIMITS={'calls':48,'input_tokens':1572864,'output_tokens':240000,'active_ms':9000000,'executions':3}
REVIEW_EVERY=3
TERMINAL=('uncertain_operation','blocked_drift','blocked_budget','awaiting_discrepancy','awaiting_supported_increment')


def items():
    lock,docs,_=preparation.load_bundle()
    if lock['files']['contract.json']!=auth.CONTRACT_SHA:raise ValueError('Increment contract drift')
    cases={c['id']:c for c in docs['acceptance.json']}
    ui={c.split(' ',1)[0]:c for c in docs['ui-contract.json']['checks_specified']}
    return [{'id':name,'depends_on':list(deps),'acceptance':[cases[c] for c in api],
      'ui_acceptance':[ui[c] for c in checks],'state':'pending' if name=='auth-session-core' else 'unsupported',
      'supported':name=='auth-session-core','criteria_status':'specified_not_executed',
      'product_tests_executed':False,'full_product_acceptance':False,
      'reason':'' if name=='auth-session-core' else 'Source registry, immutable runtime and product acceptance for this increment are pending'}
      for name,deps,api,checks in GROUPS]


def tables(db):db.execute('CREATE TABLE IF NOT EXISTS phase3_increment_queues(id TEXT PRIMARY KEY,contract_identity TEXT UNIQUE NOT NULL,body TEXT NOT NULL)')


def load(db,run_id):
    found=db.execute('SELECT body FROM phase3_increment_queues WHERE id=?',(run_id,)).fetchone()
    if not found:raise ValueError('Unknown increment queue')
    return manifest.parse(found[0])


def get(journal,run_id):
    with journal.transaction() as db:
        tables(db);return load(db,run_id)


def save(db,row,event=None):
    row['updated_at']=review.now()
    if event:row['events'].append({'seq':len(row['events']),'at':row['updated_at'],**event})
    db.execute('UPDATE phase3_increment_queues SET body=? WHERE id=?',(manifest.canonical(row),row['id']))


def change(journal,run_id,event=None,**changes):
    with journal.transaction() as db:
        row=load(db,run_id);row.update(changes);save(db,row,event);return row


def inputs(journal,resources):
    if set(resources)!={'cache','tls_directory','nss_deb'}:raise ValueError('Increment resource recipe denied')
    seed=sandbox.seed_files();cache=sandbox.cached_tarballs(seed,resources['cache'])
    folder=Path(resources['tls_directory'])
    ca=materializer.checked_bytes(folder/'ca.pem',16384)
    cert=materializer.checked_bytes(folder/'server.pem',16384)
    key=materializer.checked_bytes(folder/'server-key.pem',16384)
    tls={'ca_sha256':sandbox.sha(ca),'server_sha256':sandbox.sha(cert),'certutil_sha256':sandbox.CERTUTIL_SHA256}
    materializer.validate_tls(ca,cert,key,tls)
    deb=materializer.checked_bytes(resources['nss_deb'],16*1024*1024);materializer.validate_nss_deb(deb)
    binding={'seed':sandbox.FILES,'lock':sandbox.LOCK_SHA256,'policy':sandbox.POLICY,'tls_public':tls,
     'cache':{n:sandbox.sha(data) for n,data in sorted(cache.items())},'nss_deb_sha256':sandbox.sha(deb),
     'historical_public_inputs':sandbox.public_originals(journal)}
    return binding,(ca,cert,key,deb)


def create(journal,run_id,cache,tls_directory,nss_deb):
    if not isinstance(run_id,str) or not re.fullmatch('[a-z0-9-]{1,45}',run_id):raise ValueError('Bad increment queue ID')
    resources={k:str(Path(v).resolve()) for k,v in {'cache':cache,'tls_directory':tls_directory,'nss_deb':nss_deb}.items()}
    suite=controller_gate.require_green();lock,_,_=preparation.load_bundle();recipe,_=inputs(journal,resources)
    binding={'suite_identity':suite,'input_identity':manifest.identity(lock),'contract_sha256':auth.CONTRACT_SHA,
     'recipe':recipe,'resources':resources,'items':items(),'limits':LIMITS,'review_every':REVIEW_EVERY,
     'supported_dispatch':['auth-session-core'],'deployment_authorized':False}
    identity=manifest.identity({'requirement_id':lock['requirement_id'],'contract':auth.CONTRACT_SHA})
    with journal.transaction() as db:
        tables(db);prior=db.execute('SELECT id FROM phase3_increment_queues WHERE id=? OR contract_identity=?',(run_id,identity)).fetchall()
        if prior:
            if len(prior)!=1 or prior[0][0]!=run_id:raise ValueError('Contract already queued; preserve its original budget and ID')
            row=load(db,run_id)
            if row['binding']!=binding:raise ValueError('Existing queue binding drift; do not reset')
            return row
        row={'schema':'agentapp.phase3-increment-queue/1','id':run_id,'state':'active','reason':'',
          'binding':binding,'binding_sha256':manifest.identity(binding),'items':copy.deepcopy(binding['items']),
          'used':{k:0 for k in LIMITS},'operations':[],'events':[],'round':0,'review_index':0,
          'reviews':{},'recoveries':{},'recovery_feedback':{},'overrides':{},'product_feedback':None,
          'product_tests_executed':False,'full_product_acceptance':False,'audits':[],
          'last_audited_round':0,'created_at':review.now(),'updated_at':review.now()}
        db.execute('INSERT INTO phase3_increment_queues VALUES(?,?,?)',(run_id,identity,manifest.canonical(row)))
        save(db,row,{'kind':'queue.created','supported':['auth-session-core']})
    return row


def verify(journal,row):
    binding=row['binding']
    if (manifest.identity(binding)!=row['binding_sha256'] or binding['limits']!=LIMITS
        or binding['review_every']!=REVIEW_EVERY or binding['items']!=items()
        or binding['supported_dispatch']!=['auth-session-core'] or binding['deployment_authorized'] is not False
        or controller_gate.require_green()!=binding['suite_identity']
        or manifest.identity(preparation.load_bundle()[0])!=binding['input_identity']):raise ValueError('Increment queue binding drift')
    recipe,private=inputs(journal,binding['resources'])
    if recipe!=binding['recipe']:raise ValueError('Increment resource/original drift')
    expected={k:0 for k in LIMITS}
    for seq,op in enumerate(row['operations']):
        if op['seq']!=seq or op['state'] not in ('in_flight','confirmed'):raise ValueError('Increment operation drift')
        amount=op['actual'] if op['state']=='confirmed' else op['reserved']
        if set(amount)-set(LIMITS) or any(type(v) is not int or v<0 for v in amount.values()):raise ValueError('Increment accounting drift')
        for k in LIMITS:expected[k]+=amount.get(k,0)
        if op['state']=='confirmed':
            child=journal.get(op['child_id']) if op['kind']=='review' else execution.get(journal,op['child_id'])
            if manifest.identity(child)!=op['result_sha256']:raise ValueError('Increment original changed')
    if expected!=row['used'] or any(v>LIMITS[k] for k,v in expected.items()):raise ValueError('Increment aggregate budget drift')
    original_items=items()
    if len(row['items'])!=len(original_items):raise ValueError('Increment item inventory drift')
    for index,(item,original) in enumerate(zip(row['items'],original_items)):
        if index:
            if item!=original:raise ValueError('Unsupported increment acceptance drift')
        elif item!=original:
            op=next((o for o in reversed(row['operations']) if o['kind']=='execution' and o['state']=='confirmed'),None)
            if not op or not accepted(execution.get(journal,op['child_id'])):raise ValueError('Increment acceptance lacks original product tests')
            expected=original|{'state':'qualified_session_core','product_tests_executed':True,
              'criteria_status':'scoped_session_tests_passed_not_full_criterion_coverage','execution_id':op['child_id']}
            if item!=expected:raise ValueError('Increment acceptance scope drift')
    verify_audits(row)
    return private


def stop(journal,run_id,state,reason,child=None):
    return change(journal,run_id,{'kind':'queue.attention','state':state,'child_id':child},state=state,reason=reason,
      attention={'state':state,'reason':reason,'child_id':child,'originals_preserved':True,'budget_reset_allowed':False})


def reserve(journal,run_id,child_id,kind,amount):
    limits=source.LIMITS
    expected=({k:limits[k] for k in ('calls','input_tokens','output_tokens')}|{'active_ms':limits['active_seconds']*1000}) if kind=='review' else {'executions':1,'active_ms':sandbox.POLICY['total_active_ms']+sandbox.POLICY['cleanup_ms']}
    if kind not in ('review','execution') or amount!=expected:raise ValueError('Queue reservation differs from fixed child recipe')
    with journal.transaction() as db:
        row=load(db,run_id)
        if row['state']!='active' or any(o['state']=='in_flight' for o in row['operations']):raise ValueError('Queue cannot reserve another operation')
        if any(o['child_id']==child_id for o in row['operations']):raise ValueError('Queue operation already recorded')
        if set(amount)-set(LIMITS) or any(type(v) is not int or v<0 for v in amount.values()):raise ValueError('Queue reservation invalid')
        if any(row['used'][k]+amount.get(k,0)>LIMITS[k] for k in LIMITS):
            row.update(state='blocked_budget',reason='Increment aggregate budget exhausted before dispatch');save(db,row);return None
        for k in LIMITS:row['used'][k]+=amount.get(k,0)
        op={'seq':len(row['operations']),'child_id':child_id,'kind':kind,'state':'in_flight','reserved':copy.deepcopy(amount)}
        row['operations'].append(op);save(db,row,{'kind':'queue.operation.reserved','child_id':child_id,'task':kind})
        return op


def settle(journal,run_id,child,actual):
    with journal.transaction() as db:
        row=load(db,run_id);op=row['operations'][-1]
        if op['state']!='in_flight' or op['child_id']!=child['id']:raise ValueError('Queue result identity drift')
        if set(actual)-set(op['reserved']) or any(type(v) is not int or not 0<=v<=op['reserved'].get(k,0) for k,v in actual.items()):raise ValueError('Queue result exceeds reservation')
        for k in LIMITS:row['used'][k]+=actual.get(k,0)-op['reserved'].get(k,0)
        op.update(state='confirmed',actual=copy.deepcopy(actual),result_sha256=manifest.identity(child),child_state=child['state'])
        save(db,row,{'kind':'queue.operation.completed','child_id':child['id'],'state':child['state']});return row


def rounds(row):return [o for o in row['operations'] if o['kind']=='review' and o['state']=='confirmed']


def packet(row,start=None,end=None):
    all_rounds=rounds(row);start=row['last_audited_round'] if start is None else start;end=len(all_rounds) if end is None else end
    return {'schema':'agentapp.phase3-increment-audit/1','queue_id':row['id'],'binding_sha256':row['binding_sha256'],
      'from_round':start+1,'through_round':end,'originals':[{'id':o['child_id'],'sha256':o['result_sha256'],'state':o['child_state']} for o in all_rounds[start:end]],
      'used':copy.deepcopy(row['used']),'product_tests_executed':row['product_tests_executed'],
      'product_feedback':copy.deepcopy(row['product_feedback']),'execution_originals':[{'id':o['child_id'],'sha256':o['result_sha256'],'state':o['child_state']} for o in row['operations'] if o['kind']=='execution' and o['state']=='confirmed'],
      'pending_discrepancy':copy.deepcopy(row.get('pending_attention')),'full_product_acceptance':False}


def verify_audits(row):
    last=0;all_rounds=rounds(row)
    for audit in row['audits']:
        p=audit['packet'];end=p['through_round']
        expected=[{'id':o['child_id'],'sha256':o['result_sha256'],'state':o['child_state']} for o in all_rounds[last:end]]
        if (audit['sha256']!=manifest.identity(p) or p['queue_id']!=row['id'] or p['binding_sha256']!=row['binding_sha256']
            or p['from_round']!=last+1 or type(end) is not int or not last<end<=len(all_rounds)
            or p['originals']!=expected or audit['decision'] not in ('continue','discrepancy')):raise ValueError('Increment audit drift')
        last=end
    if row['last_audited_round']!=last:raise ValueError('Increment audit counter drift')


def checkpoint(journal,row):
    if len(rounds(row))-row['last_audited_round']<REVIEW_EVERY:return False
    if any(o['state']=='in_flight' for o in row['operations']):raise ValueError('Cannot audit uncertain increment work')
    p=packet(row)
    change(journal,row['id'],{'kind':'queue.periodic_audit','through_round':p['through_round']},
      state='awaiting_flow_audit',reason='Periodic review after three completed agent attempts',pending_audit={'packet':p,'sha256':manifest.identity(p)})
    return True


def acknowledge_audit(journal,run_id,digest,decision,summary):
    if decision not in ('continue','discrepancy') or not isinstance(summary,str) or not 1<=len(summary.encode())<=5000:raise ValueError('Bad queue audit decision')
    with worker_lock(journal.path,'increment-'+run_id) as owned:
        if not owned:raise ValueError('Queue worker busy')
        row=get(journal,run_id)
        previous=next((a for a in row['audits'] if a['sha256']==digest),None)
        if previous:
            if previous['decision']!=decision or previous['summary']!=summary:raise ValueError('Queue audit replay conflict')
            return row
        verify(journal,row);p=packet(row)
        if row['state']!='awaiting_flow_audit' or row['pending_audit']!={'packet':p,'sha256':digest} or digest!=manifest.identity(p):raise ValueError('Stale queue audit packet')
        audits=row['audits']+[{'packet':p,'sha256':digest,'decision':decision,'summary':summary,'operator':'Codex/user','at':review.now()}]
        row=change(journal,run_id,{'kind':'queue.audit.completed','through_round':p['through_round']},audits=audits,last_audited_round=p['through_round'],pending_audit=None,state='active',reason='')
        return stop(journal,run_id,'awaiting_discrepancy','Periodic audit discrepancy: '+summary) if decision=='discrepancy' else row


def workspace(journal,row):
    files=sandbox.seed_files()
    for kind in auth.SECTIONS:
        child,_,_,_=handoff.consume(journal,row['reviews'][kind],kind)
        files[auth.PATHS[kind]]=child['candidate']['content'].encode()
    root=executor.WORKSPACE/('age50-increment-'+manifest.identity({'id':row['id']})[:16]+'-'+str(row['round']))
    root.mkdir(parents=True,exist_ok=True)
    if root.is_symlink() or root.is_junction():raise ValueError('Increment workspace link')
    for name,data in files.items():
        target=manifest.relative_path(name,root);target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink() or target.is_junction():raise ValueError('Increment source link')
        try:
            with target.open('xb') as output:output.write(data)
        except FileExistsError:
            if target.read_bytes()!=data:raise ValueError('Increment materialized original drift')
    concrete=sandbox.assemble(journal,row['reviews'],root,row['binding']['recipe']['tls_public'])
    return root,concrete


def failed_feedback(child):
    reports=[r for r in child.get('reports',[]) if r['exit_code']!=0 and r['name'] in sandbox.STAGES]
    if (child['state']!='failed_operation' or not reports or any(o['state']!='confirmed' for o in child['operations'])
        or len(child.get('cleanup',[]))!=len(child.get('resources',[])) or any(not c['confirmed'] for c in child['cleanup'])):return None
    failed=reports[-1]
    if failed['exit_code'] in (125,126,127):return None
    stage={'api':'unit','browser':'e2e','bundle':'fe_build'}.get(failed['name'],failed['name'])
    output=(failed['stdout']+'\n'+failed['stderr']).encode()[-6000:].decode('utf-8','replace')
    return source.validate_feedback({'execution_id':child['id'],'execution_sha256':manifest.identity(child),'stage':stage,'output':output})


def accepted(child):
    try:return _accepted(child)
    except (KeyError,ValueError,TypeError,IndexError):return False


def _accepted(child):
    if (child['state']!='qualified_session_core' or child.get('product_tests_executed') is not True
        or child.get('accepted_before_cleanup') is not True or child.get('runtime_ready') is not True
        or len(child.get('cleanup',[]))!=len(child.get('resources',[])) or not child.get('resources')
        or any(not c['confirmed'] for c in child['cleanup'])
        or any(o['state']!='confirmed' or o['outcome']['exit_code']!=0 for o in child['operations'])):return False
    owned={(r['kind'],r['name']) for r in child['resources']}
    cleaned=[(r['kind'],r['name']) for r in child['cleanup']]
    if len(owned)!=len(child['resources']) or len(set(cleaned))!=len(cleaned) or set(cleaned)!=owned:return False
    reports=child.get('reports',[])
    if [r['name'] for r in reports]!=list(sandbox.STAGES) or any(r['exit_code']!=0 for r in reports):return False
    api=next(r['stdout'] for r in reports if r['name']=='api')
    if any(not re.search(r'# '+field+r' '+str(value)+r'\b',api) for field,value in (('tests',9),('pass',9),('fail',0),('cancelled',0),('skipped',0),('todo',0))):return False
    report=json.loads(child['browser_report']);stats=report['stats']
    return stats.get('expected')==5 and all(stats.get(k)==0 for k in ('unexpected','flaky','skipped')) and not report.get('errors')


def run(journal,run_id,caller=None,review_observer=None,transport=None):
    with worker_lock(journal.path,'increment-'+run_id) as owned:
        if not owned:return get(journal,run_id)|{'busy':True}
        while True:
            row=get(journal,run_id)
            if row['state']!='active':return row
            try:private=verify(journal,row)
            except (ValueError,OSError):return stop(journal,run_id,'blocked_drift','Controller, contract, resources, budget or original evidence changed')
            if checkpoint(journal,row):return get(journal,run_id)
            if row.get('pending_attention'):
                attention=row['pending_attention']
                return stop(journal,run_id,'awaiting_discrepancy',attention['reason'],attention['child_id'])
            pending=next((o for o in row['operations'] if o['state']=='in_flight'),None)
            if row['review_index']<len(auth.SECTIONS):
                kind=auth.SECTIONS[row['review_index']];recovery=row['recoveries'].get(kind,0)
                child_id=run_id+'-r'+str(row['round'])+'-'+kind.replace('_','-')+('-fix1' if recovery else '')
                seed=journal.get(row['reviews'][kind])['candidate'] if kind in row['reviews'] else {'path':auth.PATHS[kind],'content':''}
                seed=row['overrides'].get(kind,seed)
                if pending and (pending['child_id']!=child_id or pending['kind']!='review'):return stop(journal,run_id,'uncertain_operation','Pending review identity differs',pending['child_id'])
                confirmed=next((o for o in row['operations'] if o['child_id']==child_id and o['state']=='confirmed'),None)
                if not pending and not confirmed:
                    limits=source.LIMITS;amount={k:limits[k] for k in ('calls','input_tokens','output_tokens')};amount['active_ms']=limits['active_seconds']*1000
                    pending=reserve(journal,run_id,child_id,'review',amount)
                    if not pending:return get(journal,run_id)
                try:
                    try:child=journal.get(child_id)
                    except ValueError:child=journal.create(child_id,seed,section=kind,
                      product_feedback=row['product_feedback'] if not recovery else None,review_recovery=row['recovery_feedback'].get(kind))
                    if child['binding']['seed_sha256']!=manifest.identity(seed) or child['binding'].get('section')!=kind:raise ValueError('Queue child seed identity differs')
                    if not confirmed:child=review.run(journal,child_id,caller=caller,observer=review_observer)
                except Exception:return stop(journal,run_id,'uncertain_operation','Review acknowledgement uncertain; original inference is never resent',child_id)
                if child.get('busy'):return get(journal,run_id)|{'busy':True}
                if child['state']=='uncertain_operation':return stop(journal,run_id,'uncertain_operation','Incomplete original inference preserved',child_id)
                if not confirmed:
                    actual={k:child[k] for k in ('calls','input_tokens','output_tokens','active_ms')} if workflow.verified_usage(journal,child) else pending['reserved']
                    try:row=settle(journal,run_id,child,actual)
                    except ValueError:return stop(journal,run_id,'uncertain_operation','Review consumption could not be reconciled; full reservation retained',child_id)
                if child['state']!='reviewed_application_file':
                    recovered=workflow.recovery_evidence(journal,child_id)
                    if recovered and not recovery:
                        recoveries=row['recoveries']|{kind:1};feedback=row['recovery_feedback']|{kind:recovered};overrides=row['overrides']|{kind:child['candidate']}
                        change(journal,run_id,{'kind':'queue.review_recovery','child_id':child_id},recoveries=recoveries,recovery_feedback=feedback,overrides=overrides);continue
                    change(journal,run_id,pending_attention={'reason':'Original source review stopped: '+child['reason'],'child_id':child_id});continue
                try:handoff.consume(journal,child_id,kind)
                except ValueError:return stop(journal,run_id,'blocked_drift','Reviewed original cannot be consumed',child_id)
                change(journal,run_id,reviews=row['reviews']|{kind:child_id},review_index=row['review_index']+1)
                continue
            if row['product_feedback']:
                previous=execution.get(journal,row['product_feedback']['execution_id'])
                previous_hashes=previous['manifest']['binding']['workspace']['files']
                if all(sandbox.sha(journal.get(row['reviews'][k])['candidate']['content'].encode())==previous_hashes[auth.PATHS[k]] for k in auth.SECTIONS):
                    return stop(journal,run_id,'awaiting_discrepancy','Agents returned unchanged auth after product failure; no repeated execution',previous['id'])
            child_id=run_id+'-auth-exec-r'+str(row['round'])
            if pending and (pending['child_id']!=child_id or pending['kind']!='execution'):return stop(journal,run_id,'uncertain_operation','Pending execution identity differs',pending['child_id'])
            confirmed=next((o for o in row['operations'] if o['child_id']==child_id and o['state']=='confirmed'),None)
            if not pending and not confirmed:
                pending=reserve(journal,run_id,child_id,'execution',{'executions':1,'active_ms':sandbox.POLICY['total_active_ms']+sandbox.POLICY['cleanup_ms']})
                if not pending:return get(journal,run_id)
            try:
                root,concrete=workspace(journal,row)
                try:child=execution.get(journal,child_id)
                except ValueError:child=None
                if confirmed:
                    if child is None:raise ValueError('Confirmed execution missing')
                elif child and child['operations']:
                    execution.recover_interrupted(journal,child_id)
                    child=materializer.Runtime(journal,child_id,transport or materializer.process_tools.process).cleanup()
                else:
                    args=() if transport is None else (transport,)
                    materializer.run(journal,child_id,concrete,row['reviews'],root,row['binding']['resources']['cache'],*private,*args)
                    child=execution.get(journal,child_id)
                sandbox.validate(concrete,journal,row['reviews'],root,row['binding']['recipe']['tls_public'])
            except Exception:return stop(journal,run_id,'uncertain_operation','Product acknowledgement uncertain; do not repeat resource operations',child_id)
            if child['state'] in ('uncertain_operation','needs_resource_reconciliation'):return stop(journal,run_id,'uncertain_operation','Product operation/cleanup uncertain; reservation retained',child_id)
            if not confirmed:
                try:row=settle(journal,run_id,child,{'executions':1,'active_ms':child['used']['active_ms']})
                except ValueError:return stop(journal,run_id,'uncertain_operation','Execution consumption cannot be reconciled; reservation retained',child_id)
            row=change(journal,run_id,product_tests_executed=row['product_tests_executed'] or child['product_tests_executed'])
            if accepted(child):
                remaining=copy.deepcopy(row['items']);remaining[0].update(state='qualified_session_core',product_tests_executed=True,
                  criteria_status='scoped_session_tests_passed_not_full_criterion_coverage',execution_id=child_id)
                change(journal,run_id,{'kind':'queue.increment.completed','increment':'auth-session-core'},items=remaining)
                return stop(journal,run_id,'awaiting_supported_increment','Session core accepted; remaining scopes require their source registry, frozen runtime and acceptance',child_id)
            feedback=failed_feedback(child)
            if not feedback:return stop(journal,run_id,'awaiting_discrepancy','Unsupported infrastructure/product result; original preserved',child_id)
            if row['used']['executions']>=LIMITS['executions']:return stop(journal,run_id,'awaiting_discrepancy','Auth still fails after bounded corrections; execution budget exhausted',child_id)
            change(journal,run_id,{'kind':'queue.product_feedback','execution_id':child_id,'stage':feedback['stage']},round=row['round']+1,
              review_index=0,product_feedback=feedback,recoveries={},recovery_feedback={},overrides={})


def export(journal,run_id,destination):
    row=get(journal,run_id);evidence={}
    for op in row['operations']:
        if op['kind']=='review':
            try:evidence[op['child_id']]={'run':journal.get(op['child_id']),'operations':journal.records(op['child_id'],'plan_ops')}
            except ValueError:pass
        else:
            try:evidence[op['child_id']]=execution.get(journal,op['child_id'])
            except ValueError:pass
    value={'queue':row,'originals':evidence,'product_acceptance_scope':'session core only','full_product_acceptance':False}
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    # Export is a projection; durable originals stay in SQLite.
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    run_parser=sub.add_parser('run');run_parser.add_argument('--run-id',required=True)
    for name in ('cache','tls-directory','nss-deb'):run_parser.add_argument('--'+name,type=Path)
    run_parser.add_argument('--export',type=Path,required=True)
    audit=sub.add_parser('audit');audit.add_argument('--run-id',required=True);audit.add_argument('--packet-digest',required=True)
    audit.add_argument('--decision',choices=('continue','discrepancy'),required=True);audit.add_argument('--summary',type=Path,required=True);audit.add_argument('--export',type=Path,required=True)
    status=sub.add_parser('status');status.add_argument('--run-id',required=True)
    args=parser.parse_args(argv);journal=review.Journal()
    if args.command=='run':
        resources=(args.cache,args.tls_directory,args.nss_deb)
        if any(resources):
            if not all(resources):raise ValueError('A new queue requires all three resource inputs')
            create(journal,args.run_id,*resources)
        row=run(journal,args.run_id);export(journal,args.run_id,args.export)
    elif args.command=='audit':
        row=acknowledge_audit(journal,args.run_id,args.packet_digest,args.decision,args.summary.read_text(encoding='utf-8'))
        export(journal,args.run_id,args.export)
    else:row=get(journal,args.run_id)
    print(json.dumps({k:row[k] for k in ('id','state','reason','used','round','review_index','last_audited_round','product_tests_executed','full_product_acceptance')},ensure_ascii=False,indent=2))
    return 0 if row['state']=='active' else 2


if __name__=='__main__':raise SystemExit(main())
