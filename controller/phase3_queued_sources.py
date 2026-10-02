"""Independent source preparation attached to the original contract queue.

No replacement queue, budget or rebinding: the stopped auth queue is immutable.
This extension supports only one distinct geography source task, not execution.
Its reservations share the parent's aggregate cap and retain uncertain children.
"""
import argparse
import copy
import json
from pathlib import Path

import controller_gate
import manifest
import phase3_geography_source as geography
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_prepare as preparation
import phase3_review as review
import phase3_source as source
import phase3_workflow as workflow
from worker_lock import worker_lock

TABLE='phase3_queued_sources'
SCOPE='official-geography'


def tables(db):db.execute('CREATE TABLE IF NOT EXISTS phase3_queued_sources(queue_id TEXT,scope TEXT,body TEXT NOT NULL,PRIMARY KEY(queue_id,scope))')


def load(db,queue_id):
    found=db.execute('SELECT body FROM phase3_queued_sources WHERE queue_id=? AND scope=?',(queue_id,SCOPE)).fetchone()
    if not found:raise ValueError('Unknown independent source task')
    return manifest.parse(found[0])


def get(journal,queue_id):
    with journal.transaction() as db:tables(db);return load(db,queue_id)


def child_originals(db,parent):
    originals={}
    for op in parent['operations']:
        if op['kind']!='review':raise ValueError('Independent source cannot overlap product resources')
        child=db.execute('SELECT body FROM plan_runs WHERE id=?',(op['child_id'],)).fetchone()
        if child is None:raise ValueError('Parent original missing')
        row=manifest.parse(child[0])
        ops=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_ops WHERE run_id=? ORDER BY seq',(op['child_id'],))]
        events=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_events WHERE run_id=? ORDER BY seq',(op['child_id'],))]
        originals[op['child_id']]={'run_sha256':manifest.identity(row),'ops_sha256':manifest.identity(ops),'events_sha256':manifest.identity(events)}
    return originals


def check(db,row,parent):
    binding=row['binding'];snapshot=binding['parent_snapshot']
    if (manifest.identity(binding)!=row['binding_sha256'] or manifest.identity(snapshot)!=binding['parent_sha256']
        or manifest.identity(parent)!=binding['parent_sha256'] or snapshot!=parent
        or binding['scope']!=SCOPE or binding['section']!=geography.SECTION or binding['path']!=geography.PATH
        or binding['execution_authorized'] is not False or binding['parent_children']!=child_originals(db,parent)
        or binding['source_protocol']!=geography.SOURCE or binding['rules_sha256']!=manifest.identity(geography.RULES)
        or binding['acceptance_sha256']!=manifest.identity(geography.ACCEPTANCE)
        or binding['review_every']!=queue.REVIEW_EVERY
        or snapshot['binding']['limits']!=queue.LIMITS
        or snapshot['binding']['contract_sha256']!=preparation.load_bundle()[0]['files']['contract.json']
        or snapshot['state']!='uncertain_operation' or any(o['kind']!='review' for o in snapshot['operations'])
        or binding['product_acceptance'] is not False or row['source_only'] is not True
        or row['product_tests_executed'] is not False or row['full_product_acceptance'] is not False
        or row['child_id']!=parent['id']+'-geo-source-r0'
        or row['queue_id']!=snapshot['id'] or row['scope']!=SCOPE):raise ValueError('Independent scope/parent/original drift')
    amounts={k:0 for k in queue.LIMITS}
    if len(row['operations'])>1:raise ValueError('Independent source duplicate operation')
    for op in row['operations']:
        expected={k:source.LIMITS[k] for k in ('calls','input_tokens','output_tokens')}|{'active_ms':source.LIMITS['active_seconds']*1000}
        if op['kind']!='review' or op['reserved']!=expected or op['child_id']!=row['child_id'] or op['state'] not in ('in_flight','confirmed'):
            raise ValueError('Independent reservation drift')
        value=op['actual'] if op['state']=='confirmed' else op['reserved']
        if set(value)!=set(expected) or any(type(v) is not int or not 0<=v<=expected[k] for k,v in value.items()):raise ValueError('Independent accounting drift')
        for k in amounts:amounts[k]+=value.get(k,0)
        if op['state']=='confirmed':
            found=db.execute('SELECT body FROM plan_runs WHERE id=?',(op['child_id'],)).fetchone()
            if found is None:raise ValueError('Independent source original missing')
            child=manifest.parse(found[0])
            if manifest.identity(child)!=op['result_sha256']:raise ValueError('Independent source original changed')
            for table,key in (('plan_ops','ops_sha256'),('plan_events','events_sha256')):
                records=[manifest.parse(v[0]) for v in db.execute(f'SELECT body FROM {table} WHERE run_id=? ORDER BY seq',(op['child_id'],))]
                if manifest.identity(records)!=op[key]:raise ValueError('Independent source operation/event original changed')
            if (child['binding'].get('section')!=geography.SECTION or child['state']=='uncertain_operation'
                or op['child_state']!=child['state']):raise ValueError('Independent source completion drift')
            if op.get('source_accepted') and (child['state']!='reviewed_application_file' or child['findings']
                or child['execution_authorized'] or child['product_tests_executed']):raise ValueError('Independent source acceptance drift')
            if value!=expected:
                ops=[manifest.parse(v[0]) for v in db.execute('SELECT body FROM plan_ops WHERE run_id=? ORDER BY seq',(op['child_id'],))]
                if (value!={k:child[k] for k in expected} or len(ops)!=child['calls'] or not ops or any(o['state']!='confirmed' or not o.get('result',{}).get('ok')
                    or o['result'].get('done') is not True or o['result'].get('done_reason')!='stop'
                    or o['result'].get('tool_calls') or any(type(o['result'].get(k)) is not int
                        or not 0<=o['result'][k]<=o['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens'))
                    or o['result'].get('model_digest')!=child['binding']['digest'] for o in ops)):
                    raise ValueError('Independent unverifiable usage refund')
    if amounts!=row['used']:raise ValueError('Independent counters drift')
    if row['state']=='reviewed_source' and (not row['operations'] or not row['operations'][0].get('source_accepted')):raise ValueError('Independent acceptance requires original handoff')
    verify_audit(row,parent)
    return amounts


def audit_packet(row,parent):
    rounds=queue.rounds(parent)+[o for o in row['operations'] if o['state']=='confirmed']
    start=parent['last_audited_round']
    total={k:parent['used'][k]+row['used'][k] for k in queue.LIMITS}
    return {'schema':'agentapp.phase3-independent-source-audit/1','queue_id':parent['id'],
      'binding_sha256':row['binding_sha256'],'from_round':start+1,'through_round':len(rounds),
      'originals':[{'id':o['child_id'],'sha256':o['result_sha256'],'state':o['child_state']} for o in rounds[start:]],
      'parent_children':row['binding']['parent_children'],'used':total,'product_tests_executed':False,'full_product_acceptance':False}


def verify_audit(row,parent):
    packet=audit_packet(row,parent);due=packet['through_round']-parent['last_audited_round']>=queue.REVIEW_EVERY
    pending=row.get('pending_audit');audit=row.get('audit')
    if pending and pending!={'packet':packet,'sha256':manifest.identity(packet)}:raise ValueError('Independent pending audit drift')
    if audit and (audit['packet']!=packet or audit['sha256']!=manifest.identity(packet)
        or audit['decision'] not in ('continue','discrepancy') or not isinstance(audit['notes'],str)
        or not 1<=len(audit['notes'])<=3000):raise ValueError('Independent audit record drift')
    if due and not audit and (not pending or row['state']!='awaiting_flow_audit'):raise ValueError('Third completed round requires review')


def aggregate(db,parent):
    """Called by both old reservations and this extension, in the same DB tx."""
    used=copy.deepcopy(parent['used'])
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone():return used
    for value in db.execute('SELECT body FROM phase3_queued_sources WHERE queue_id=?',(parent['id'],)):
        row=manifest.parse(value[0]);spent=check(db,row,parent)
        for k in used:used[k]+=spent[k]
    from phase3_source_corrections import add_usage
    used=add_usage(db,parent,used)
    from phase3_source_revalidations import add_usage as revalidation_usage
    used=revalidation_usage(db,parent,used)
    if any(v>queue.LIMITS[k] for k,v in used.items()):raise ValueError('Shared queue aggregate exceeded')
    return used


def save(db,row,event=None):
    row['updated_at']=review.now()
    if event:row['events'].append({'at':row['updated_at'],'seq':len(row['events']),**event})
    db.execute('UPDATE phase3_queued_sources SET body=? WHERE queue_id=? AND scope=?',(manifest.canonical(row),row['queue_id'],SCOPE))


def create(journal,queue_id,parent_digest):
    suite=controller_gate.require_green();lock,_,_=preparation.load_bundle()
    with journal.transaction() as db:
        tables(db);parent=queue.load(db,queue_id)
        if manifest.identity(parent)!=parent_digest:raise ValueError('Stale parent source preparation digest')
        if parent['state']!='uncertain_operation' or any(o['kind']!='review' for o in parent['operations']):raise ValueError('Only source uncertainty permits independent preparation')
        queue.verify_audits(parent)
        if len(queue.rounds(parent))-parent['last_audited_round']>=queue.REVIEW_EVERY:raise ValueError('Original queue periodic review required before new work')
        item=next(i for i in parent['items'] if i['id']==SCOPE)
        if item['depends_on'] or item['state']!='unsupported':raise ValueError('Scope is dependent or already executed')
        binding={'suite_identity':suite,'input_identity':manifest.identity(lock),'scope':SCOPE,'section':geography.SECTION,
          'path':geography.PATH,'parent_snapshot':copy.deepcopy(parent),'parent_sha256':parent_digest,
          'parent_children':child_originals(db,parent),'execution_authorized':False,'product_acceptance':False,
          'source_protocol':geography.SOURCE,'rules_sha256':manifest.identity(geography.RULES),
          'acceptance_sha256':manifest.identity(geography.ACCEPTANCE),
          'review_every':queue.REVIEW_EVERY}
        prior=db.execute('SELECT body FROM phase3_queued_sources WHERE queue_id=? AND scope=?',(queue_id,SCOPE)).fetchone()
        if prior:
            row=manifest.parse(prior[0])
            if row['binding']!=binding:raise ValueError('Existing preparation binding drift; no fresh budget')
            return row
        if db.execute('SELECT 1 FROM plan_runs WHERE id=?',(queue_id+'-geo-source-r0',)).fetchone():raise ValueError('Independent child ID already used')
        row={'schema':'agentapp.phase3-queued-source/1','queue_id':queue_id,'scope':SCOPE,'state':'active','reason':'',
          'child_id':queue_id+'-geo-source-r0','binding':binding,'binding_sha256':manifest.identity(binding),
          'used':{k:0 for k in queue.LIMITS},'operations':[],'events':[],'created_at':review.now(),'updated_at':review.now(),
          'source_only':True,'product_tests_executed':False,'full_product_acceptance':False,'audit':None,'pending_audit':None}
        check(db,row,parent);aggregate(db,parent)
        db.execute('INSERT INTO phase3_queued_sources VALUES(?,?,?)',(queue_id,SCOPE,manifest.canonical(row)))
        save(db,row,{'kind':'independent_source.created','parent_uncertainty_preserved':True})
        return row


def settle_original(journal,queue_id):
    row=get(journal,queue_id)
    try:child=journal.get(row['child_id'])
    except ValueError:child=None
    if child is None or child['state'] in ('active','uncertain_operation') or any(o['state']=='in_flight' for o in journal.records(child['id'],'plan_ops')):
        with journal.transaction() as db:
            current=load(db,queue_id);check(db,current,queue.load(db,queue_id))
            if current['state']!='uncertain_operation':
                current.update(state='uncertain_operation',reason='Independent result uncertain; full reservation retained; never replay inference')
                save(db,current,{'kind':'independent_source.uncertain','child_id':row['child_id']})
        return get(journal,queue_id)
    review.verify_binding(child)
    if child['binding'].get('section')!=geography.SECTION or child['binding'].get('initial_proposal')!='absent-geography-module/1':raise ValueError('Independent child binding drift')
    verified=workflow.verified_usage(journal,child)
    amount=row['operations'][0]['reserved'];actual={k:child[k] for k in amount} if verified else amount
    accepted=False
    original_ops=journal.records(child['id'],'plan_ops');original_events=journal.records(child['id'],'plan_events')
    if child['state']=='reviewed_application_file':
        try:
            consumed,ops,_,_=handoff.consume(journal,child['id'],geography.SECTION)
            accepted=consumed==child and ops==original_ops
        except ValueError:pass
    with journal.transaction() as db:
        current=load(db,queue_id);parent=queue.load(db,queue_id);check(db,current,parent)
        op=current['operations'][0]
        if op['state']!='in_flight':return current
        op.update(state='confirmed',actual=actual,result_sha256=manifest.identity(child),child_state=child['state'],source_accepted=accepted,
          ops_sha256=manifest.identity(original_ops),events_sha256=manifest.identity(original_events))
        current['used']={k:actual.get(k,0) for k in queue.LIMITS}
        current.update(state='reviewed_source' if accepted else 'awaiting_discrepancy',reason='Geography source reviewed; PostgreSQL/official catalogue/UI acceptance pending' if accepted else 'Original geography review stopped')
        packet=audit_packet(current,parent)
        if packet['through_round']-parent['last_audited_round']>=queue.REVIEW_EVERY:
            current['pending_audit']={'packet':packet,'sha256':manifest.identity(packet)};current['state']='awaiting_flow_audit'
        check(db,current,parent);save(db,current,{'kind':'independent_source.completed','child_id':child['id'],'source_only':True})
    return get(journal,queue_id)


def acknowledge_audit(journal,queue_id,digest,decision,notes):
    if decision not in ('continue','discrepancy') or not isinstance(notes,str) or not 1<=len(notes)<=3000:raise ValueError('Independent audit decision invalid')
    with worker_lock(journal.path,'increment-'+queue_id) as owned:
        if not owned:raise ValueError('Independent worker busy')
        with journal.transaction() as db:
            row=load(db,queue_id);parent=queue.load(db,queue_id);check(db,row,parent)
            expected={'sha256':digest,'decision':decision,'notes':notes}
            if row['audit']:
                if all(row['audit'][k]==v for k,v in expected.items()):return row
                raise ValueError('Independent audit replay drift')
            if not row['pending_audit'] or row['pending_audit']['sha256']!=digest:raise ValueError('Stale independent audit')
            row['audit']=row['pending_audit']|expected;row['pending_audit']=None
            row['state']='reviewed_source' if decision=='continue' and row['operations'][0]['source_accepted'] else 'awaiting_discrepancy'
            check(db,row,parent);save(db,row,{'kind':'independent_source.audit','decision':decision})
            return row


def run(journal,queue_id,caller=None,observer=None):
    with worker_lock(journal.path,'increment-'+queue_id) as owned:
        if not owned:return get(journal,queue_id)|{'busy':True}
        with journal.transaction() as db:
            row=load(db,queue_id);parent=queue.load(db,queue_id)
            check(db,row,parent)
            if row['state'] not in ('active','uncertain_operation'):return row
            if (controller_gate.require_green()!=row['binding']['suite_identity']
                or manifest.identity(preparation.load_bundle()[0])!=row['binding']['input_identity']):raise ValueError('Independent controller/input drift')
            if row['operations']:
                fresh=False
            else:
                spent=aggregate(db,parent)
                amount={k:source.LIMITS[k] for k in ('calls','input_tokens','output_tokens')}|{'active_ms':source.LIMITS['active_seconds']*1000}
                if any(spent[k]+amount.get(k,0)>queue.LIMITS[k] for k in spent):
                    row.update(state='blocked_budget',reason='Original shared queue cap blocks independent source');save(db,row);return row
                row['operations']=[{'kind':'review','child_id':row['child_id'],'state':'in_flight','reserved':amount}]
                row['used']={k:amount.get(k,0) for k in queue.LIMITS}
                save(db,row,{'kind':'independent_source.reserved','child_id':row['child_id']});fresh=True
        if fresh:
            try:
                journal.create(row['child_id'],{'path':geography.PATH,'content':''},section=geography.SECTION,initial_proposal=True)
                review.run(journal,row['child_id'],caller=caller,observer=observer)
            except Exception:pass
        # Includes restart after a complete child but before settlement. Inspection
        # may settle its original result; it never dispatches that child again.
        return settle_original(journal,queue_id)


def export(journal,queue_id,path):
    row=get(journal,queue_id)
    with journal.transaction() as db:total=aggregate(db,queue.load(db,queue_id))
    original=None
    if row['operations']:
        try:original={'run':journal.get(row['child_id']),'operations':journal.records(row['child_id'],'plan_ops'),'events':journal.records(row['child_id'],'plan_events')}
        except ValueError:pass
    value={'source_task':row,'shared_queue_used':total,'original':original,'product_tests_executed':False,'full_product_acceptance':False}
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--queue-id',required=True)
    parser.add_argument('--parent-digest');parser.add_argument('--export',type=Path,required=True)
    args=parser.parse_args(argv);journal=review.Journal()
    if args.parent_digest:create(journal,args.queue_id,args.parent_digest)
    row=run(journal,args.queue_id);export(journal,args.queue_id,args.export)
    print(json.dumps({'state':row['state'],'reason':row['reason'],'used':row['used'],'source_only':True}));return 0 if row['state']=='reviewed_source' else 2


if __name__=='__main__':raise SystemExit(main())
