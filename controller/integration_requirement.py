"""Read a bounded remote requirement; remote data never grants capabilities."""
import asyncio
import json
import re
from datetime import datetime, timezone

import manifest
from integration_notion import PAGE, connection, read
from integration_sync import digest

URL = 'https://app.notion.com/p/' + PAGE.replace('-', '')


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {'input_key', 'attention_states'}:
        raise ValueError('Requirement fields not supported')
    if not isinstance(spec['input_key'], str) or not re.fullmatch('[a-z][a-z_]{0,39}', spec['input_key']):
        raise ValueError('Invalid input field')
    states = spec['attention_states']
    if (not isinstance(states, list) or not 1 <= len(states) <= 10
            or any(not isinstance(s, str) or not re.fullmatch('[a-z][a-z_]{0,39}', s) for s in states)
            or len(set(states)) != len(states) or 'unknown' in states):
        raise ValueError('Invalid attention states')
    return spec


def extract(body, external_id):
    if not re.fullmatch('[a-z0-9-]{1,80}', external_id):
        raise ValueError('Invalid requirement ID')
    start, end = 'agentapp-requirement-' + external_id, 'agentapp-end-' + external_id
    if body.count(start) != 1 or body.count(end) != 1:
        raise ValueError('Missing or ambiguous requirement')
    section = body.split(start, 1)[1].split(end, 1)[0].strip()
    match = re.fullmatch(r'```json\s*\n(.*?)\n```', section, re.S)
    if not match or len(match[1]) > 2000:
        raise ValueError('Expected one bounded JSON requirement')
    return validate_spec(manifest.parse(match[1]))


async def capture(external_id, remote=None):
    async def get(client):
        body = await read(client)
        spec = extract(body, external_id)
        return {'service': 'notion', 'page_id': PAGE, 'url': URL,
                'external_id': external_id, 'revision': digest(body),
                'spec': spec, 'content_sha256': digest(spec),
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'policy': 'immutable_snapshot'}
    if remote is not None:
        return await get(remote)
    async with connection() as client:
        return await get(client)


def validate_snapshot(source):
    if not isinstance(source, dict) or set(source) != {
            'service','page_id','url','external_id','revision','spec','content_sha256','captured_at','policy'}:
        raise ValueError('Invalid source snapshot')
    if (source['service'], source['page_id'], source['url'], source['policy']) != (
            'notion', PAGE, URL, 'immutable_snapshot'):
        raise ValueError('Unauthorized requirement source')
    if not re.fullmatch('[a-z0-9-]{1,80}', source['external_id']):
        raise ValueError('Invalid source identity')
    if not re.fullmatch('[a-f0-9]{64}', source['revision']):
        raise ValueError('Invalid source revision')
    if digest(validate_spec(source['spec'])) != source['content_sha256']:
        raise ValueError('Requirement snapshot changed')
    if datetime.fromisoformat(source['captured_at']).tzinfo is None:
        raise ValueError('Snapshot requires timezone')


def pilot(source):
    validate_snapshot(source)
    spec = source['spec']
    requirement = (
        'Implementa summarize_runs(records), una funcion pura Python. records debe ser una lista de diccionarios. '
        'Rechaza con TypeError contenedor o elemento invalido. Lee el campo ' + repr(spec['input_key']) + '. '
        'Normaliza strings con strip; ausente, vacio o no string se vuelve unknown. '
        'Devuelve total, by_status con claves ordenadas y needs_attention, contando exactamente los estados '
        + repr(spec['attention_states']) + ', distinguiendo mayusculas. No modifiques la entrada. '
        'No uses imports, E/S, globals, decorators, dunders ni otras funciones. '
        'Solo def summarize_runs(records): y su cuerpo. Builtins: isinstance, list, dict, str, len, sorted, TypeError. '
        'Metodos: get, strip, items, keys, values. No afirmes haber ejecutado pruebas.')
    tests = '''import unittest
import copy
from solution import summarize_runs
KEY = __KEY__
STATES = __STATES__
class RemoteSummaryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_runs([]), dict(total=0, by_status={}, needs_attention=0))
    def test_each_selected_state(self):
        self.assertEqual(summarize_runs([{KEY:s} for s in STATES]),
                         dict(total=len(STATES), by_status=dict(sorted((s,1) for s in STATES)), needs_attention=len(STATES)))
    def test_repeat_and_unknown(self):
        s=STATES[0]
        self.assertEqual(summarize_runs([{KEY:s},{KEY:s},{}]),
                         dict(total=3, by_status=dict(sorted([(s,2),('unknown',1)])), needs_attention=2))
    def test_spaces_and_case(self):
        s=STATES[0]
        self.assertEqual(summarize_runs([{KEY:' '+s+' '},{KEY:s.upper()}])['needs_attention'],1)
    def test_unknown_values(self):
        rows=[{}, {KEY:None},{KEY:True},{KEY:[]},{KEY:''},{KEY:'  '}]
        self.assertEqual(summarize_runs(rows),dict(total=6,by_status={'unknown':6},needs_attention=0))
    def test_order_unicode_and_preservation(self):
        rows=[{KEY:' zeta ', 'nested':[1]},{KEY:'á'}, {KEY:'alpha'}]
        before=copy.deepcopy(rows); result=summarize_runs(rows)
        self.assertEqual(rows,before)
        self.assertEqual(list(result['by_status']),['alpha','zeta','á'])
    def test_wrong_alias(self):
        alias='status' if KEY!='status' else 'recorded_status'
        self.assertEqual(summarize_runs([{alias:STATES[0]}])['by_status'],{'unknown':1})
    def test_non_attention(self):
        self.assertEqual(summarize_runs([{KEY:'other_state_not_selected'}])['needs_attention'],0)
    def test_bad_container(self):
        for x in [None, {}, (), 3, 'text']:
            with self.subTest(x=x), self.assertRaises(TypeError): summarize_runs(x)
    def test_bad_record(self):
        for x in [None, [], 3, 'text']:
            with self.subTest(x=x), self.assertRaises(TypeError): summarize_runs([{},x])
'''.replace('__KEY__', repr(spec['input_key'])).replace('__STATES__', repr(spec['attention_states']))
    return requirement, tests, 'remote_summary'


def bind(contract, source):
    from integration_linear import PROJECT
    from local_pilot import digest as text_digest
    validate_snapshot(source)
    contract['schema_version'] = '1.3'
    contract['allowed_resources']['linear_projects'] = [PROJECT]
    contract['allowed_resources']['notion_pages'] = [PAGE]
    contract['pipeline']['kind'] = 'remote_summary_v1'
    contract['pipeline']['source'] = source
    contract['pipeline']['linear_state_policy'] = 'report_only'
    requirement, tests, _ = pilot(source)
    contract['pipeline']['requirement_sha256'] = text_digest(requirement)
    contract['pipeline']['tests_sha256'] = text_digest(tests)
    contract['project']['name'] = 'remote-summary-phase2'
    return contract
