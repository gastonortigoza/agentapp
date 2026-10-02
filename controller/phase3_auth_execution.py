"""Durable auth operation ledger. Resource materialization is not yet wired.

Reserve before every external operation. An interrupted or uncertain operation
retains its reservation and cannot be sent again, even under another run ID.
The public directory executor and exhausted profile workflow are untouched.
"""
import copy
import re
import secrets

import manifest
import phase3_auth_sandbox as sandbox

SCOPE='auth-session-core-v1'
LIMITS={'executions':3,'operations':240,'active_ms':2700000}


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS phase3_auth_budgets(scope TEXT PRIMARY KEY,body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS phase3_auth_executions(id TEXT PRIMARY KEY,binding TEXT UNIQUE NOT NULL,body TEXT NOT NULL)')


def load(db,run_id):
    tables(db);record=db.execute('SELECT body FROM phase3_auth_executions WHERE id=?',(run_id,)).fetchone()
    if not record:raise ValueError('Unknown auth execution')
    row=manifest.parse(record[0])
    if manifest.identity(row['manifest'])!=row['manifest_sha256']:raise ValueError('Auth execution binding drift')
    return row


def get(journal,run_id):
    with journal.transaction() as db:return load(db,run_id)


def save(db,row):
    db.execute('UPDATE phase3_auth_executions SET body=? WHERE id=?',(manifest.canonical(row),row['id']))


def budget(db):
    tables(db);found=db.execute('SELECT body FROM phase3_auth_budgets WHERE scope=?',(SCOPE,)).fetchone()
    if not found:
        value={'scope':SCOPE,'limits':copy.deepcopy(LIMITS),'used':{'executions':0,'operations':0,'active_ms':0}}
        db.execute('INSERT INTO phase3_auth_budgets VALUES(?,?)',(SCOPE,manifest.canonical(value)))
        return value
    value=manifest.parse(found[0])
    if value.get('scope')!=SCOPE or value.get('limits')!=LIMITS or set(value.get('used',{}))!=set(LIMITS):raise ValueError('Auth aggregate budget drift')
    if any(type(v) is not int or v<0 or v>LIMITS[k] for k,v in value['used'].items()):raise ValueError('Auth aggregate accounting drift')
    return value


def save_budget(db,value):db.execute('UPDATE phase3_auth_budgets SET body=? WHERE scope=?',(manifest.canonical(value),SCOPE))


def create(journal,run_id,concrete,review_ids,workspace,tls_public):
    if not isinstance(run_id,str) or not re.fullmatch('[a-z0-9-]{1,100}',run_id):raise ValueError('Auth execution ID denied')
    bound=sandbox.validate(concrete,journal,review_ids,workspace,tls_public)
    digest=manifest.identity(bound)
    # Controller/TLS updates cannot grant another attempt for unchanged product bytes.
    product_identity=manifest.identity({'scope':SCOPE,'contract':bound['binding']['contract_sha256'],
                                     'files':bound['binding']['workspace']['files']})
    with journal.transaction() as db:
        tables(db);prior=db.execute('SELECT id,body FROM phase3_auth_executions WHERE id=? OR binding=?',(run_id,product_identity)).fetchall()
        if prior:
            if len(prior)!=1 or prior[0][0]!=run_id:raise ValueError('Auth code already has a durable execution; do not reset it')
            row=load(db,run_id)
            if row['manifest_sha256']!=digest:raise ValueError('Existing auth execution drift')
            return row
        aggregate=budget(db)
        if aggregate['used']['executions']>=LIMITS['executions']:raise ValueError('Auth execution budget exhausted')
        aggregate['used']['executions']+=1;save_budget(db,aggregate)
        row={'id':run_id,'scope':SCOPE,'state':'awaiting_resource_materializer','manifest':bound,'manifest_sha256':digest,
             'product_identity':product_identity,'nonce':secrets.token_hex(16),'operations':[],
             'used':{'operations':0,'active_ms':0},'product_tests_executed':False,'runtime_ready':False,
             'reason':'PG/app/test/gateway/browser materializer and end-to-end runtime qualification pending'}
        db.execute('INSERT INTO phase3_auth_executions VALUES(?,?,?)',(run_id,product_identity,manifest.canonical(row)))
    return row


def reserve(journal,run_id,key,kind,timeout_ms,public_request):
    if (not isinstance(key,str) or not re.fullmatch('[a-z0-9-]{1,80}',key)
        or kind not in ('setup','stage','cleanup') or type(timeout_ms) is not int or not 1<=timeout_ms<=300000):raise ValueError('Auth operation bound denied')
    if (not isinstance(public_request,dict) or set(public_request)!={'action','resource'}
        or not isinstance(public_request['action'],str) or not re.fullmatch('[a-z0-9-]{1,80}',public_request['action'])
        or not isinstance(public_request['resource'],str) or not re.fullmatch('age50-auth-[a-z0-9-]{1,60}|sha256:[0-9a-f]{64}',public_request['resource'])):
        raise ValueError('Only secret-free auth operation metadata may be persisted')
    with journal.transaction() as db:
        row=load(db,run_id)
        if any(op['key']==key for op in row['operations']):raise ValueError('Auth operation already recorded; do not resend')
        if any(op['state']=='in_flight' for op in row['operations']):raise ValueError('Auth operation still in flight; reconcile interruption first')
        if kind!='cleanup' and row['state'] in ('uncertain_operation','failed_operation','blocked_budget','qualified_session_core'):
            raise ValueError('Auth execution cannot continue ordinary work')
        if kind=='stage' and row['runtime_ready'] is not True:raise ValueError('Auth resource materializer is not qualified')
        aggregate=budget(db);limit=sandbox.POLICY['cleanup_ms'] if kind=='cleanup' else sandbox.POLICY['total_active_ms']
        used=sum(op['charged_ms'] for op in row['operations'] if (op['kind']=='cleanup')==(kind=='cleanup'))
        if used+timeout_ms>limit or row['used']['operations']>=sandbox.POLICY['operation_limit'] or aggregate['used']['active_ms']+timeout_ms>LIMITS['active_ms'] or aggregate['used']['operations']>=LIMITS['operations']:
            # Reservation rejection does not erase accepted records or enable a new ID.
            row.update(state='blocked_budget',reason='Persisted auth operation/aggregate bound exhausted');save(db,row)
            return None
        op={'seq':len(row['operations']),'key':key,'kind':kind,'state':'in_flight','reserve_ms':timeout_ms,
            'charged_ms':timeout_ms,'public_request':copy.deepcopy(public_request)}
        row['operations'].append(op);row['used']['operations']+=1;row['used']['active_ms']+=timeout_ms
        aggregate['used']['operations']+=1;aggregate['used']['active_ms']+=timeout_ms
        save(db,row);save_budget(db,aggregate)
    return copy.deepcopy(op)


def settle(journal,run_id,key,outcome):
    # Raw output may contain fixture credentials. Store only bounded digests;
    # a future materializer must persist sanitized test reports separately.
    if (not isinstance(outcome,dict) or set(outcome)!={'exit_code','duration_ms','timed_out','truncated','stdout_sha256','stderr_sha256'}
        or type(outcome['exit_code']) is not int or type(outcome['duration_ms']) is not int or outcome['duration_ms']<0
        or any(type(outcome[k]) is not bool for k in ('timed_out','truncated'))
        or any(not isinstance(outcome[k],str) or not re.fullmatch('[a-f0-9]{64}',outcome[k]) for k in ('stdout_sha256','stderr_sha256'))):raise ValueError('Auth operation outcome denied')
    with journal.transaction() as db:
        row=load(db,run_id);found=[op for op in row['operations'] if op['key']==key]
        if len(found)!=1 or found[0]['state']!='in_flight':raise ValueError('Auth operation missing/already settled')
        op=found[0];uncertain=outcome['timed_out'] or outcome['truncated'] or outcome['duration_ms']>op['reserve_ms']
        op.update(outcome=copy.deepcopy(outcome),state='uncertain' if uncertain else 'confirmed')
        if not uncertain:
            refund=op['charged_ms']-outcome['duration_ms'];op['charged_ms']=outcome['duration_ms'];row['used']['active_ms']-=refund
            aggregate=budget(db);aggregate['used']['active_ms']-=refund;save_budget(db,aggregate)
        if uncertain:row.update(state='uncertain_operation',reason='Outcome uncertain; retain reservation, reconcile read-only, never replay')
        elif outcome['exit_code']!=0:row.update(state='failed_operation',reason='Confirmed auth operation failed; preserve originals')
        save(db,row)
    return row


def recover_interrupted(journal,run_id):
    """Never turn an old in-flight operation into a fresh dispatch."""
    with journal.transaction() as db:
        row=load(db,run_id)
        if any(op['state']=='in_flight' for op in row['operations']):
            row.update(state='uncertain_operation',reason='Interrupted operation preserved; read-only resource reconciliation required')
            save(db,row)
        return row


def verify_container(description,kind,run_id,nonce,image_id,namespace=None,install=False):
    cfg=description['Config'];host=description['HostConfig'];policy=sandbox.POLICY['node' if kind in ('app','api_test') else kind]
    argv=sandbox.container_argv(kind,'age50-auth-inspection',sandbox.directory.POSTGRES if kind=='postgres' else image_id,run_id,nonce,namespace,install)
    expected_tmpfs={argv[i+1].split(':',1)[0]:argv[i+1].split(':',1)[1] for i,a in enumerate(argv) if a=='--tmpfs'}
    expected_network='none' if namespace is None else 'container:'+namespace
    if (description['Image']!=image_id or cfg.get('Labels',{}).get('agentapp.auth.run')!=run_id
        or cfg.get('Labels',{}).get('agentapp.auth.nonce')!=nonce or cfg.get('User')!=('999:999' if kind=='postgres' else '1000:1000')
        or host.get('NetworkMode')!=expected_network or not host.get('ReadonlyRootfs') or host.get('Privileged')
        or host.get('Binds') or host.get('PortBindings') or host.get('CapAdd') or host.get('Devices')
        or host.get('CapDrop')!=['ALL'] or host.get('SecurityOpt')!=['no-new-privileges']
        or host.get('Memory')!=int(policy['memory'].removesuffix('m'))*1024*1024 or host.get('MemorySwap')!=host.get('Memory')
        or host.get('NanoCpus')!=round(float(policy['cpu'])*1e9) or host.get('PidsLimit')!=int(policy['pids'])
        or host.get('Tmpfs')!=expected_tmpfs or any(m.get('Type')!='tmpfs' for m in description.get('Mounts',[]))
        or host.get('LogConfig',{}).get('Type')!='none'):
        raise ValueError('Effective auth isolation differs from frozen policy')
    env=cfg.get('Env',[])
    allowed={'HOME','CI','NPM_CONFIG_USERCONFIG','PATH','NODE_VERSION','YARN_VERSION','PG_MAJOR','PG_VERSION','PG_SHA256','LANG','GOSU_VERSION','PGDATA','POSTGRES_DB'}|set(sandbox.POLICY['credentials'].get(kind,[]))
    if any(not isinstance(v,str) or v.split('=',1)[0] not in allowed for v in env):raise ValueError('Foreign auth worker credentials')
    if kind!='gateway' and any('AUTH_TLS_' in v for v in env):raise ValueError('Private TLS key must remain in gateway')
    if kind!='api_test' and any('AUTH_FIXTURE_DATABASE_URL=' in v for v in env):raise ValueError('Fixture DB credentials must remain in test worker')
    return True
