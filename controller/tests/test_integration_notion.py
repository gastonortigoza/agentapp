import asyncio
import pytest
import integration_notion as notion
from integration_sync import SyncStore, RevisionConflict
from integration_auth import AuthenticationRequired


class Remote:
    def __init__(self):
        self.body = 'Human notes\nUnique final paragraph'
        self.writes = 0
        self.lose_response = False
        self.expired = False
    def validate(self, name, args):
        assert name == 'notion-update-page'
        assert args['command'] == 'update_content'
        assert args['page_id'] == notion.PAGE
    async def call(self, name, args):
        if self.expired:
            raise AuthenticationRequired('expired')
        if name == 'notion-fetch':
            return {'url': 'https://app.notion.com/p/' + notion.PAGE.replace('-', ''),
                    'text': '<content>\n' + self.body + '\n</content>'}
        self.validate(name, args)
        patch = args['content_updates'][0]
        assert patch['new_str'].startswith(patch['old_str'])
        assert self.body.count(patch['old_str']) == 1
        self.body = self.body.replace(patch['old_str'], patch['new_str'])
        self.writes += 1
        if self.lose_response:
            raise TimeoutError('lost response')
        return {'success': True}


@pytest.fixture
def setup(tmp_path):
    store, remote = SyncStore(tmp_path / 'notes.sqlite'), Remote()
    op = asyncio.run(notion.prepare(store, remote, 'acceptance', 'Verified evidence'))
    return store, op, remote


def execute(setup):
    return asyncio.run(notion.execute(*setup))


def test_append_preserves_content_and_repeat_is_noop(setup):
    _, _, remote = setup
    before = remote.body
    assert execute(setup)['state'] == 'confirmed'
    assert execute(setup)['state'] == 'confirmed'
    assert remote.body.startswith(before)
    assert remote.writes == 1


def test_lost_response_reconciles_without_second_append(setup):
    store, op, remote = setup
    remote.lose_response = True
    assert execute(setup)['state'] == 'uncertain'
    assert execute((SyncStore(store.path), op, remote))['state'] == 'confirmed'
    assert remote.writes == 1


def test_changed_page_blocks_before_write(setup):
    _, _, remote = setup
    remote.body += '\nNew human content'
    assert execute(setup)['reason'] == 'revision_conflict'
    assert remote.writes == 0


def test_uncertain_missing_note_does_not_repeat(setup):
    store, op, remote = setup
    store._set(op, 'in_flight')
    assert execute(setup)['state'] == 'uncertain'
    assert remote.writes == 0


def test_expired_auth_has_no_effect(setup):
    _, _, remote = setup
    remote.expired = True
    assert execute(setup)['reason'] == 'local_oauth_login_required'
    assert remote.writes == 0


def test_reprepare_uses_original_identity_and_rejects_changes(setup):
    store, op, remote = setup
    assert execute(setup)['state'] == 'confirmed'
    assert asyncio.run(notion.prepare(store, remote, 'acceptance', 'Verified evidence')) == op
    with pytest.raises(RevisionConflict):
        asyncio.run(notion.prepare(store, remote, 'acceptance', 'Changed evidence'))


def test_ambiguous_marker_cannot_confirm(setup):
    store, op, remote = setup
    remote.lose_response = True
    execute(setup)
    remote.body += '\n' + notion.addition(op, store.get(op)['intent'])
    assert execute(setup)['state'] == 'uncertain'
    assert remote.writes == 1


def test_notion_paragraph_serialization_and_marker_escaping(setup):
    store, op, remote = setup
    remote.lose_response = True
    execute(setup)
    remote.body = remote.body.replace('\n\n', '\n')
    lines = remote.body.splitlines()
    lines[-1] = lines[-1].replace(':', '\\:')
    remote.body = '\n'.join(lines)
    assert execute(setup)['state'] == 'confirmed'
    assert remote.writes == 1


def test_explicit_formatting_resolution_is_audited_without_write(tmp_path):
    import getpass
    store=SyncStore(tmp_path/'notes.sqlite');remote=Remote()
    op=asyncio.run(notion.prepare(store,remote,'formatting','Use agent.py and {value}.'))
    remote.lose_response=True
    assert asyncio.run(notion.execute(store,op,remote))['state']=='uncertain'
    remote.body=remote.body.replace('agent.py','[agent.py](http://agent.py)').replace('{value}',r'\{value\}')
    actor='windows:'+getpass.getuser()
    assert asyncio.run(notion.resolve_formatting(store,op,remote,actor))['state']=='confirmed'
    assert remote.writes==1
    with store.transaction() as db:
        assert db.execute('SELECT actor FROM resolution_audit WHERE operation=?',(op,)).fetchone()[0]==actor


def test_formatting_resolution_rejects_altered_link_target(tmp_path):
    import getpass
    store=SyncStore(tmp_path/'notes.sqlite');remote=Remote()
    op=asyncio.run(notion.prepare(store,remote,'formatting','Use agent.py.'))
    remote.lose_response=True;asyncio.run(notion.execute(store,op,remote))
    remote.body=remote.body.replace('agent.py','[agent.py](https://evil.test)')
    with pytest.raises(RevisionConflict):
        asyncio.run(notion.resolve_formatting(store,op,remote,'windows:'+getpass.getuser()))
    assert store.get(op)['state']=='uncertain' and remote.writes==1
