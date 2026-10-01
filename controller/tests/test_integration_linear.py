import asyncio
import copy
import pytest

import integration_linear as linear
from integration_sync import SyncStore, RevisionConflict
from integration_auth import AuthenticationRequired


@pytest.fixture
def prepared(tmp_path):
    data = {'external_id': 'DEV-014-acceptance', 'title': 'Validate ticket creation',
            'description': 'Record retry evidence.', 'requirement': 'https://app.notion.com/p/example',
            'run_id': 'phase2-acceptance', 'pr': 'pending'}
    store = SyncStore(tmp_path / 'sync.sqlite')
    op = store.prepare(linear.proposal(data))
    return store, op


class Remote:
    schemas = {'create_issue': {}}

    def __init__(self):
        self.issues = []
        self.creates = 0
        self.lose_response = False
        self.expired = False
        self.project = linear.PROJECT
        self.hook = None

    def validate(self, name, args):
        assert name == 'create_issue'
        assert set(args) == {'title', 'description', 'team', 'project', 'state'}

    async def call(self, name, args):
        if self.expired:
            raise AuthenticationRequired('expired')
        if name == 'get_workspace':
            return {'id': linear.WORKSPACE}
        if name == 'get_project':
            return {'uuid': self.project, 'leadTeam': {'id': linear.TEAM}}
        if name == 'list_issue_statuses':
            return [{'id': linear.TODO, 'type': 'unstarted'}]
        if name == 'list_issues':
            if self.hook:
                self.hook()
            return {'issues': copy.deepcopy(self.issues), 'hasNextPage': False}
        if name == 'get_issue':
            return next(copy.deepcopy(i) for i in self.issues if i['id'] == args['id'])
        assert name == 'create_issue'
        self.creates += 1
        issue = {'id': f'AGE-{100 + self.creates}', 'title': args['title'],
                 'description': args['description'], 'projectId': args['project'], 'teamId': args['team']}
        self.issues.append(issue)
        if self.lose_response:
            raise TimeoutError('response lost')
        return issue


def execute(prepared, remote):
    return asyncio.run(linear.execute(*prepared, remote))


def test_creation_and_repeat_preserve_existing_issues(prepared):
    remote = Remote()
    foreign = {'id': 'AGE-5', 'description': 'Human content'}
    remote.issues.append(foreign.copy())
    assert execute(prepared, remote)['state'] == 'confirmed'
    assert execute(prepared, remote)['state'] == 'confirmed'
    assert remote.creates == 1
    assert remote.issues[0] == foreign


def test_timeout_after_create_reconciles_after_restart(prepared):
    remote = Remote()
    remote.lose_response = True
    assert execute(prepared, remote)['state'] == 'uncertain'
    store, op = prepared
    assert execute((SyncStore(store.path), op), remote)['state'] == 'confirmed'
    assert remote.creates == 1


def test_uncertain_absence_never_recreates(prepared):
    remote = Remote()
    store, op = prepared
    store._set(op, 'in_flight')
    assert execute(prepared, remote)['state'] == 'uncertain'
    assert remote.creates == 0


def test_duplicate_receipts_block(prepared):
    remote = Remote()
    remote.lose_response = True
    execute(prepared, remote)
    second = copy.deepcopy(remote.issues[0])
    second['id'] = 'AGE-102'
    remote.issues.append(second)
    assert execute(prepared, remote)['state'] == 'uncertain'
    assert remote.creates == 1


@pytest.mark.parametrize('field,value', [('projectId', 'foreign'), ('teamId', 'foreign'),
                                        ('title', 'changed'), ('description', 'changed')])
def test_changed_remote_evidence_blocks_reconciliation(prepared, field, value):
    remote = Remote()
    remote.lose_response = True
    execute(prepared, remote)
    remote.issues[0][field] = value
    assert execute(prepared, remote)['state'] == 'uncertain'
    assert remote.creates == 1


def test_expired_credential_can_retry_only_before_dispatch(prepared):
    remote = Remote()
    remote.expired = True
    assert execute(prepared, remote)['reason'] == 'local_oauth_login_required'
    assert remote.creates == 0
    remote.expired = False
    assert execute(prepared, remote)['state'] == 'confirmed'


def test_wrong_project_or_state_denies_write(prepared):
    remote = Remote()
    remote.project = 'foreign'
    assert execute(prepared, remote)['state'] == 'failed'
    assert remote.creates == 0


def test_pause_during_preflight_prevents_dispatch(prepared):
    store, op = prepared
    remote = Remote()
    remote.hook = lambda: store.pause(op, True, store.get(op)['version'])
    with pytest.raises(RevisionConflict):
        execute(prepared, remote)
    assert remote.creates == 0


def test_crash_before_local_receipt_never_duplicates(prepared, monkeypatch):
    store, op = prepared
    remote = Remote()
    def crash(*args):
        raise SystemExit('process killed')
    monkeypatch.setattr(store, '_confirm', crash)
    with pytest.raises(SystemExit):
        execute(prepared, remote)
    assert store.get(op)['state'] == 'in_flight'
    assert execute((SyncStore(store.path), op), remote)['state'] == 'confirmed'
    assert remote.creates == 1


def test_agent_cannot_override_destination_or_tool():
    with pytest.raises(ValueError):
        linear.proposal({'project': 'foreign', 'tool': 'delete_issue'})


def test_prepared_identity_rejects_modified_content(prepared):
    store, op = prepared
    intent = store.get(op)['intent']
    intent['payload']['text'] += ' changed'
    with pytest.raises(RevisionConflict):
        store.prepare(intent)


def test_truncated_list_and_linear_autolinks_reconcile(prepared):
    class TruncatedRemote(Remote):
        async def call(self, name, args):
            result = await super().call(name, args)
            if name == 'list_issues':
                for issue in result['issues']:
                    issue['description'] = 'truncated, use get_issue'
            return result
    remote = TruncatedRemote()
    remote.lose_response = True
    execute(prepared, remote)
    body = remote.issues[0]['description']
    url = 'https://app.notion.com/p/example'
    remote.issues[0]['description'] = body.replace(url, f'[{url}](<{url}>)')
    assert execute(prepared, remote)['state'] == 'confirmed'
    assert remote.creates == 1


def test_changed_link_target_is_not_normalized(prepared):
    remote = Remote()
    remote.lose_response = True
    execute(prepared, remote)
    url = 'https://app.notion.com/p/example'
    remote.issues[0]['description'] = remote.issues[0]['description'].replace(url, f'[{url}](<https://evil.test/>)')
    assert execute(prepared, remote)['state'] == 'uncertain'
    assert remote.creates == 1


def test_notion_autolink_without_angle_brackets():
    url = 'https://linear.app/agenta-pp/issue/AGE-9/example'
    assert linear.normalized_links(f'[{url}]({url})') == url
    altered = f'[{url}](https://different.example/path)'
    assert linear.normalized_links(altered) == altered


@pytest.fixture
def planner_checkpoint(tmp_path, monkeypatch):
    import json
    import manifest
    contract = json.loads((linear.ROOT / 'config/local-pilot.json').read_text(encoding='utf-8'))
    contract['schema_version'] = '1.2'
    contract['allowed_resources']['linear_projects'] = [linear.PROJECT]
    contract['allowed_resources']['notion_pages'] = ['3e567de3-6dff-81c6-a414-c87fa86f8a1e']
    row = {'manifest': json.dumps(contract), 'manifest_hash': manifest.identity(contract),
           'execution_kind': 'local', 'state': 'planning', 'workspace': str(tmp_path),
           'fingerprint': json.dumps(__import__('controller').fingerprint(tmp_path, []))}
    plan = {'steps': ['Implementar el requisito', 'Validar los resultados']}
    class RunStore:
        def get(self, run_id): return row
        def output(self, run_id, role):
            assert role == 'planner'
            return plan
    monkeypatch.setattr(manifest, 'verify_base', lambda *args: None)
    return RunStore(), row, plan, SyncStore(tmp_path / 'tickets.sqlite')


def test_planner_prepares_stable_tickets_without_network(planner_checkpoint):
    store, row, plan, tickets = planner_checkpoint
    first = linear.prepare_planner_tickets(store, 'run-1', tickets)
    assert linear.prepare_planner_tickets(store, 'run-1', tickets) == first
    assert len(first) == 2
    assert all(tickets.get(op)['state'] == 'prepared' for op in first)
    assert all(tickets.get(op)['intent']['resource'] == linear.PROJECT for op in first)


def test_local_only_run_does_not_prepare_external_tickets(planner_checkpoint):
    import json
    store, row, _, tickets = planner_checkpoint
    contract = json.loads(row['manifest'])
    contract['allowed_resources']['linear_projects'] = []
    row['manifest'] = json.dumps(contract)
    assert linear.prepare_planner_tickets(store, 'run-1', tickets) == []


def test_changed_planner_proposal_cannot_reuse_identity(planner_checkpoint):
    store, row, plan, tickets = planner_checkpoint
    linear.prepare_planner_tickets(store, 'run-1', tickets)
    plan['steps'][0] = 'different'
    with pytest.raises(RevisionConflict):
        linear.prepare_planner_tickets(store, 'run-1', tickets)


def test_paused_run_cannot_prepare_tickets(planner_checkpoint):
    store, row, _, tickets = planner_checkpoint
    row['state'] = 'paused'
    with pytest.raises(ValueError):
        linear.prepare_planner_tickets(store, 'run-1', tickets)


def test_planner_dispatch_creates_once(planner_checkpoint, monkeypatch):
    store, row, _, tickets = planner_checkpoint
    row['version'] = 7
    monkeypatch.setattr(linear.controller_gate, 'require_green', lambda: None)
    remote = Remote()
    first = asyncio.run(linear.dispatch_planner_tickets(store, 'run-1', tickets, remote))
    second = asyncio.run(linear.dispatch_planner_tickets(store, 'run-1', tickets, remote))
    assert len(first) == 2 and first == second and remote.creates == 2


def test_planner_pause_during_remote_reads_prevents_write(planner_checkpoint, monkeypatch):
    store, row, _, tickets = planner_checkpoint
    row['version'] = 7
    monkeypatch.setattr(linear.controller_gate, 'require_green', lambda: None)
    remote = Remote()
    def pause():
        row['version'] += 1
        row['state'] = 'paused'
    remote.hook = pause
    with pytest.raises(RevisionConflict):
        asyncio.run(linear.dispatch_planner_tickets(store, 'run-1', tickets, remote))
    assert remote.creates == 0


def test_planner_uncertain_dispatch_does_not_repeat_creation(planner_checkpoint, monkeypatch):
    store, row, _, tickets = planner_checkpoint
    row['version'] = 7
    monkeypatch.setattr(linear.controller_gate, 'require_green', lambda: None)
    remote = Remote()
    remote.lose_response = True
    with pytest.raises(RevisionConflict):
        asyncio.run(linear.dispatch_planner_tickets(store, 'run-1', tickets, remote))
    assert remote.creates == 1
    remote.lose_response = False
    assert len(asyncio.run(linear.dispatch_planner_tickets(store, 'run-1', tickets, remote))) == 2
    assert remote.creates == 2
