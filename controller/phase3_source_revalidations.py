"""One prospective revalidation, same queue/aggregate, immutable audit lineage."""
import copy
import json
from pathlib import Path
import controller_gate
import manifest
import phase3_geography_revalidation as protocol
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_prepare as preparation
import phase3_queued_sources as base
import phase3_review as review
import phase3_source_corrections as correction
import phase3_workflow as workflow
from worker_lock import worker_lock

TABLE='phase3_source_revalidations'
amount=correction.amount
originals=correction.originals


def tables(db):db.execute('CREATE TABLE IF NOT EXISTS phase3_source_revalidations(queue_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
def load(db,rid):
    found=db.execute('SELECT body FROM phase3_source_revalidations WHERE queue_id=?',(rid,)).fetchone()
    if found is None:raise ValueError('Unknown source revalidation')
    return manifest.parse(found[0])
def get(journal,rid):
    with journal.transaction() as db:tables(db);return load(db,rid)
def save(db,row,event=None):
    row['updated_at']=review.now()
    if event:row['events'].append({'seq':len(row['events']),'at':row['updated_at'],**event})
    db.execute('UPDATE phase3_source_revalidations SET body=? WHERE queue_id=?',(manifest.canonical(row),row['queue_id']))
def original(db,rid):
    child=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(rid,)).fetchone()[0])
    ops=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_ops WHERE run_id=? ORDER BY seq',(rid,))]
    return child,ops


def packet(row,parent,previous,first):
    rounds=queue.rounds(parent)+[o for o in first['operations']+previous['operations']+row['operations'] if o['state']=='confirmed']
    start=previous['audit']['packet']['through_round']
    return {'schema':'agentapp.phase3-source-revalidation-audit/1','queue_id':parent['id'],'binding_sha256':row['binding_sha256'],
      'from_round':start+1,'through_round':len(rounds),
      'originals':[{'id':o['child_id'],'sha256':o['result_sha256'],'state':o['child_state']} for o in rounds[start:]],
      'preserved_parent_children':row['binding']['parent_children'],
      'used':{k:parent['used'][k]+first['used'][k]+previous['used'][k]+row['used'][k] for k in queue.LIMITS},
      'product_tests_executed':False,'full_product_acceptance':False}


def check(db,row,parent,previous):
    first=base.load(db,parent['id']);correction.check(db,previous,parent,first)
    b=row['binding']
    if (manifest.identity(b)!=row['binding_sha256'] or b['protocol']!=protocol.PROTOCOL
        or b['parent_sha256']!=manifest.identity(parent) or b['previous_sha256']!=manifest.identity(previous)
        or b['previous_snapshot']!=previous or b['parent_children']!=base.child_originals(db,parent)
        or b['previous_originals']!=originals(db,previous['child_id']) or b['limits']!=queue.LIMITS
        or b['input_identity']!=manifest.identity(preparation.load_bundle()[0])
        or b['rules_sha256']!=manifest.identity(protocol.RULES) or b['acceptance_sha256']!=first['binding']['acceptance_sha256']
        or not previous['audit'] or previous['audit']['decision']!='continue'
        or b['audited_through_round']!=previous['audit']['packet']['through_round']
        or row['queue_id']!=parent['id'] or row['child_id']!=parent['id']+'-geo-revalidation-r0'
        or row['source_only'] is not True or row['product_tests_executed'] is not False or row['full_product_acceptance'] is not False
        or b['execution_authorized'] is not False):raise ValueError('Revalidation binding/original drift')
    seed,proof=protocol.origin(*original(db,previous['child_id']))
    if b['origin']!=proof or b['seed']!=seed:raise ValueError('Revalidation seed drift')
    used={k:0 for k in queue.LIMITS}
    if len(row['operations'])>1:raise ValueError('Only one revalidation lineage permitted')
    for op in row['operations']:
        if op.get('kind')!='review' or op['child_id']!=row['child_id'] or op['reserved']!=amount() or op['state'] not in ('in_flight','confirmed'):raise ValueError('Revalidation reservation drift')
        value=op['actual'] if op['state']=='confirmed' else op['reserved']
        if set(value)!=set(amount()) or any(type(v) is not int or not 0<=v<=amount()[k] for k,v in value.items()):raise ValueError('Revalidation accounting drift')
        for k in used:used[k]+=value.get(k,0)
        if op['state']=='confirmed':
            child,ops=original(db,row['child_id']);captured=originals(db,child['id'])
            if captured!=op['originals'] or captured['run_sha256']!=op['result_sha256'] or child['state'] in ('active','uncertain_operation') or child['state']!=op['child_state']:raise ValueError('Revalidation result drift')
            if child['binding'].get('section')!=protocol.SECTION or child['binding'].get('source_origin')!=proof:raise ValueError('Revalidation child binding drift')
            if op['source_accepted'] and (child['state']!='reviewed_application_file' or child['findings'] or child['execution_authorized'] or child['product_tests_executed']):raise ValueError('Revalidation false acceptance')
            if value!=amount() and (value!={k:child[k] for k in amount()} or len(ops)!=child['calls'] or not ops or any(
                o['state']!='confirmed' or not o.get('result',{}).get('ok') or o['result'].get('done') is not True
                or o['result'].get('done_reason')!='stop' or o['result'].get('tool_calls') or o['result'].get('model_digest')!=child['binding']['digest']
                or any(type(o['result'].get(k)) is not int or not 0<=o['result'][k]<=o['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens')) for o in ops)):
                raise ValueError('Revalidation unverifiable refund')
    if used!=row['used']:raise ValueError('Revalidation counter drift')
    if row['state']=='reviewed_source' and (not row['operations'] or not row['operations'][0].get('source_accepted')):raise ValueError('Revalidation requires original handoff')
    p=packet(row,parent,previous,first);due=p['through_round']-p['from_round']+1>=queue.REVIEW_EVERY
    expected={'packet':p,'sha256':manifest.identity(p)}
    if row['pending_audit'] and row['pending_audit']!=expected:raise ValueError('Revalidation pending audit drift')
    if row['audit'] and (any(row['audit'][k]!=v for k,v in expected.items()) or row['audit']['decision'] not in ('continue','discrepancy')
        or not isinstance(row['audit']['notes'],str) or not 1<=len(row['audit']['notes'])<=3000):raise ValueError('Revalidation audit drift')
    if due and not row['audit'] and (row['state']!='awaiting_flow_audit' or not row['pending_audit']):raise ValueError('Third complete attempt requires audit')
    return used


def add_usage(db,parent,total):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",(TABLE,)).fetchone():return total
    found=db.execute('SELECT body FROM phase3_source_revalidations WHERE queue_id=?',(parent['id'],)).fetchone()
    if found:
        used=check(db,manifest.parse(found[0]),parent,correction.load(db,parent['id']))
        total={k:v+used[k] for k,v in total.items()}
    return total


def create(journal,rid,previous_digest):
    suite=controller_gate.require_green()
    with worker_lock(journal.path,'increment-'+rid) as owned:
        if not owned:raise ValueError('Source worker busy')
        with journal.transaction() as db:
            tables(db);parent=queue.load(db,rid);previous=correction.load(db,rid);first=base.load(db,rid)
            correction.check(db,previous,parent,first)
            if manifest.identity(previous)!=previous_digest:raise ValueError('Stale revalidation parent')
            if (previous['state']!='awaiting_discrepancy' or len(previous['operations'])!=1 or previous['operations'][0]['state']!='confirmed'
                or not previous['audit'] or previous['audit']['decision']!='continue'):raise ValueError('Completed audited discrepancy required')
            seed,proof=protocol.origin(*original(db,previous['child_id']))
            b={'suite_identity':suite,'protocol':protocol.PROTOCOL,'previous_snapshot':copy.deepcopy(previous),'previous_sha256':previous_digest,
              'previous_originals':originals(db,previous['child_id']),'parent_sha256':manifest.identity(parent),'parent_children':base.child_originals(db,parent),
              'input_identity':manifest.identity(preparation.load_bundle()[0]),'rules_sha256':manifest.identity(protocol.RULES),
              'acceptance_sha256':first['binding']['acceptance_sha256'],'origin':proof,'seed':seed,
              'audited_through_round':previous['audit']['packet']['through_round'],'limits':queue.LIMITS,'execution_authorized':False}
            found=db.execute('SELECT body FROM phase3_source_revalidations WHERE queue_id=?',(rid,)).fetchone()
            if found:
                row=manifest.parse(found[0])
                if row['binding']!=b:raise ValueError('Existing revalidation binding drift; no fresh budget')
                check(db,row,parent,previous)
                return row
            cid=rid+'-geo-revalidation-r0'
            if db.execute('SELECT 1 FROM plan_runs WHERE id=?',(cid,)).fetchone():raise ValueError('Revalidation child ID already used')
            row={'schema':'agentapp.phase3-source-revalidation/1','queue_id':rid,'child_id':cid,'binding':b,'binding_sha256':manifest.identity(b),
              'state':'active','reason':'','used':{k:0 for k in queue.LIMITS},'operations':[],'events':[],
              'created_at':review.now(),'updated_at':review.now(),'source_only':True,'product_tests_executed':False,'full_product_acceptance':False,'audit':None,'pending_audit':None}
            check(db,row,parent,previous);db.execute('INSERT INTO phase3_source_revalidations VALUES(?,?)',(rid,manifest.canonical(row)))
            save(db,row,{'kind':'source_revalidation.created','original_id':proof['original_id']});return row


def settle(journal,rid):
    row=get(journal,rid)
    try:child=journal.get(row['child_id'])
    except ValueError:child=None
    if child is None or child['state'] in ('active','uncertain_operation') or any(o['state']=='in_flight' for o in journal.records(child['id'],'plan_ops')):
        with journal.transaction() as db:
            row=load(db,rid);check(db,row,queue.load(db,rid),correction.load(db,rid))
            if row['state']!='uncertain_operation':row.update(state='uncertain_operation',reason='Full reservation retained; inspect originals, never replay');save(db,row,{'kind':'source_revalidation.uncertain'})
        return get(journal,rid)
    review.verify_binding(child);verified=workflow.verified_usage(journal,child);accepted=False
    if child['state']=='reviewed_application_file':
        try:handoff.consume(journal,child['id'],protocol.SECTION);accepted=True
        except ValueError:pass
    with journal.transaction() as db:
        row=load(db,rid);parent=queue.load(db,rid);previous=correction.load(db,rid);check(db,row,parent,previous)
        op=row['operations'][0]
        if op['state']!='in_flight':return row
        captured=originals(db,child['id'])
        if captured['run_sha256']!=manifest.identity(child):raise ValueError('Revalidation changed before settlement')
        actual={k:child[k] for k in amount()} if verified else amount()
        op.update(state='confirmed',actual=actual,result_sha256=manifest.identity(child),child_state=child['state'],source_accepted=accepted,originals=captured)
        row['used']={k:actual.get(k,0) for k in queue.LIMITS};row.update(state='reviewed_source' if accepted else 'awaiting_discrepancy',reason='Source only; PostgreSQL acceptance/composition/official data pending' if accepted else 'Prospective source review stopped')
        p=packet(row,parent,previous,base.load(db,rid))
        if p['through_round']-p['from_round']+1>=queue.REVIEW_EVERY:row['pending_audit']={'packet':p,'sha256':manifest.identity(p)};row['state']='awaiting_flow_audit'
        check(db,row,parent,previous);save(db,row,{'kind':'source_revalidation.completed','source_only':True});return row


def run(journal,rid,caller=None,observer=None):
    with worker_lock(journal.path,'increment-'+rid) as owned:
        if not owned:return get(journal,rid)|{'busy':True}
        with journal.transaction() as db:
            row=load(db,rid);parent=queue.load(db,rid);previous=correction.load(db,rid);check(db,row,parent,previous)
            if row['state'] not in ('active','uncertain_operation'):return row
            if controller_gate.require_green()!=row['binding']['suite_identity']:raise ValueError('Revalidation controller drift')
            fresh=not row['operations']
            if fresh:
                total=base.aggregate(db,parent)
                if any(total[k]+amount().get(k,0)>queue.LIMITS[k] for k in total):row.update(state='blocked_budget',reason='Original shared cap exhausted');save(db,row);return row
                row['operations']=[{'child_id':row['child_id'],'kind':'review','reserved':amount(),'state':'in_flight'}]
                row['used']={k:amount().get(k,0) for k in queue.LIMITS};save(db,row,{'kind':'source_revalidation.reserved'})
        if fresh:
            try:
                journal.create(row['child_id'],row['binding']['seed'],section=protocol.SECTION,source_origin=row['binding']['origin'])
                review.run(journal,row['child_id'],caller=caller,observer=observer)
            except Exception:pass
        return settle(journal,rid)


def export(journal,rid,path):
    row=get(journal,rid)
    with journal.transaction() as db:total=base.aggregate(db,queue.load(db,rid))
    child=None
    if row['operations']:
        try:child={'run':journal.get(row['child_id']),'operations':journal.records(row['child_id'],'plan_ops'),'events':journal.records(row['child_id'],'plan_events')}
        except ValueError:pass
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'revalidation':row,'shared_used':total,'original':child,'full_product_acceptance':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
