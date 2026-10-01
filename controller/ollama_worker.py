"""Proceso de transporte local: stdlib, sin proxy, redirects ni herramientas."""
import json
import sys
import time
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Redirect no permitido')


def exchange(payload):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def read(path,data=None):
        request=urllib.request.Request('http://127.0.0.1:11434'+path,data=data,
            headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=payload['timeout_seconds']) as response:
            raw=response.read(128001)
            if len(raw)>128000:raise ValueError('Respuesta demasiado grande')
            return json.loads(raw)
    def check_model():
        models=read('/api/tags')['models']
        if not any(m.get('name')==payload['model'] and m.get('digest')==payload['digest'] for m in models):
            raise ValueError('Modelo local distinto o ausente')
    sent=False
    started=time.monotonic()
    try:
        check_model()
        body={'model':payload['model'],'messages':payload['messages'],'stream':False,'think':False,'keep_alive':'5m',
              'options':{'num_ctx':payload['context_tokens'],'num_predict':payload['output_tokens'],'temperature':0,'seed':42}}
        if payload.get('format') is not None:body['format']=payload['format']
        sent=True
        result=read('/api/chat',json.dumps(body).encode('utf-8'))
        check_model()
        return {'ok':True,'text':result.get('message',{}).get('content',''),
            'tool_calls':bool(result.get('message',{}).get('tool_calls')),
            'done':result.get('done'),'done_reason':result.get('done_reason'),
            'input_tokens':result.get('prompt_eval_count'),'output_tokens':result.get('eval_count'),
            'model_digest':payload['digest'],'load_duration_ns':result.get('load_duration'),
            'total_duration_ns':result.get('total_duration'),
            'elapsed_ms':round((time.monotonic()-started)*1000)}
    except Exception as exc:
        return {'ok':False,'sent':sent,'error_type':type(exc).__name__,
                'elapsed_ms':round((time.monotonic()-started)*1000)}


if __name__=='__main__':
    raw=sys.stdin.buffer.read(64001)
    if len(raw)>64000:raise SystemExit(2)
    print(json.dumps(exchange(json.loads(raw)),ensure_ascii=True))
