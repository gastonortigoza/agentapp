"""Synthetic stream protocol only: no real model calls or product acceptance."""
import copy
import io
import json
from pathlib import Path
import subprocess
import types

import pytest
import controller_gate
import phase3_transport as transport
import phase3_review as review
import phase3_roles as roles
import phase3_auth_source as auth
import phase3_prepare as preparation
import agent_runtime
import lab


@pytest.fixture
def payload(tmp_path,monkeypatch):
    monkeypatch.setattr(transport,'ROOT',tmp_path)
    return {'model':'synthetic-model','digest':'a'*64,'messages':[{'role':'user','content':'Untrusted synthetic source'}],
      'context_tokens':32768,'output_tokens':5000,'timeout_seconds':150,'format':{'type':'object'},
      'evidence':{'protocol':transport.PROTOCOL,'run_id':'original-review','sequence':0,'binding_sha256':'b'*64}}


def frame(payload,text='',done=False,**changes):
    return transport.canonical({'model':payload['model'],'message':{'role':'assistant','content':text},'done':done,
      **({'done_reason':'stop','prompt_eval_count':50,'eval_count':10,'load_duration':20,'total_duration':30} if done else {}),**changes})+b'\n'


class Response(io.BytesIO):
    def __init__(self,data,fail=False):super().__init__(data);self.fail=fail
    def readline(self,*args):
        value=super().readline(*args)
        if self.fail and not value:raise TimeoutError('synthetic timeout')
        return value


def server(payload,monkeypatch,stream,fail=False,changed_model=False):
    calls=[]
    class Opener:
        def open(self,request,timeout):
            url=request if isinstance(request,str) else request.full_url;calls.append(url)
            assert 0<timeout<=150
            if url.endswith('/api/tags'):
                digest='c'*64 if changed_model and len(calls)>1 else payload['digest']
                return Response(transport.canonical({'models':[{'name':payload['model'],'digest':digest}]}))
            body=json.loads(request.data)
            assert body['stream'] is True and body['think'] is False and 'evidence' not in body
            assert body['options']['num_predict']==5000 and body['options']['num_ctx']==32768
            return Response(stream,fail)
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *args:Opener())
    return calls


def dispatch(payload):
    path,created=transport.prepare(payload);assert created
    return path,transport.exchange(payload)


def test_complete_stream_is_byte_original_and_restarts_without_any_http(payload,monkeypatch):
    raw=frame(payload,'{"code":')+frame(payload,'"synthetic"}')+frame(payload,done=True)
    calls=server(payload,monkeypatch,raw);path,result=dispatch(payload)
    assert result['ok'] and result['text']=='{"code":"synthetic"}' and result['done_reason']=='stop'
    assert result['input_tokens']==50 and result['output_tokens']==10
    assert (path/'frames.ndjson').read_bytes()==raw
    assert result['artifact']['frames_sha256']==transport.sha(raw)
    assert len(calls)==3
    assert transport.transport(payload,151)==result and transport.exchange(payload)==result and len(calls)==3


def test_timeout_retains_partial_original_without_inventing_completion_or_usage(payload,monkeypatch):
    raw=frame(payload,'{"content":"unfinished')
    calls=server(payload,monkeypatch,raw,fail=True);path,result=dispatch(payload)
    assert not result['ok'] and result['sent'] and result['error_type']=='TimeoutError'
    assert result['partial_text']=='{"content":"unfinished' and 'text' not in result
    assert 'input_tokens' not in result and 'output_tokens' not in result
    assert (path/'frames.ndjson').read_bytes()==raw
    assert transport.transport(payload,151)==result and len(calls)==2


def test_model_change_after_stream_prevents_verification_and_keeps_original(payload,monkeypatch):
    raw=frame(payload,'complete')+frame(payload,done=True)
    server(payload,monkeypatch,raw,changed_model=True);path,result=dispatch(payload)
    assert not result['ok'] and result['partial_text']=='complete' and result['sent']
    assert not (path/'after.json').exists() and (path/'frames.ndjson').read_bytes()==raw


def test_truncation_has_original_length_reason_and_does_not_become_source_acceptance(payload,monkeypatch):
    server(payload,monkeypatch,frame(payload,'partial')+frame(payload,done=True,done_reason='length'))
    _,result=dispatch(payload)
    assert result['ok'] and result['done'] and result['done_reason']=='length'


@pytest.mark.parametrize('mutation',['payload','request','frames','result','identity','extra_file'])
def test_original_or_owner_drift_blocks_replay_without_dispatch(payload,monkeypatch,mutation):
    calls=server(payload,monkeypatch,frame(payload,'data')+frame(payload,done=True));path,_=dispatch(payload)
    if mutation=='payload':payload['evidence']['binding_sha256']='d'*64
    elif mutation=='request':(path/'request.json').write_text('{}')
    elif mutation=='frames':(path/'frames.ndjson').write_bytes(frame(payload,'different')+frame(payload,done=True))
    elif mutation=='result':
        value=json.loads((path/'result.json').read_bytes());value['result']['text']='forged';(path/'result.json').write_bytes(transport.canonical(value))
    elif mutation=='identity':(path/'after.json').write_text('{}')
    else:(path/'foreign.txt').write_text('extra')
    with pytest.raises((ValueError,KeyError)):transport.transport(payload,151)
    assert len(calls)==3


def test_parent_timeout_after_worker_completion_reads_original_without_second_worker(payload,monkeypatch):
    calls=server(payload,monkeypatch,frame(payload,'received')+frame(payload,done=True));workers=[]
    def process(*args,**kwargs):
        workers.append(1);transport.exchange(json.loads(kwargs['input']))
        raise subprocess.TimeoutExpired(args[0],151)
    monkeypatch.setattr(transport.subprocess,'run',process)
    result=transport.transport(payload,151)
    assert result['ok'] and result['text']=='received' and len(workers)==1
    assert transport.transport(payload,151)==result and len(workers)==1 and len(calls)==3


def test_worker_interrupted_after_dispatch_retains_prefix_and_no_restart_worker(payload,monkeypatch):
    workers=[]
    def process(*args,**kwargs):
        workers.append(1);path=transport.folder(payload)
        transport.write(path/'started.json',{'request_sha256':transport.sha(transport.canonical(payload))})
        transport.write(path/'dispatch.json',{'request_sha256':transport.sha(transport.canonical(payload))})
        (path/'frames.ndjson').write_bytes(frame(payload,'partial')+b'{"half')
        raise subprocess.TimeoutExpired(args[0],151)
    monkeypatch.setattr(transport.subprocess,'run',process)
    result=transport.transport(payload,151)
    assert not result['ok'] and result['sent'] and result['partial_text']=='partial'
    assert transport.transport(payload,151)==result and len(workers)==1


def test_final_frame_without_post_identity_and_result_stays_unverified(payload,monkeypatch):
    path,_=transport.prepare(payload);(path/'frames.ndjson').write_bytes(frame(payload,'complete')+frame(payload,done=True))
    monkeypatch.setattr(transport.subprocess,'run',lambda *a,**k:pytest.fail('No resend'))
    result=transport.transport(payload,151)
    assert not result['ok'] and 'input_tokens' not in result and result['partial_text']=='complete'


@pytest.mark.parametrize('field,value',[('output_tokens',5001),('timeout_seconds',151),('context_tokens',32769),('evidence',{'run_id':'../escape'}),('host','http://external')])
def test_invalid_scope_or_larger_budget_is_rejected_before_io(payload,tmp_path,monkeypatch,field,value):
    payload[field]=value
    monkeypatch.setattr(transport.subprocess,'run',lambda *a,**k:pytest.fail('Invalid request must not dispatch'))
    with pytest.raises((ValueError,KeyError)):transport.transport(payload,151)
    assert not (tmp_path/'.state').exists()


@pytest.mark.parametrize('case',['line','wire','frames','text','usage','reason','model','missing_final','duplicate_json'])
def test_stream_limits_or_unverifiable_final_cannot_be_accepted(payload,monkeypatch,case):
    if case=='line':raw=b'x'*(transport.MAX_LINE+1)
    elif case=='wire':
        monkeypatch.setattr(transport,'MAX_WIRE',20);raw=frame(payload,'larger than wire limit')
    elif case=='frames':
        monkeypatch.setattr(transport,'MAX_FRAMES',2);raw=frame(payload,'first')+frame(payload,'second')+frame(payload,done=True)
    elif case=='text':
        monkeypatch.setattr(transport,'MAX_TEXT',3);raw=frame(payload,'large')+frame(payload,done=True)
    elif case=='usage':raw=frame(payload,done=True,eval_count=5001)
    elif case=='reason':raw=frame(payload,done=True,done_reason='unknown')
    elif case=='model':raw=frame(payload,done=True,model='other-model')
    elif case=='missing_final':raw=frame(payload,'partial')
    else:raw=b'{"done":false,"done":true}\n'
    server(payload,monkeypatch,raw)
    path,_=transport.prepare(payload)
    try:result=transport.exchange(payload)
    except (ValueError,KeyError):result={'ok':False}
    assert not result['ok']
    assert (path/'started.json').exists() and (path/'dispatch.json').exists()


def test_success_without_dispatch_owner_marker_is_rejected(payload,monkeypatch):
    server(payload,monkeypatch,frame(payload,'complete')+frame(payload,done=True));path,_=dispatch(payload)
    (path/'dispatch.json').unlink()
    with pytest.raises(ValueError):transport.inspection(payload)


def test_directory_and_file_links_are_rejected(payload,tmp_path):
    path,_=transport.prepare(payload);foreign=tmp_path/'foreign';foreign.mkdir()
    link=path/'frames.ndjson'
    try:link.symlink_to(foreign/'original')
    except OSError:pytest.skip('OS does not permit synthetic symlink creation')
    with pytest.raises(ValueError):transport.inspection(payload)


def test_wall_clock_cutoff_is_total_even_when_fragments_arrive(payload,monkeypatch):
    clock=[0.0];monkeypatch.setattr(transport.time,'monotonic',lambda:clock[0])
    class Slow(Response):
        def readline(self,*args):clock[0]+=151;return super().readline(*args)
    class Opener:
        def open(self,request,timeout):
            if isinstance(request,str):return Response(transport.canonical({'models':[{'name':payload['model'],'digest':payload['digest']}]}))
            return Slow(frame(payload,'late')+frame(payload,done=True))
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *a:Opener())
    _,result=dispatch(payload)
    assert not result['ok'] and result['error_type']=='TimeoutError' and result['partial_text']=='late'


def test_new_auth_role_selects_bound_durable_transport_legacy_role_keeps_old_transport(tmp_path,monkeypatch):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'synthetic-green')
    monkeypatch.setattr(review,'ROOT',tmp_path)
    journal=review.Journal(tmp_path/'review.sqlite')
    row=journal.create('prospective-auth',{'path':auth.PATHS['auth_api'],'content':''},section='auth_api',initial_proposal=True)
    assert row['binding']['transport_protocol']==transport.PROTOCOL
    op=journal.reserve(row['id']);requests=[];durable=[];legacy=[]
    op['_record_request']=requests.append
    answer={'ok':True,'text':'{}'}
    monkeypatch.setattr(transport,'transport',lambda payload,timeout:durable.append((payload,timeout)) or copy.deepcopy(answer))
    monkeypatch.setattr(agent_runtime,'transport',lambda payload,timeout:legacy.append((payload,timeout)) or copy.deepcopy(answer))
    class Crew:
        def __init__(self,agents,**kwargs):self.agent=agents[0]
        def kickoff(self):self.agent.llm.call([{'role':'user','content':'Synthetic bounded task'}])
    monkeypatch.setattr(lab,'Crew',Crew)
    lock,docs,_=preparation.load_bundle()
    result=roles.call_role(row,op,lock,docs)
    assert result['ok'] and result['text']=='{}' and result['prompt_sha256']==transport.sha(transport.canonical(requests[0]['messages']))
    assert len(durable)==1 and not legacy and requests[0]==durable[0][0]
    assert requests[0]['evidence']=={'protocol':transport.PROTOCOL,'run_id':row['id'],'sequence':0,'binding_sha256':row['binding_sha256']}
    old=copy.deepcopy(row);old['binding'].pop('transport_protocol');roles.call_role(old,op,lock,docs)
    assert len(legacy)==1 and len(durable)==1 and 'evidence' not in legacy[0][0]
