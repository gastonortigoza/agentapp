"""Append-only evidence notes to the explicitly authorized phase-2 page."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import controller_gate
from integration_auth import LocalOAuth, SecureStorage, silence_auth_logs
from integration_cli import failure_reason
from integration_linear import MCP, normalized_links
from integration_sync import SyncStore, RevisionConflict, digest
from worker_lock import worker_lock

ROOT = Path(__file__).resolve().parent
PAGE = '3e567de3-6dff-81c6-a414-c87fa86f8a1e'
ENDPOINT = 'https://mcp.notion.com/mcp'
DATABASE = ROOT / '.state/notion-notes.sqlite'


@asynccontextmanager
async def connection():
    silence_auth_logs()
    with worker_lock(ROOT / '.state/oauth-lock', 'notion') as acquired:
        if not acquired:
            raise ValueError('Notion connection busy')
        auth = LocalOAuth('notion', ENDPOINT, SecureStorage('notion', ENDPOINT))
        async with asyncio.timeout(90):
            async with httpx.AsyncClient(auth=auth, trust_env=False, follow_redirects=False, timeout=15) as client:
                async with streamable_http_client(ENDPOINT, http_client=client) as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        page = await session.list_tools()
                        yield MCP(session, {t.name: t.inputSchema for t in page.tools})


async def read(remote):
    result = await remote.call('notion-fetch', {'id': PAGE})
    if result.get('truncated') or result.get('unknown_block_count') or result.get('unknown_block_ids'):
        raise ValueError('Incomplete page content')
    url = urlsplit(result['url'])
    if url.hostname != 'app.notion.com' or url.path != '/p/' + PAGE.replace('-', ''):
        raise ValueError('Unexpected page identity')
    text = result['text']
    if text.count('<content>') != 1 or text.count('</content>') != 1:
        raise ValueError('Incomplete page content')
    return text.split('<content>', 1)[1].split('</content>', 1)[0].strip('\n')


def addition(op, intent):
    return intent['payload']['text'] + '\n\nagentapp-note:' + op + ':' + digest(intent)


def comparable(text):
    # Notion serializes paragraph spacing and escapes ':' in the plain marker.
    text = re.sub(r'(?m)^agentapp-note\\?:([a-f0-9]{64})\\?:([a-f0-9]{64})$',
                  r'agentapp-note:\1:\2', normalized_links(text))
    return '\n'.join(line for line in text.splitlines() if line.strip())


async def resolve_formatting(store, op, remote, actor):
    """Explicit read-only reconciliation with a narrowly bounded rendering rule."""
    import getpass
    if actor != 'windows:' + getpass.getuser():
        raise ValueError('Resolver identity mismatch')
    with worker_lock(store.path, op) as acquired:
        if not acquired:
            raise RevisionConflict('Note already executing')
        row = store.get(op)
        if row['state'] == 'confirmed':
            return row
        if row['state'] != 'uncertain' or row['paused']:
            raise RevisionConflict('Only an unpaused uncertain note can be reconciled')
        intent = row['intent']
        if (intent['service'],intent['resource'],intent['action']) != ('notion',PAGE,'append_note'):
            raise ValueError('Wrong note destination')
        body = await read(remote)
        expected = comparable(addition(op,intent))
        # Exact known editor transformations, never arbitrary link targets.
        observed = comparable(body).replace('\\{','{').replace('\\}','}')
        observed = observed.replace('[agent.py](http://agent.py)', 'agent.py')
        marker = 'agentapp-note:' + op + ':'
        if observed.count(marker) != 1 or observed.count(expected) != 1:
            raise RevisionConflict('Rendered note differs beyond approved formatting')
        receipt = {'operation':op,'intent_hash':digest(intent),'service':'notion',
                   'resource':PAGE,'remote_id':PAGE}
        with store.transaction() as db:
            current = store._row(db,op)
            if current['version'] != row['version'] or current['paused']:
                raise RevisionConflict('Note changed during resolution')
            db.execute('CREATE TABLE IF NOT EXISTS resolution_audit (operation TEXT PRIMARY KEY, actor TEXT, observation_hash TEXT, intent_hash TEXT, rule TEXT)')
            db.execute('INSERT INTO resolution_audit VALUES(?,?,?,?,?)',
                       (op,actor,digest(body),digest(intent),'notion-braces-and-exact-agent.py-autolink-v1'))
            store._event(db,current,'confirmed','formatting_verified_no_resend',receipt)
        return store.get(op)


def confirm(store, op, intent):
    store._confirm(op, intent, {'operation': op, 'intent_hash': digest(intent),
                   'service': 'notion', 'resource': PAGE, 'remote_id': PAGE})


async def prepare(store, remote, external_id, text):
    if not text.strip() or len(text.encode()) > 12000 or 'agentapp-note:' in text:
        raise ValueError('Invalid evidence note')
    # Re-preparing a logical note uses its original revision, never silently
    # binds a changed document or different text to an existing identity.
    op = digest(['notion', PAGE, external_id, 'append_note'])
    try:
        existing = store.get(op)
    except ValueError:
        existing = None
    revision = existing['intent']['revision'] if existing else digest(await read(remote))
    intent = {'external_id': external_id, 'service': 'notion', 'resource': PAGE,
              'revision': revision, 'action': 'append_note', 'payload': {'text': text},
              'links': {'requirement': 'https://app.notion.com/p/' + PAGE.replace('-', ''),
                        'run_id': external_id, 'pr': 'pending'}}
    return store.prepare(intent)


async def execute(store, op, remote):
    with worker_lock(store.path, op) as acquired:
        if not acquired:
            raise RevisionConflict('Note already executing')
        row = store.get(op)
        intent = row['intent']
        if (intent['service'], intent['resource'], intent['action']) != ('notion', PAGE, 'append_note'):
            raise ValueError('Destination denied')
        if row['state'] == 'confirmed':
            return row
        if row['paused']:
            raise RevisionConflict('Note paused')
        uncertain = row['state'] in {'in_flight', 'uncertain'}
        note = addition(op, intent)
        marker = 'agentapp-note:' + op + ':'
        try:
            before = await read(remote)
            if comparable(before).count(marker) == 1 and comparable(note) in comparable(before):
                confirm(store, op, intent)
                return store.get(op)
            if uncertain or marker in comparable(before):
                store._set(op, 'uncertain', 'reconciliation_required')
                return store.get(op)
            if digest(before) != intent['revision']:
                raise RevisionConflict('Page changed since preparation')
            anchor = before.split('\n')[-1]
            if not anchor.strip() or before.count(anchor) != 1:
                raise RevisionConflict('Append anchor is ambiguous')
            args = {'page_id': PAGE, 'command': 'update_content', 'properties': {},
                    'content_updates': [{'old_str': anchor, 'new_str': anchor + '\n\n' + note}],
                    'allow_async': False}
            remote.validate('notion-update-page', args)
        except Exception as exc:
            store._set(op, 'uncertain' if uncertain else 'failed',
                       'reconciliation_required' if uncertain else
                       ('revision_conflict' if isinstance(exc, RevisionConflict) else failure_reason(exc)))
            return store.get(op)
        with store.transaction() as db:
            current = store._row(db, op)
            if current['paused'] or current['version'] != row['version']:
                raise RevisionConflict('Note authorization changed')
            store._event(db, current, 'in_flight')
        try:
            await remote.call('notion-update-page', args)
            after = await read(remote)
            if (comparable(after).count(marker) != 1 or comparable(note) not in comparable(after)
                    or comparable(before) not in comparable(after)):
                raise RevisionConflict('Append or preservation not verified')
            confirm(store, op, intent)
        except Exception:
            store._set(op, 'uncertain', 'reconciliation_required')
        return store.get(op)


async def run(args):
    controller_gate.require_green()
    store = SyncStore(DATABASE)
    async with connection() as remote:
        if args.action == 'resolve-formatting':
            import getpass
            return await resolve_formatting(store,args.operation,remote,'windows:'+getpass.getuser())
        if args.action == 'prepare':
            path = Path(args.file)
            if path.stat().st_size > 12000:
                raise ValueError('Note too large')
            op = await prepare(store, remote, args.external_id, path.read_text(encoding='utf-8').strip())
            return store.get(op)
        return await execute(store, args.operation, remote)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('external_id')
    p.add_argument('file')
    sub.add_parser('execute').add_argument('operation')
    sub.add_parser('resolve-formatting').add_argument('operation')
    try:
        result = asyncio.run(run(parser.parse_args(argv)))
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': failure_reason(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['state'] not in {'uncertain', 'failed'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
