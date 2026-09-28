"""Explicit ticket creation for the authorized AgentApp project over MCP.

The planner supplies a proposal, never a tool name or credential. Creation is
journaled before dispatch. An uncertain request is reconciled, never resent.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import controller_gate
from integration_auth import Callback, LocalOAuth, SecureStorage, silence_auth_logs
from integration_cli import failure_reason, unique_pairs
from integration_sync import SyncStore, RevisionConflict, digest
from worker_lock import worker_lock

ROOT = Path(__file__).resolve().parent
PROJECT = '478a5228-5e0e-4351-a90c-324422050c50'
TEAM = 'd565b39a-d4f0-47ce-bbb6-eec11559a6c8'
WORKSPACE = '1e1cafa2-9333-464a-b13a-46fa0621ef95'
TODO = '880bbed7-a90c-41dc-b04d-f6e3d227d4a1'
READ_ENDPOINT = 'https://mcp.linear.app/mcp/readonly'
WRITE_ENDPOINT = 'https://mcp.linear.app/mcp'
DATABASE = ROOT / '.state/linear-tickets.sqlite'


def prepare_planner_tickets(run_store, run_id, ticket_store=None):
    """Convert a verified planner checkpoint into durable proposals, no network.

    Existing local-only runs stay local. Permissions come from the run contract,
    never from model output. Dispatch is separate and rechecks the checkpoint.
    """
    import manifest
    from controller import fingerprint
    row = run_store.get(run_id)
    contract = json.loads(row['manifest'])
    resources = contract['allowed_resources']
    if PROJECT not in resources['linear_projects']:
        return []
    if row['execution_kind'] != 'local' or row['state'] != 'planning':
        raise ValueError('Planner checkpoint is not active')
    if manifest.identity(contract) != row['manifest_hash']:
        raise ValueError('Run contract changed')
    manifest.validate(contract, row['workspace'])
    recorded = json.loads(row['fingerprint'])
    if fingerprint(row['workspace'], recorded) != recorded:
        raise ValueError('Run sources changed')
    pages = resources['notion_pages']
    if len(pages) != 1 or not re.fullmatch(r'[a-f0-9-]{32,36}', pages[0]):
        raise ValueError('One requirement page must be bound to the run')
    plan = run_store.output(run_id, 'planner')
    if not plan or not isinstance(plan.get('steps'), list) or not 1 <= len(plan['steps']) <= 5:
        raise ValueError('Planner evidence missing')
    if any(not isinstance(step, str) or not step.strip() or len(step) > 1000 for step in plan['steps']):
        raise ValueError('Invalid planner steps')
    # Validate every proposal before preparing any, with stable per-run keys.
    intents = []
    for index, step in enumerate(plan['steps'], 1):
        intents.append(proposal({'external_id': f'PLAN-{run_id}-{index}',
            'title': f'Paso {index}: ' + ' '.join(step.split())[:150],
            'description': 'Paso propuesto por el planificador:\n\n' + step,
            'requirement': 'https://app.notion.com/p/' + pages[0].replace('-', ''),
            'run_id': run_id, 'pr': 'pending'}))
    tickets = ticket_store if ticket_store is not None else SyncStore(DATABASE)
    return [tickets.prepare(intent) for intent in intents]


async def dispatch_planner_tickets(run_store, run_id, ticket_store=None, remote=None):
    tickets = ticket_store if ticket_store is not None else SyncStore(DATABASE)
    ops = prepare_planner_tickets(run_store, run_id, tickets)
    if not ops:
        return []
    controller_gate.require_green()
    version = run_store.get(run_id)['version']
    def authorize():
        if run_store.get(run_id)['version'] != version:
            raise RevisionConflict('Run authorization changed')
        if prepare_planner_tickets(run_store, run_id, tickets) != ops:
            raise RevisionConflict('Planner checkpoint changed')
    async def send(client):
        results = []
        for op in ops:
            authorize()
            result = await execute(tickets, op, client, before_write=authorize)
            if result['state'] != 'confirmed':
                raise RevisionConflict('Planner ticket pending reconciliation')
            results.append({'operation':op, 'receipt':result['receipt']})
        return results
    if remote is not None:
        return await send(remote)
    async with connection(write=True) as client:
        return await send(client)


def dispatch_planner_sync(run_store, run_id):
    # CrewAI owns an event loop; keep the MCP loop in a bounded worker thread.
    # Old local-only contracts do not open a connection or need credentials.
    if PROJECT not in json.loads(run_store.get(run_id)['manifest'])['allowed_resources']['linear_projects']:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(dispatch_planner_tickets(run_store, run_id))).result()


def proposal(data):
    if not isinstance(data, dict) or set(data) != {'external_id', 'title', 'description', 'requirement', 'run_id', 'pr'}:
        raise ValueError('Invalid ticket proposal')
    for k, v in data.items():
        if not isinstance(v, str) or not v.strip():
            raise ValueError('Missing proposal field')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', data['external_id']):
        raise ValueError('Invalid external identity')
    if len(data['title']) > 180 or any(c in data['title'] for c in '\r\n'):
        raise ValueError('Invalid title')
    if len(data['description'].encode()) > 12000 or 'agentapp-operation:' in json.dumps(data):
        raise ValueError('Invalid description or reserved marker')
    if not data['requirement'].startswith('https://app.notion.com/p/'):
        raise ValueError('Requirement must link to Notion')
    if data['pr'] != 'pending' and not re.fullmatch(r'https://github.com/gastonortigoza/agentapp/pull/[1-9][0-9]*', data['pr']):
        raise ValueError('Invalid PR reference')
    return {'external_id': data['external_id'], 'service': 'linear', 'resource': PROJECT,
            'revision': 'create-v1', 'action': 'create_issue',
            'payload': {'text': data['title'] + '\n\n' + data['description']},
            'links': {k: data[k] for k in ('requirement', 'run_id', 'pr')}}


def ticket_fields(op, intent):
    title, description = intent['payload']['text'].split('\n\n', 1)
    marker = f'agentapp-operation:{op}:{digest(intent)}'
    links = intent['links']
    body = (description + '\n\nRequisito: ' + links['requirement'] +
            '\nEjecución: ' + links['run_id'] + '\nPR: ' + links['pr'] + '\n\n' + marker)
    return {'title': title, 'description': body, 'team': TEAM, 'project': PROJECT, 'state': TODO}


class MCP:
    def __init__(self, session, schemas):
        self.session, self.schemas = session, schemas

    def validate(self, name, args):
        if name not in self.schemas:
            raise ValueError('Required MCP capability missing')
        jsonschema.validate(args, self.schemas[name])

    async def call(self, name, args):
        self.validate(name, args)
        result = await self.session.call_tool(name, args)
        if result.isError:
            raise ValueError('Remote operation rejected')
        texts = [c.text for c in result.content if c.type == 'text']
        if len(texts) != 1 or len(texts[0]) > 2_000_000:
            raise ValueError('Unexpected MCP response')
        return json.loads(texts[0], object_pairs_hook=unique_pairs)


@asynccontextmanager
async def connection(write=False, interactive=False):
    silence_auth_logs()
    service = 'linear_write' if write else 'linear'
    endpoint = WRITE_ENDPOINT if write else READ_ENDPOINT
    callback = Callback(service) if interactive else None
    from contextlib import nullcontext
    with worker_lock(ROOT / '.state/oauth-lock', service) as acquired:
        if not acquired:
            raise ValueError('Connector busy')
        with callback.listen() if callback else nullcontext():
            auth = LocalOAuth(service, endpoint, SecureStorage(service, endpoint),
                              interactive=interactive, redirect=callback.redirect if callback else None,
                              callback=callback.wait if callback else None)
            async with asyncio.timeout(360 if interactive else 90):
                async with httpx.AsyncClient(auth=auth, trust_env=False, follow_redirects=False, timeout=15) as client:
                    async with streamable_http_client(endpoint, http_client=client) as (r, w, _):
                        async with ClientSession(r, w) as session:
                            await session.initialize()
                            schemas, cursor = {}, None
                            for _ in range(20):
                                page = await session.list_tools(cursor=cursor)
                                schemas.update({t.name: t.inputSchema for t in page.tools})
                                cursor = page.nextCursor
                                if not cursor:
                                    break
                            else:
                                raise ValueError('Capability pagination incomplete')
                            yield MCP(session, schemas)


async def verify_destination(remote):
    workspace = await remote.call('get_workspace', {})
    project = await remote.call('get_project', {'query': PROJECT})
    states = await remote.call('list_issue_statuses', {'team': TEAM})
    if (workspace.get('id') != WORKSPACE or project.get('uuid', project.get('id')) != PROJECT
            or project.get('leadTeam', {}).get('id') != TEAM
            or not any(s.get('id') == TODO and s.get('type') == 'unstarted' for s in states)):
        raise ValueError('Destination or state mapping changed')


def normalized_links(text):
    # Linear's editor wraps bare URLs. Only unwrap identical label/target pairs;
    # a changed target or any other content still invalidates the receipt.
    text = re.sub(r'\[(https://[^\s\[\]<>]+)\]\(<\1>\)', r'\1', text)
    return re.sub(r'\[(https://[^\s\[\]<>()]+)\]\(\1\)', r'\1', text)


async def receipt(remote, op, intent, remote_id, issue=None):
    if issue is None:
        issue = await remote.call('get_issue', {'id': remote_id})
    expected = ticket_fields(op, intent)
    project = issue.get('projectId') or issue.get('project', {}).get('id')
    team = issue.get('teamId') or issue.get('team', {}).get('id')
    if (project != PROJECT or team != TEAM or issue.get('title') != expected['title']
            or normalized_links(issue.get('description') or '') != normalized_links(expected['description'])):
        raise RevisionConflict('Created issue does not match prepared intent')
    identity = issue.get('uuid') or issue.get('id')
    return {'operation': op, 'intent_hash': digest(intent), 'service': 'linear',
            'resource': PROJECT, 'remote_id': identity}


async def find_receipts(remote, op, intent):
    # Full bounded project scan. Never interpret an empty search as permission
    # to retry an uncertain write. Read every candidate back by remote identity.
    matches, cursor = [], None
    for _ in range(20):
        args = {'project': PROJECT, 'team': TEAM, 'includeArchived': True,
                'limit': 250, 'fields': ['id', 'uuid', 'description', 'projectId', 'teamId']}
        if cursor:
            args['cursor'] = cursor
        page = await remote.call('list_issues', args)
        for issue in page['issues']:
            identity = issue.get('uuid') or issue['id']
            # list_issues truncates descriptions, even with fields requested.
            full = await remote.call('get_issue', {'id': identity})
            if f'agentapp-operation:{op}:' in (full.get('description') or ''):
                matches.append(await receipt(remote, op, intent, identity, issue=full))
        if not page.get('hasNextPage'):
            return matches
        cursor = page.get('cursor')
        if not cursor:
            raise ValueError('Issue pagination incomplete')
    raise ValueError('Issue inventory limit exceeded')


async def execute(store, op, remote, before_write=None):
    with worker_lock(store.path, op) as acquired:
        if not acquired:
            raise RevisionConflict('Operation already executing')
        row = store.get(op)
        intent = row['intent']
        if (intent['service'], intent['resource'], intent['action']) != ('linear', PROJECT, 'create_issue'):
            raise ValueError('Resource or operation denied')
        if row['state'] == 'confirmed':
            return row
        if row['paused']:
            raise RevisionConflict('Operation paused')
        uncertain = row['state'] in {'in_flight', 'uncertain'}
        try:
            await verify_destination(remote)
            matches = await find_receipts(remote, op, intent)
            if len(matches) == 1:
                store._confirm(op, intent, matches[0])
                return store.get(op)
            if matches or uncertain:
                store._set(op, 'uncertain', 'reconciliation_required')
                return store.get(op)
            fields = ticket_fields(op, intent)
            name = 'create_issue' if 'create_issue' in remote.schemas else 'save_issue'
            remote.validate(name, fields)  # schema must allow creation without an ID
            if before_write is not None:
                before_write()
        except Exception as exc:
            store._set(op, 'uncertain' if uncertain else 'failed',
                       'reconciliation_required' if uncertain else failure_reason(exc))
            return store.get(op)
        with store.transaction() as db:
            current = store._row(db, op)
            if current['paused'] or current['version'] != row['version']:
                raise RevisionConflict('Authorization changed')
            store._event(db, current, 'in_flight')
        try:
            created = await remote.call(name, fields)
            confirmed = await receipt(remote, op, intent, created.get('uuid') or created['id'])
            store._confirm(op, intent, confirmed)
        except Exception:
            store._set(op, 'uncertain', 'reconciliation_required')
        return store.get(op)


async def run(args):
    store = SyncStore(DATABASE)
    if args.action == 'prepare':
        path = Path(args.file)
        if path.stat().st_size > 20000:
            raise ValueError('Proposal too large')
        intent = proposal(json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs))
        op = store.prepare(intent)
        return {'operation': op, 'state': store.get(op)['state'], 'ticket': ticket_fields(op, intent)}
    if args.action == 'status':
        return store.get(args.operation)
    controller_gate.require_green()
    async with connection(write=True, interactive=args.action == 'login') as remote:
        if args.action == 'login':
            await verify_destination(remote)
            return {'status': 'authenticated', 'project': PROJECT, 'scope': 'read write',
                    'tools': sorted(remote.schemas), 'ticket_created': False}
        return await execute(store, args.operation, remote)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('login')
    sub.add_parser('prepare').add_argument('file')
    for name in ('execute', 'status'):
        sub.add_parser(name).add_argument('operation')
    try:
        result = asyncio.run(run(parser.parse_args(argv)))
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': failure_reason(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('state') not in {'failed', 'uncertain'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
