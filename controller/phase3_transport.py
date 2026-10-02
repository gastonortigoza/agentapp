"""Prospective bounded Ollama stream evidence, one dispatch per journal operation.

Artifacts are local originals, never product acceptance. Restart reads evidence;
partial text is not a completed response. Older non-streaming calls are untouched.
This worker uses only stdlib and the fixed loopback endpoint, without tools/proxy.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

PROTOCOL='agentapp.phase3-durable-transport/1'
ROOT=Path(__file__).resolve().parent
MAX_WIRE=2*1024*1024
MAX_LINE=8192
MAX_FRAMES=8192
MAX_TEXT=24000
NAMES={'request.json','started.json','dispatch.json','before.json','after.json','frames.ndjson','result.json'}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(data):return hashlib.sha256(data).hexdigest()


def parse(raw):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('Duplicate response member')
            value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def validate(payload):
    expected={'model','digest','messages','context_tokens','output_tokens','timeout_seconds','format','evidence'}
    if not isinstance(payload,dict) or set(payload)!=expected or len(canonical(payload))>64000:raise ValueError('Bad durable request')
    ref=payload['evidence']
    if (not isinstance(ref,dict) or set(ref)!={'protocol','run_id','sequence','binding_sha256'}
        or ref['protocol']!=PROTOCOL or not isinstance(ref['run_id'],str) or not re.fullmatch('[a-z0-9-]{1,100}',ref['run_id'])
        or type(ref['sequence']) is not int or not 0<=ref['sequence']<5
        or not isinstance(ref['binding_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',ref['binding_sha256'])):raise ValueError('Bad evidence owner')
    if (not isinstance(payload['model'],str) or not re.fullmatch('[a-zA-Z0-9_.:/-]{1,100}',payload['model'])
        or not isinstance(payload['digest'],str) or not re.fullmatch('[0-9a-f]{64}',payload['digest'])
        or any(type(payload[k]) is not int or not 1<=payload[k]<=bound for k,bound in
          (('context_tokens',32768),('output_tokens',5000),('timeout_seconds',150)))
        or not isinstance(payload['format'],dict) or not isinstance(payload['messages'],list)
        or not 1<=len(payload['messages'])<=5
        or any(not isinstance(m,dict) or set(m)!={'role','content'} or m['role'] not in ('system','user','assistant')
          or not isinstance(m['content'],str) for m in payload['messages'])):raise ValueError('Bad bounded local request')
    return payload


def folder(payload):
    validate(payload)
    ref=payload['evidence']
    # Changing payload or binding cannot create another dispatch for the same op.
    identity=sha(canonical({'run_id':ref['run_id'],'sequence':ref['sequence']}))
    path=ROOT/'.state/phase3/transport'/identity
    for parent in [ROOT,*path.relative_to(ROOT).parents]:
        checked=parent if parent==ROOT else ROOT/parent
        if checked.is_symlink() or checked.is_junction():raise ValueError('Evidence directory link')
    if path.is_symlink() or path.is_junction():raise ValueError('Evidence directory link')
    return path


def read(path,limit):
    if path.is_symlink() or path.is_junction() or not path.is_file():raise ValueError('Evidence file type')
    if path.stat().st_size>limit:raise ValueError('Evidence file too large')
    data=path.read_bytes()
    if len(data)>limit:raise ValueError('Evidence file grew beyond bound')
    return data


def write(path,value):
    data=canonical(value)
    with path.open('xb') as output:
        output.write(data);output.flush();os.fsync(output.fileno())


def prepare(payload):
    path=folder(payload);path.parent.mkdir(parents=True,exist_ok=True)
    try:path.mkdir()
    except FileExistsError:return path,False
    write(path/'request.json',payload)
    return path,True


def verified_request(payload,path):
    if path!=folder(payload) or read(path/'request.json',64000)!=canonical(payload):raise ValueError('Evidence request drift')
    if any(p.name not in NAMES or p.is_symlink() or p.is_junction() or not p.is_file() for p in path.iterdir()):
        raise ValueError('Unexpected evidence entry')


def transcript(raw,payload,partial=False):
    if len(raw)>MAX_WIRE:raise ValueError('Stream wire bound')
    lines=raw.splitlines(keepends=True)
    if len(lines)>MAX_FRAMES:raise ValueError('Stream frame bound')
    content=[];size=0;terminal=None;tools=False
    for index,line in enumerate(lines):
        if len(line)>MAX_LINE:raise ValueError('Stream line bound')
        if not line.endswith(b'\n'):
            if partial and index==len(lines)-1:break
            raise ValueError('Incomplete stream line')
        value=parse(line)
        if not isinstance(value,dict) or terminal is not None:raise ValueError('Invalid stream order')
        if value.get('model')!=payload['model'] or type(value.get('done')) is not bool:raise ValueError('Stream model/completion drift')
        message=value.get('message',{})
        if not isinstance(message,dict) or not isinstance(message.get('content',''),str):raise ValueError('Invalid content')
        text=message.get('content','');size+=len(text.encode())
        if size>MAX_TEXT:raise ValueError('Stream text bound')
        content.append(text);tools=tools or bool(message.get('tool_calls'))
        if value['done']:terminal=value
    return ''.join(content),terminal,tools


def inspection(payload):
    """Read-only: never start a worker or infer completion from partial frames."""
    path=folder(payload);verified_request(payload,path)
    raw=read(path/'frames.ndjson',MAX_WIRE) if (path/'frames.ndjson').exists() else b''
    text,final,tools=transcript(raw,payload,partial=True)
    artifact={'protocol':PROTOCOL,'identity':path.name,'request_sha256':sha(canonical(payload)),
      'frames_sha256':sha(raw),'wire_bytes':len(raw),'partial_text_bytes':len(text.encode())}
    unknown={'ok':False,'sent':True,'error_type':'incomplete_durable_capture','partial_text':text,'artifact':artifact}
    if not (path/'result.json').exists():return unknown
    envelope=parse(read(path/'result.json',64000));result=envelope['result']
    if envelope['request_sha256']!=artifact['request_sha256'] or envelope['frames_sha256']!=artifact['frames_sha256']:
        raise ValueError('Durable completion drift')
    if not isinstance(result,dict) or type(result.get('ok')) is not bool:raise ValueError('Durable result type')
    if result['ok']:
        if final is None or not raw.endswith(b'\n'):raise ValueError('No final stream frame')
        marker={'request_sha256':artifact['request_sha256']}
        if any(parse(read(path/(stage+'.json'),4096))!=marker for stage in ('started','dispatch')):
            raise ValueError('Dispatch ownership evidence drift')
        identity={'model':payload['model'],'digest':payload['digest'],'verified':True}
        if any(parse(read(path/(stage+'.json'),4096))!=identity for stage in ('before','after')):raise ValueError('Model identity evidence drift')
        expected=completion(payload,text,final,tools,result['elapsed_ms'])
        if result!=expected:raise ValueError('Result differs from original stream')
    elif (type(result.get('sent')) is not bool or not isinstance(result.get('error_type'),str)
          or 'text' in result or 'input_tokens' in result or 'output_tokens' in result):raise ValueError('Failure implies false completion')
    return result|{'artifact':artifact,**({'partial_text':text} if not result['ok'] else {})}


def completion(payload,text,final,tools,elapsed):
    if (type(elapsed) is not int or not 0<=elapsed<=payload['timeout_seconds']*1000
        or final.get('done_reason') not in ('stop','length')
        or any(type(final.get(k)) is not int or not 0<=final[k]<=payload[bound] for k,bound in
           (('prompt_eval_count','context_tokens'),('eval_count','output_tokens')))):raise ValueError('Unverified final usage/deadline')
    return {'ok':True,'text':text,'tool_calls':tools,'done':True,'done_reason':final['done_reason'],
      'input_tokens':final['prompt_eval_count'],'output_tokens':final['eval_count'],'model_digest':payload['digest'],
      'load_duration_ns':final.get('load_duration'),'total_duration_ns':final.get('total_duration'),'elapsed_ms':elapsed}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Redirect denied')


def exchange(payload):
    path=folder(payload);verified_request(payload,path)
    try:write(path/'started.json',{'request_sha256':sha(canonical(payload))})
    except FileExistsError:return inspection(payload)
    started=time.monotonic();sent=False;wire=bytearray()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def remaining():
        value=payload['timeout_seconds']-(time.monotonic()-started)
        if value<=0:raise TimeoutError('Durable stream deadline')
        return value
    def check(stage):
        with opener.open('http://127.0.0.1:11434/api/tags',timeout=remaining()) as response:
            raw=response.read(128001)
            if len(raw)>128000:raise ValueError('Model catalogue bound')
            tags=parse(raw)
        if not any(m.get('name')==payload['model'] and m.get('digest')==payload['digest'] for m in tags['models']):
            raise ValueError('Pinned model missing or changed')
        write(path/(stage+'.json'),{'model':payload['model'],'digest':payload['digest'],'verified':True})
    try:
        check('before')
        body={'model':payload['model'],'messages':payload['messages'],'stream':True,'think':False,'keep_alive':'5m',
          'format':payload['format'],'options':{'num_ctx':payload['context_tokens'],'num_predict':payload['output_tokens'],'temperature':0,'seed':42}}
        write(path/'dispatch.json',{'request_sha256':sha(canonical(payload))})
        sent=True
        request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=canonical(body),headers={'Content-Type':'application/json'})
        with (path/'frames.ndjson').open('xb') as output,opener.open(request,timeout=remaining()) as response:
            frames=0
            while True:
                remaining();line=response.readline(MAX_LINE+1)
                if not line:break
                if len(line)>MAX_LINE or len(wire)+len(line)>MAX_WIRE or frames>=MAX_FRAMES:raise ValueError('Stream capture bound')
                # Store the original before interpreting it; failures retain bytes.
                output.write(line);output.flush();os.fsync(output.fileno());wire.extend(line);frames+=1
                remaining()
                value=parse(line)
                if isinstance(value,dict) and value.get('done') is True:break
        text,final,tools=transcript(bytes(wire),payload)
        if final is None:raise ValueError('Stream ended without completion')
        check('after');remaining()
        result=completion(payload,text,final,tools,round((time.monotonic()-started)*1000))
    except Exception as exc:
        result={'ok':False,'sent':sent,'error_type':type(exc).__name__,'elapsed_ms':round((time.monotonic()-started)*1000)}
    write(path/'result.json',{'request_sha256':sha(canonical(payload)),'frames_sha256':sha(bytes(wire)),'result':result})
    return inspection(payload)


def transport(payload,timeout):
    if type(timeout) is not int or not 1<=timeout<=payload['timeout_seconds']+1:raise ValueError('Transport timeout exceeds bound')
    path,created=prepare(payload)
    if not created:return inspection(payload)
    env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','TEMP','TMP') if k in os.environ};env['PYTHONUTF8']='1'
    try:
        subprocess.run([sys.executable,'-I',str(Path(__file__).resolve())],input=canonical(payload),
          capture_output=True,timeout=timeout,env=env,check=True)
    except (subprocess.TimeoutExpired,subprocess.CalledProcessError,OSError):
        # subprocess.run kills/waits its owned worker on timeout. Never re-dispatch.
        pass
    return inspection(payload)


if __name__=='__main__':
    raw=sys.stdin.buffer.read(64001)
    if len(raw)>64000:raise SystemExit(2)
    print(json.dumps(exchange(validate(parse(raw))),ensure_ascii=True))
