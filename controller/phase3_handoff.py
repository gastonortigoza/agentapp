"""Consume original review evidence at the executor boundary, without dispatch.

The accepted phase3 policy is documentary. This gate cannot turn it into an
execution capability. A future supported sandbox must consume the same current
binding before any dispatch; hashes here do not establish product correctness.
"""
import copy
import hashlib
import os
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import controller_gate
import manifest
import phase3_contract as contract
import phase3_prepare as preparation
import phase3_review as review

KINDS=('plan',*contract.SECTIONS)
MAX_FILES=200
MAX_BYTES=8*1024*1024


def finding(code,pointer,fix):
    return {'code':code,'pointer':pointer,'fix':fix}

def verify_chain(row,ops,lock,documents):
    expected=row['binding']['seed_sha256']
    if row['calls']!=len(ops) or row['corrections']!=sum(op['role']=='developer' for op in ops):
        raise ValueError('Review accounting drift')
    for seq,op in enumerate(ops):
        result=op.get('result',{});request=op.get('request',{})
        stage=row|{'candidate':op['candidate'],'candidate_sha256':op['candidate_sha256']}
        if (op['seq']!=seq or op['candidate_sha256']!=expected or
            manifest.identity(op['candidate'])!=expected or
            not result.get('ok') or result.get('done') is not True or
            result.get('done_reason')!='stop' or result.get('tool_calls') or
            result.get('model_digest')!=row['binding']['digest'] or
            any(type(result.get(k)) is not int or not 0<=result[k]<=op['reserve_'+k.split('_')[0]]
                for k in ('input_tokens','output_tokens')) or
            manifest.identity(request)!=op.get('request_sha256') or
            request.get('model')!=row['binding']['model'] or request.get('digest')!=row['binding']['digest'] or
            request.get('format')!=review.output_schema(stage,op,lock,documents) or
            result.get('prompt_sha256')!=manifest.identity(request.get('messages'))):
            raise ValueError('Review original/request/completion drift')
        parsed=manifest.parse(result['text'])
        if op['role']=='reviewer':
            if review.checked_review(stage,parsed,documents)!=op.get('validated_review'):
                raise ValueError('Review citation drift')
        elif op['role']=='developer':
            Draft202012Validator(review.output_schema(stage,op,lock,documents)).validate(parsed)
            expected=manifest.identity(parsed)
        else:raise ValueError('Unsupported review role')
    if expected!=row['candidate_sha256']:raise ValueError('Corrected candidate lineage drift')


def consume(journal,run_id,kind):
    row=journal.get(run_id)
    expected_state='reviewed_plan' if kind=='plan' else 'reviewed_contract_section'
    expected_scope='file_plan_structural_review' if kind=='plan' else 'contract_section_document_review'
    if kind=='application':expected_state,expected_scope='reviewed_application_file','application_file_review'
    if (row['state']!=expected_state or row['scope']!=expected_scope or
        row['binding'].get('section')!=(None if kind=='plan' else kind) or
        'expected_checks' in row['binding'] or row['findings']):
        raise ValueError('review_not_accepted:'+kind)
    lock,documents=review.verify_binding(row)
    ops=journal.records(run_id,'plan_ops')
    if not ops or any(op['state']!='confirmed' for op in ops):
        raise ValueError('review_operation_uncertain:'+kind)
    verify_chain(row,ops,lock,documents)
    final=ops[-1];result=final.get('result',{})
    if (final['role']!='reviewer' or final['candidate']!=row['candidate'] or
        final['candidate_sha256']!=row['candidate_sha256'] or
        manifest.identity(final['candidate'])!=final['candidate_sha256'] or
        not result.get('ok') or result.get('done') is not True or
        result.get('done_reason')!='stop' or result.get('tool_calls') or
        result.get('model_digest')!=row['binding']['digest'] or
        any(type(result.get(k)) is not int or not 0<=result[k]<=final['reserve_'+k.split('_')[0]]
            for k in ('input_tokens','output_tokens'))):
        raise ValueError('review_completion_invalid:'+kind)
    checked=review.checked_review(row,manifest.parse(result['text']),documents)
    if (checked!=final.get('validated_review') or checked['findings'] or
        any(c['passed'] is not True for c in checked['checks'].values()) or
        review.candidate_defects(row,lock,documents)):
        raise ValueError('review_evidence_invalid:'+kind)
    return row,ops,lock,documents


def inventory(workspace,plan):
    """Bound every application byte; reject extra files and links before reading."""
    root=Path(workspace)
    if root.is_symlink() or root.is_junction():raise ValueError('Workspace link')
    import executor
    root=executor.within_workspace(root)
    expected={entry['path'] for entry in plan['files']}
    hashes={};failures=[];total=0;count=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        # Count directories too, so arbitrarily deep/empty inputs are bounded.
        count+=len(dirs)+len(files)
        if count>MAX_FILES:raise ValueError('Workspace file/directory limit')
        for name in dirs:
            path=Path(directory)/name
            relative=path.relative_to(root).as_posix()
            if path.is_symlink() or path.is_junction():raise ValueError('Workspace link')
            preparation.plan_path(relative+'/synthetic/bounded-file' if relative=='fixtures' else relative+'/bounded-file')
        for name in files:
            path=Path(directory)/name
            relative=path.relative_to(root).as_posix()
            preparation.plan_path(relative)
            if path.is_symlink() or path.is_junction() or not path.is_file():raise ValueError('Workspace link or special file')
            if relative not in expected:
                failures.append(finding('unplanned_file','/files/'+relative,'Review a new bounded file plan before consuming additional files.'))
            size=path.stat().st_size;total+=size
            if total>MAX_BYTES:raise ValueError('Workspace byte limit')
            data=path.read_bytes()
            if len(data)!=size:raise ValueError('Workspace changed during read')
            hashes[relative]=hashlib.sha256(data).hexdigest()
    for name in sorted(expected-hashes.keys()):
        failures.append(finding('planned_file_missing','/files/'+name,'Supply the reviewed application file, including every dependency lock.'))
    return {'root':str(root),'files':hashes,'bytes':total},failures


def assemble(journal,reviews,workspace):
    if (not isinstance(reviews,dict) or set(reviews)!=set(KINDS) or
        any(not isinstance(v,str) for v in reviews.values()) or len(set(reviews.values()))!=len(KINDS)):
        raise ValueError('Supply one distinct original review ID for plan and each contract section')
    suite=controller_gate.require_green();receipts={};rows={};identity=None
    for kind in KINDS:
        row,ops,lock,documents=consume(journal,reviews[kind],kind)
        if identity is not None and identity!=row['binding']['input_identity']:
            raise ValueError('Mixed contract input identities')
        identity=row['binding']['input_identity'];rows[kind]=row
        receipts[kind]={'run_id':row['id'],'row_sha256':manifest.identity(row),'operations_sha256':manifest.identity(ops)}
    complete=copy.deepcopy(documents['contract.json'])
    for section in contract.SECTIONS:complete[section]=rows[section]['candidate']
    Draft202012Validator(documents['contract.schema.json']).validate(complete)
    # Neighbour references are checked on the assembled candidate, not on frozen
    # neighbours from a different run. Unreviewed narrative sections stay pinned.
    assembled=documents|{'contract.json':complete}
    defects=[]
    for section in contract.SECTIONS:
        for defect in contract.defects(complete[section],section,assembled):
            defects.append(finding(defect['issue'],'/'+section+defect['pointer'],defect['fix']))
    dates=complete['subscriptions']['dates']
    for field in dates:
        if field not in complete['api']['dtos']['Subscription'] or field not in complete['data']['tables']['subscriptions']['columns']:
            defects.append(finding('subscription_date_binding','/subscriptions/dates','Bind protocol dates to both API DTO and persisted columns.'))
    protocol=complete['subscriptions']
    table=complete['data']['tables'].get(protocol['lock_table'],{})
    # user_id is the authenticated owner identifier, mapped by the persisted FK
    # to users.id; it need not be a literal column named user_id in users.
    lock_reference=protocol['lock_key']+' REFERENCES '+protocol['lock_table']+'(id)'
    relationships=complete['data']['tables']['subscriptions']['foreign_keys']
    if 'id' not in table.get('columns',{}) or not any(fk==lock_reference or fk.startswith(lock_reference+' ON DELETE ') for fk in relationships):
        defects.append(finding('subscription_lock_binding','/subscriptions/lock_key','Lock an existing declared owner row using its actual column.'))
    if defects:raise ValueError('Assembled contract inconsistent: '+','.join(sorted({d['code'] for d in defects})))
    plan=preparation.validate_plan(rows['plan']['candidate'],lock,documents)
    files,failures=inventory(workspace,plan)
    binding={'suite_identity':suite,'input_identity':identity,'reviews':receipts,
             'contract_sha256':manifest.identity(complete),'plan_sha256':manifest.identity(plan),
             'policy_sha256':lock['files']['policy.json'],'workspace':files}
    # Re-read the durable evidence after assembling/reading files.
    for kind in KINDS:
        row,ops,_,_=consume(journal,reviews[kind],kind)
        if (manifest.identity(row)!=receipts[kind]['row_sha256'] or
            manifest.identity(ops)!=receipts[kind]['operations_sha256']):raise ValueError('Review changed during preflight')
    if inventory(workspace,plan)[0]!=files:raise ValueError('Workspace changed during preflight')
    if controller_gate.require_green()!=suite:raise ValueError('Controller changed during preflight')
    return {'schema':'agentapp.phase3-execution-preflight/1','binding':binding,
            'binding_sha256':manifest.identity(binding),'contract':complete,'plan':plan,
            'findings':failures,'execution_authorized':False,'product_tests_executed':False}


def preflight(journal,preflight_id,reviews,workspace):
    if not re.fullmatch('[a-z0-9-]{1,100}',preflight_id):raise ValueError('Bad preflight ID')
    # Failed evidence validation never creates or overwrites an approval receipt.
    result=assemble(journal,reviews,workspace)
    result.update(id=preflight_id,state='blocked_execution_policy',
                  reason='Frozen documentary policy denies generated code execution; no supported phase3 sandbox manifest.')
    result['findings'].append(finding('documentary_policy','/manifest/enabled',result['reason']))
    with journal.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS phase3_preflights(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        record=db.execute('SELECT body FROM phase3_preflights WHERE id=?',(preflight_id,)).fetchone()
        if record:
            previous=manifest.parse(record[0])
            if previous!=result:raise ValueError('Preflight drift: preserve original evidence and repeat affected checks in a new run')
            return previous
        db.execute('INSERT INTO phase3_preflights VALUES(?,?)',(preflight_id,manifest.canonical(result)))
    return result
