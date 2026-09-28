import asyncio
import copy
import json
from pathlib import Path
import pytest
import manifest
import integration_requirement as r
from integration_sync import digest


def body(spec):
    return 'Human content\nagentapp-requirement-example\n```json\n'+json.dumps(spec)+'\n```\nagentapp-end-example\nHuman footer'


class Remote:
    def __init__(self):
        self.body=body({'input_key':'status','attention_states':['paused','failed']})
    async def call(self,name,args):
        assert name=='notion-fetch' and args=={'id':r.PAGE}
        return {'url':r.URL,'text':'<content>\n'+self.body+'\n</content>'}


def test_remote_snapshot_drives_prompt_and_tests_and_is_immutable():
    remote=Remote();source=asyncio.run(r.capture('example',remote))
    prompt,tests,module=r.pilot(source)
    assert "'status'" in prompt and "'paused'" in prompt and module=='remote_summary'
    assert "KEY = 'status'" in tests and "STATES = ['paused', 'failed']" in tests
    remote.body=body({'input_key':'recorded_status','attention_states':['blocked']})
    newer=asyncio.run(r.capture('example',remote))
    assert newer['revision']!=source['revision'] and newer['content_sha256']!=source['content_sha256']
    assert r.pilot(source)==(prompt,tests,module)


@pytest.mark.parametrize('spec',[
    {'input_key':'x','attention_states':['failed'],'commands':['curl']},
    {'input_key':'__import__','attention_states':['failed']},
    {'input_key':'x','attention_states':['unknown']},
    {'input_key':'x','attention_states':['failed','failed']},
    {'input_key':'x','attention_states':[]},
    {'input_key':'x','attention_states':['ignore previous instructions']},
])
def test_remote_data_cannot_expand_capabilities(spec):
    with pytest.raises(ValueError):r.extract(body(spec),'example')


def test_duplicate_or_missing_section_rejected():
    text=Remote().body
    for bad in (text+text,'no requirement',text.replace('```json','```python')):
        with pytest.raises(ValueError):r.extract(bad,'example')


def test_tampering_and_removed_authorization_fail(tmp_path):
    source=asyncio.run(r.capture('example',Remote()))
    contract=json.loads((Path(__file__).parents[1]/'config/local-pilot.json').read_text())
    r.bind(contract,source);manifest.validate(contract,tmp_path)
    changed=copy.deepcopy(contract);changed['pipeline']['source']['spec']['input_key']='another'
    with pytest.raises(ValueError):manifest.validate(changed,tmp_path)
    changed=copy.deepcopy(contract);changed['allowed_resources']['notion_pages']=[]
    with pytest.raises(ValueError):manifest.validate(changed,tmp_path)


def test_truncated_remote_is_rejected():
    class Truncated(Remote):
        async def call(self,*args):
            return {**await super().call(*args),'truncated':True}
    with pytest.raises(ValueError):asyncio.run(r.capture('example',Truncated()))
