"""One grounded correction of completed geography originals, shared queue cap."""
import copy
import json
from pathlib import Path
import controller_gate
import manifest
import phase3_geography_correction as protocol
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_prepare as preparation
import phase3_queued_sources as base
import phase3_review as review
import phase3_source as source
import phase3_workflow as workflow
from worker_lock import worker_lock

TABLE='phase3_source_corrections'


def amount():return {k:source.LIMITS[k] for k in ('calls','input_tokens','output_tokens')}|{'active_ms':source.LIMITS['active_seconds']*1000}
def tables(db):db.execute('CREATE TABLE IF NOT EXISTS phase3_source_corrections(queue_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
def load(db,rid):
    found=db.execute('SELECT body FROM phase3_source_corrections WHERE queue_id=?',(rid,)).fetchone()
    if found is None:raise ValueError('Unknown source correction')
    return manifest.parse(found[0])
def get(journal,rid):
    with journal.transaction() as db:tables(db);return load(db,rid)
def save(db,row,event=None):
    row['updated_at']=review.now()
    if event:row['events'].append({'seq':len(row['events']),'at':row['updated_at'],**event})
    db.execute('UPDATE phase3_source_corrections SET body=? WHERE queue_id=?',(manifest.canonical(row),row['queue_id']))
def originals(db,rid):return base.child_originals(db,{'operations':[{'kind':'review','child_id':rid}]})[rid]


def packet(row,parent,previous):
    rounds=queue.rounds(parent)+[o for o in previous['operations']+row['operations'] if o['state']=='confirmed']
    start=previous['audit']['packet']['through_round'] if previous['audit'] else parent['last_audited_round']
    return {'schema':'agentapp.phase3-source-correction-audit/1','queue_id':parent['id'],'binding_sha256':row['binding_sha256'],
      'from_round':start+1,'through_round':len(rounds),
      'originals':[{'id':o['child_id'],'sha256':o['result_sha256'],'state':o['child_state']} for o in rounds[start:]],
      'preserved_parent_children':row['binding']['parent_children'],
      'used':{k:parent['used'][k]+previous['used'][k]+row['used'][k] for k in queue.LIMITS},
      'product_tests_executed':False,'full_product_acceptance':False}


def check(db,row,parent,previous):
    b=row['binding'];base.check(db,previous,parent)
    if (manifest.identity(b)!=row['binding_sha256'] or b['protocol']!=protocol.PROTOCOL
        or b['parent_sha256']!=manifest.identity(parent) or b['previous_sha256']!=manifest.identity(previous)
        or b['previous_snapshot']!=previous or b['parent_children']!=base.child_originals(db,parent)
        or b['previous_originals']!=originals(db,previous['child_id']) or b['limits']!=queue.LIMITS
        or b['input_identity']!=manifest.identity(preparation.load_bundle()[0])
        or b['rules_sha256']!=manifest.identity(protocol.RULES) or b['acceptance_sha256']!=previous['binding']['acceptance_sha256']
        or row['queue_id']!=parent['id'] or row['child_id']!=parent['id']+'-geo-correction-r0'
        or row['source_only'] is not True or row['product_tests_executed'] is not False or row['full_product_acceptance'] is not False
        or b['execution_authorized'] is not False):raise ValueError('Source correction binding/original drift')
    original=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(previous['child_id'],)).fetchone()[0])
    ops=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_ops WHERE run_id=? ORDER BY seq',(original['id'],))]
    if b['feedback']!=protocol.feedback(original,ops):raise ValueError('Source correction feedback drift')
    used={k:0 for k in queue.LIMITS}
    if len(row['operations'])>1:raise ValueError('Only one correction lineage permitted')
    for op in row['operations']:
        if op.get('kind')!='review' or op['child_id']!=row['child_id'] or op['reserved']!=amount() or op['state'] not in ('in_flight','confirmed'):raise ValueError('Source correction reservation drift')
        value=op['actual'] if op['state']=='confirmed' else op['reserved']
        if set(value)!=set(amount()) or any(type(v) is not int or not 0<=v<=amount()[k] for k,v in value.items()):raise ValueError('Source correction accounting drift')
        for k in used:used[k]+=value.get(k,0)
        if op['state']=='confirmed':
            found=db.execute('SELECT body FROM plan_runs WHERE id=?',(row['child_id'],)).fetchone()
            if found is None:raise ValueError('Source correction original missing')
            child=manifest.parse(found[0]);captured=originals(db,child['id'])
            if captured!=op['originals'] or captured['run_sha256']!=op['result_sha256'] or child['state'] in ('active','uncertain_operation') or child['state']!=op['child_state']:raise ValueError('Source correction result drift')
            if child['binding'].get('section')!=protocol.SECTION or child['binding'].get('source_feedback')!=b['feedback']:raise ValueError('Source correction child binding drift')
            if op['source_accepted'] and (child['state']!='reviewed_application_file' or child['findings'] or child['execution_authorized'] or child['product_tests_executed']):raise ValueError('Source correction false acceptance')
            childops=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_ops WHERE run_id=? ORDER BY seq',(child['id'],))]
            if value!=amount() and (value!={k:child[k] for k in amount()} or len(childops)!=child['calls'] or any(
                o['state']!='confirmed' or not o.get('result',{}).get('ok') or o['result'].get('done') is not True
                or o['result'].get('done_reason')!='stop' or o['result'].get('tool_calls') or o['result'].get('model_digest')!=child['binding']['digest']
                or any(type(o['result'].get(k)) is not int or not 0<=o['result'][k]<=o['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens')) for o in childops)):
                raise ValueError('Source correction unverifiable refund')
    if used!=row['used']:raise ValueError('Source correction counter drift')
    if row['state']=='reviewed_source' and (not row['operations'] or not row['operations'][0].get('source_accepted')):raise ValueError('Source correction requires original handoff')
    p=packet(row,parent,previous);due=p['through_round']-p['from_round']+1>=queue.REVIEW_EVERY
    expected={'packet':p,'sha256':manifest.identity(p)}
    if row['pending_audit'] and row['pending_audit']!=expected:raise ValueError('Source correction pending audit drift')
    if row['audit'] and (any(row['audit'][k]!=v for k,v in expected.items()) or row['audit']['decision'] not in ('continue','discrepancy')
        or not isinstance(row['audit']['notes'],str) or not 1<=len(row['audit']['notes'])<=3000):raise ValueError('Source correction audit drift')
    if due and not row['audit'] and (row['state']!='awaiting_flow_audit' or not row['pending_audit']):raise ValueError('Third complete attempt requires audit')
    return used


def add_usage(db,parent,total):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",(TABLE,)).fetchone():return total
    found=db.execute('SELECT body FROM phase3_source_corrections WHERE queue_id=?',(parent['id'],)).fetchone()
    if found:
        used=check(db,manifest.parse(found[0]),parent,base.load(db,parent['id']))
        total={k:v+used[k] for k,v in total.items()}
    return total


def create(journal,rid,previous_digest):
    suite=controller_gate.require_green()
    with worker_lock(journal.path,'increment-'+rid) as owned:
        if not owned:raise ValueError('Source worker busy')
        with journal.transaction() as db:
            tables(db);parent=queue.load(db,rid);previous=base.load(db,rid);base.check(db,previous,parent)
            if manifest.identity(previous)!=previous_digest:raise ValueError('Stale source correction parent')
            if previous['state']!='awaiting_discrepancy' or len(previous['operations'])!=1 or previous['operations'][0]['state']!='confirmed':raise ValueError('Only complete stopped source can be corrected')
            if previous['audit'] and previous['audit']['decision']!='continue':raise ValueError('Prior audit discrepancy must be resolved')
            old=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(previous['child_id'],)).fetchone()[0])
            oldops=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_ops WHERE run_id=? ORDER BY seq',(old['id'],))]
            if not oldops or len(oldops)!=old['calls'] or any(o['state']!='confirmed' or not o['result'].get('ok') or o['result'].get('done') is not True or o['result'].get('done_reason')!='stop' or o['result'].get('tool_calls') or o['result'].get('model_digest')!=old['binding']['digest']
                or any(type(o['result'].get(k)) is not int or not 0<=o['result'][k]<=o['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens')) for o in oldops):raise ValueError('Incomplete original cannot be corrected')
            b={'suite_identity':suite,'protocol':protocol.PROTOCOL,'previous_snapshot':copy.deepcopy(previous),'previous_sha256':previous_digest,
              'previous_originals':originals(db,old['id']),'parent_sha256':manifest.identity(parent),'parent_children':base.child_originals(db,parent),
              'input_identity':manifest.identity(preparation.load_bundle()[0]),'rules_sha256':manifest.identity(protocol.RULES),
              'acceptance_sha256':previous['binding']['acceptance_sha256'],'feedback':protocol.feedback(old,oldops),'limits':queue.LIMITS,'execution_authorized':False}
            found=db.execute('SELECT body FROM phase3_source_corrections WHERE queue_id=?',(rid,)).fetchone()
            if found:
                row=manifest.parse(found[0])
                if row['binding']!=b:raise ValueError('Existing correction binding drift; no fresh budget')
                return row
            cid=rid+'-geo-correction-r0'
            if db.execute('SELECT 1 FROM plan_runs WHERE id=?',(cid,)).fetchone():raise ValueError('Correction child ID already used')
            row={'schema':'agentapp.phase3-source-correction/1','queue_id':rid,'child_id':cid,'binding':b,'binding_sha256':manifest.identity(b),
              'state':'active','reason':'','used':{k:0 for k in queue.LIMITS},'operations':[],'events':[],
              'created_at':review.now(),'updated_at':review.now(),'source_only':True,'product_tests_executed':False,'full_product_acceptance':False,'audit':None,'pending_audit':None}
            check(db,row,parent,previous);db.execute('INSERT INTO phase3_source_corrections VALUES(?,?)',(rid,manifest.canonical(row)))
            save(db,row,{'kind':'source_correction.created','original_id':old['id']});return row


def settle(journal,rid):
    row=get(journal,rid)
    try:child=journal.get(row['child_id'])
    except ValueError:child=None
    if child is None or child['state'] in ('active','uncertain_operation') or any(o['state']=='in_flight' for o in journal.records(child['id'],'plan_ops')):
        with journal.transaction() as db:
            row=load(db,rid);check(db,row,queue.load(db,rid),base.load(db,rid))
            if row['state']!='uncertain_operation':row.update(state='uncertain_operation',reason='Full reservation retained; inspect originals, never replay');save(db,row,{'kind':'source_correction.uncertain'})
        return get(journal,rid)
    review.verify_binding(child);verified=workflow.verified_usage(journal,child);accepted=False
    if child['state']=='reviewed_application_file':
        try:handoff.consume(journal,child['id'],protocol.SECTION);accepted=True
        except ValueError:pass
    with journal.transaction() as db:
        row=load(db,rid);parent=queue.load(db,rid);previous=base.load(db,rid);check(db,row,parent,previous)
        op=row['operations'][0]
        if op['state']!='in_flight':return row
        captured=originals(db,child['id'])
        if captured['run_sha256']!=manifest.identity(child):raise ValueError('Source correction changed before settlement')
        actual={k:child[k] for k in amount()} if verified else amount()
        op.update(state='confirmed',actual=actual,result_sha256=manifest.identity(child),child_state=child['state'],source_accepted=accepted,originals=captured)
        row['used']={k:actual.get(k,0) for k in queue.LIMITS};row.update(state='reviewed_source' if accepted else 'awaiting_discrepancy',reason='Source only; PostgreSQL acceptance/composition/official data pending' if accepted else 'Source correction stopped')
        p=packet(row,parent,previous)
        if p['through_round']-p['from_round']+1>=queue.REVIEW_EVERY:row['pending_audit']={'packet':p,'sha256':manifest.identity(p)};row['state']='awaiting_flow_audit'
        check(db,row,parent,previous);save(db,row,{'kind':'source_correction.completed','source_only':True});return row


def run(journal,rid,caller=None,observer=None):
    with worker_lock(journal.path,'increment-'+rid) as owned:
        if not owned:return get(journal,rid)|{'busy':True}
        with journal.transaction() as db:
            row=load(db,rid);parent=queue.load(db,rid);previous=base.load(db,rid);check(db,row,parent,previous)
            if row['state'] not in ('active','uncertain_operation'):return row
            if controller_gate.require_green()!=row['binding']['suite_identity']:raise ValueError('Source correction controller drift')
            fresh=not row['operations']
            if fresh:
                total=base.aggregate(db,parent)
                if any(total[k]+amount().get(k,0)>queue.LIMITS[k] for k in total):row.update(state='blocked_budget',reason='Original shared cap exhausted');save(db,row);return row
                row['operations']=[{'child_id':row['child_id'],'kind':'review','reserved':amount(),'state':'in_flight'}]
                row['used']={k:amount().get(k,0) for k in queue.LIMITS};save(db,row,{'kind':'source_correction.reserved'})
        if fresh:
            try:
                seed=journal.get(row['binding']['feedback']['original_id'])['candidate']
                journal.create(row['child_id'],seed,section=protocol.SECTION,source_feedback=row['binding']['feedback'])
                review.run(journal,row['child_id'],caller=caller,observer=observer)
            except Exception:pass
        return settle(journal,rid)


def acknowledge_audit(journal,rid,digest,decision,notes):
    if decision not in ('continue','discrepancy') or not isinstance(notes,str) or not 1<=len(notes)<=3000:raise ValueError('Bad source correction audit')
    with worker_lock(journal.path,'increment-'+rid) as owned:
        if not owned:raise ValueError('Source worker busy')
        with journal.transaction() as db:
            row=load(db,rid);parent=queue.load(db,rid);previous=base.load(db,rid);check(db,row,parent,previous)
            if row['audit']:
                if row['audit']['sha256']==digest and row['audit']['decision']==decision and row['audit']['notes']==notes:return row
                raise ValueError('Audit replay drift')
            if not row['pending_audit'] or row['pending_audit']['sha256']!=digest:raise ValueError('Stale source correction audit')
            row['audit']=row['pending_audit']|{'decision':decision,'notes':notes};row['pending_audit']=None
            row['state']='reviewed_source' if decision=='continue' and row['operations'][0]['source_accepted'] else 'awaiting_discrepancy'
            save(db,row,{'kind':'source_correction.audited','decision':decision});return row


def export(journal,rid,path):
    row=get(journal,rid)
    with journal.transaction() as db:total=base.aggregate(db,queue.load(db,rid))
    child=None
    if row['operations']:
        try:child={'run':journal.get(row['child_id']),'operations':journal.records(row['child_id'],'plan_ops'),'events':journal.records(row['child_id'],'plan_events')}
        except ValueError:pass
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'correction':row,'shared_used':total,'original':child,'full_product_acceptance':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
