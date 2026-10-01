"""Durable state updates for controller-owned planner tickets only.

Linear MCP has no advertised state compare-and-swap. Revisions are checked
before dispatch and results read back, but concurrent external state edits
cannot be excluded atomically. No description or other fields are written.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import controller_gate
import manifest
import integration_linear as linear
from integration_cli import failure_reason
from integration_sync import SyncStore, RevisionConflict, digest
from local_control import require_evidence
from worker_lock import worker_lock

STATES = {
    'In Progress': ('8d552444-a4af-49ab-8652-00793960d362', 'started'),
    'Done': ('be74997b-b563-41ad-bd4a-fc711210c382', 'completed'),
}


def target_for_run(runs, run_id):
    row = runs.get(run_id)
    contract = json.loads(row['manifest'])
    if linear.PROJECT not in contract['allowed_resources']['linear_projects']:
        return row, None
    if row['execution_kind'] != 'local' or manifest.identity(contract) != row['manifest_hash']:
        raise RevisionConflict('Invalid run contract')
    if row['state'] in {'implementing', 'validating', 'awaiting_review'}:
        return row, 'In Progress'
    if row['state'] == 'delivered':
        with runs.transaction() as db:
            require_evidence(db, row, 'delivered')
        return row, 'Done'
    raise RevisionConflict('Run state does not authorize ticket updates')


async def owned_issue(remote, creation):
    issue_id = creation['receipt']['remote_id']
    issue = await remote.call('get_issue', {'id': issue_id})
    await linear.receipt(remote, creation['id'], creation['intent'], issue_id, issue=issue)
    if (issue.get('uuid') or issue.get('id')) != issue_id:
        raise RevisionConflict('Issue identity changed')
    if not isinstance(issue.get('updatedAt'), str) or not issue['updatedAt']:
        raise RevisionConflict('Issue revision missing')
    return issue


async def update_one(store, creation, target, remote, authorize):
    # Serialize all state changes to a given ticket across runs/processes.
    with worker_lock(store.path, 'state-'+creation['id']) as acquired:
        if not acquired: raise RevisionConflict('Ticket state busy')
        if (creation['state'] != 'confirmed' or creation['paused']
                or creation['intent']['service'] != 'linear'
                or creation['intent']['resource'] != linear.PROJECT
                or creation['intent']['action'] != 'create_issue' or target not in STATES):
            raise RevisionConflict('Unmanaged ticket or invalid state')
        external = creation['id']+'-'+STATES[target][0]
        op = digest(['linear', linear.PROJECT, external, 'update_issue_state'])
        try: previous = store.get(op)
        except ValueError: previous = None
        if previous and previous['state'] == 'confirmed': return previous
        if previous and previous['paused']: raise RevisionConflict('State update paused')
        authorize()
        issue = await owned_issue(remote, creation)
        if previous is None:
            intent = {'external_id':external, 'service':'linear', 'resource':linear.PROJECT,
                      'action':'update_issue_state', 'revision':digest(issue),
                      'payload':{'text':json.dumps({'creation':creation['id'], 'target':target,
                                                   'from':issue.get('status'), 'updatedAt':issue['updatedAt']})},
                      'links':creation['intent']['links']}
            store.prepare(intent)
        row = store.get(op); intent = row['intent']
        if json.loads(intent['payload']['text'])['target'] != target:
            raise RevisionConflict('State intent changed')
        def confirm():
            store._confirm(op, intent, {'operation':op,'intent_hash':digest(intent),
                           'service':'linear','resource':linear.PROJECT,
                           'remote_id':creation['receipt']['remote_id']})
            return store.get(op)
        if issue.get('status') == target:
            return confirm()
        if row['state'] in {'in_flight','uncertain'}:
            store._set(op, 'uncertain', 'reconciliation_required')
            return store.get(op)
        allowed = {'Todo'} if target == 'In Progress' else {'Todo','In Progress'}
        if issue.get('status') not in allowed or digest(issue) != intent['revision']:
            store._set(op, 'failed', 'revision_conflict')
            return store.get(op)
        # The provider has no conditional state mutation. Never dispatch a
        # read-then-write that could overwrite a concurrent human state edit.
        store._set(op, 'failed', 'atomic_state_update_unavailable')
        return store.get(op)



async def sync_run(runs, run_id, tickets=None, remote=None):
    row, target = target_for_run(runs, run_id)
    if target is None: return []
    controller_gate.require_green()
    store = tickets if tickets is not None else SyncStore(linear.DATABASE)
    plan = runs.output(run_id, 'planner')
    if not plan or not 1 <= len(plan.get('steps', [])) <= 5:
        raise RevisionConflict('Planner checkpoint missing')
    creations = []
    for index in range(1,len(plan['steps'])+1):
        op = digest(['linear',linear.PROJECT,f'PLAN-{run_id}-{index}','create_issue'])
        creation = store.get(op)
        if creation['intent']['links']['run_id'] != run_id:
            raise RevisionConflict('Ticket belongs to another run')
        creations.append(creation)
    def authorize():
        current, current_target = target_for_run(runs, run_id)
        if current['version'] != row['version'] or current_target != target:
            raise RevisionConflict('Run paused or changed')
    async def send(client):
        await linear.verify_destination(client)
        mapping = await client.call('list_issue_statuses', {'team':linear.TEAM})
        if not any(s['id']==STATES[target][0] and s['name']==target and s['type']==STATES[target][1] for s in mapping):
            raise RevisionConflict('State mapping changed')
        result=[]
        for creation in creations:
            authorize()
            issue=await owned_issue(client,creation)
            result.append({'state':'observed','controller_state':target,
                           'workflow_state':issue.get('status'), 'policy':'report_only',
                           'remote_id':creation['receipt']['remote_id']})
        return result
    if remote is not None: return await send(remote)
    async with linear.connection(write=True) as client: return await send(client)


def sync_run_sync(runs, run_id):
    if linear.PROJECT not in json.loads(runs.get(run_id)['manifest'])['allowed_resources']['linear_projects']:
        return []
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda:asyncio.run(sync_run(runs,run_id))).result()


def main(argv=None):
    from local_control import LocalStore
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    args=parser.parse_args(argv)
    try:
        result=asyncio.run(sync_run(LocalStore(linear.ROOT/'.state/controller.sqlite'),args.run_id))
    except Exception as exc:
        print(json.dumps({'status':'blocked','reason':failure_reason(exc)})); return 2
    print(json.dumps({'updates':result},indent=2)); return 0
