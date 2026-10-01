import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
import integration_coolify as c
from integration_cli import ConnectionBlocked, failure_reason


def test_expired_authorization_blocks():
    c.check_expiry(c.EXPIRES - timedelta(seconds=1))
    with pytest.raises(ConnectionBlocked, match='authorization_expired'):
        c.check_expiry(c.EXPIRES)


@pytest.mark.parametrize('name,args', [
    ('deploy', {'uuid': c.PROJECT}),
    ('get_project', {'uuid': 'another-project'}),
    ('get_server', {'uuid': c.SERVER, 'extra': True}),
    ('get_logs', {'uuid': c.PROJECT}),
])
def test_forbidden_calls_never_reach_remote(name, args):
    class NoCalls:
        async def call_tool(self, *_): pytest.fail('Unauthorized remote call')
    with pytest.raises(ConnectionBlocked, match='outside_allowlist'):
        asyncio.run(c.ReadOnlyMCP(NoCalls(), {}).call(name, args))


def test_token_endpoint_and_permissions_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'crypt', lambda data, decrypt: data)
    p = tmp_path/'credential'
    data = {'endpoint':'http://127.0.0.1:43880/mcp', 'scope':'read',
            'team':'Root Team', 'token':'1|'+'a'*24}
    p.write_text(json.dumps(data))
    assert c.read_token(p) == data['token']
    for field, value in [('scope','root'),('endpoint','http://untrusted/mcp'),('token','x\nsecret')]:
        p.write_text(json.dumps({**data, field:value}))
        with pytest.raises(ConnectionBlocked): c.read_token(p)


def test_inventory_rejects_wrong_project_before_other_reads():
    class Remote:
        async def call(self, name, args):
            if name == 'get_current_team': return {'name':'Root Team'}
            assert name == 'get_project'
            return {'uuid':'wrong', 'name':'AgentApp'}
    with pytest.raises(ConnectionBlocked, match='project_mismatch'):
        asyncio.run(c.collect(Remote()))


def test_inventory_filters_sensitive_fields():
    class Remote:
        async def call(self, name, args):
            return {
                'get_current_team': {'name':'Root Team'},
                'get_project': {'uuid':c.PROJECT,'name':'AgentApp', 'description':'SECRET',
                                'counts':dict(applications=0,services=0,databases=0)},
                'get_environment': {'uuid':c.ENVIRONMENT,'project':{'uuid':c.PROJECT},'secret':'SECRET'},
                'get_server': {'uuid':c.SERVER,'is_reachable':True,'is_usable':True,'settings':'SECRET'},
            }[name]
    result = asyncio.run(c.collect(Remote()))
    assert 'SECRET' not in json.dumps(result)
    assert result['live_writes_enabled'] is False
    assert result['resource_access_verified'] is True


def test_nested_expiry_is_clear():
    error = ExceptionGroup('outer', [ExceptionGroup('inner', [ConnectionBlocked('coolify_tunnel_authorization_expired')])])
    assert failure_reason(error) == 'coolify_tunnel_authorization_expired'


def test_socket_owner_must_be_owned_child(monkeypatch):
    monkeypatch.setattr(c.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='[{"LocalAddress":"127.0.0.1","OwningProcess":123}]'))
    assert c.listener_owned(12345,123)
    assert not c.listener_owned(12345,999)
