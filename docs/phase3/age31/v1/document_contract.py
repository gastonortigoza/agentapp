"""Schemas and cross-section checks for proposed documents, never executable policy."""
import json
from jsonschema import Draft202012Validator

def obj(fields):
    return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
def text(): return {'type':'string','minLength':1,'maxLength':2000}
def array(item,minimum=1):return {'type':'array','items':item,'minItems':minimum,'maxItems':30}
def strings():return array(text())
def number():return {'type':'integer','minimum':1,'maximum':10000000}
def flag():return {'type':'boolean'}
def enum(*values):return {'type':'string','enum':list(values)}
def mapping(minimum=1):return {'type':'object','minProperties':minimum,'maxProperties':30,'additionalProperties':text()}

def document_schema(cfg):
    route=obj({'method':enum('GET','POST','PATCH','PUT','DELETE'),'path':text(),'auth':enum('public','owner'),
        'request':mapping(0),'response':mapping(0),'status':number(),'errors':array({'type':'integer'}),'rule':text()})
    table=obj({'columns':mapping(),'primary_key':strings(),'unique':array(strings(),0),'foreign_keys':array(text(),0)})
    command=obj({'argv':strings(),'cwd':text(),'timeout_seconds':number(),'retries':{'type':'integer','minimum':0,'maximum':2},'network':text()})
    service=obj({'bind':text(),'readiness':text()})
    sections={k:{'type':'string','minLength':40,'maxLength':14000} for k in cfg['sections']}
    sections['api']=obj({'dtos':{'type':'object','properties':{k:mapping() for k in ['PublicCard','PublicProfile','OwnProfile','Photo','Subscription','Session']},'required':['PublicCard','PublicProfile','OwnProfile','Photo','Subscription','Session'],'additionalProperties':False},
        'routes':array(route),'listing':obj({'filters':strings(),'order':strings(),'pagination':text(),'eligibility':text(),'deduplication':text()}),'error_shape':text()})
    names=['users','profiles','countries','provinces','zones','photos','plans','subscriptions','idempotency','sessions','password_resets']
    sections['data']=obj({'tables':obj({k:table for k in names}),'geography_consistency':text(),'main_photo_constraint':text(),'transaction_lock':text(),'restart':text()})
    sections['subscriptions']=obj({'endpoint':text(),'production_guard':enum('always_reject','check_client_simulated'),
        'origin_source':enum('server','client'),'allowed_body_fields':strings(),'catalog_source':enum('server','client'),
        'lock_table':text(),'lock_key':text(),'transaction':strings(),'idempotency':obj({'scope':strings(),'payload_hash':text(),'same_payload':text(),'different_payload_status':number(),'ttl_hours':number(),'after_ttl':text()}),
        'test_duration_days':number(),'dates':strings(),'notice':text(),'rebill_calls':{'type':'integer','minimum':0},'cards':flag(),'restart':text(),'real_billing':text()})
    sections['manifest']=obj({'schema':text(),'revision':number(),'deployment':enum('disabled','enabled'),'enabled':flag(),
        'unverified_blocks_enablement':strings(),'runtimes':mapping(),'commands':obj({k:command for k in ['fe','be','fe_build','be_build','unit','e2e','migrate']}),
        'services':obj({k:service for k in ['fe','be','db','objects','mail']}),'healthcheck':obj({'timeout_seconds':number(),'retries':number()}),
        'filesystem':obj({'root':text(),'allow':strings(),'deny':strings()}),'network':obj({'allow':strings(),'default':enum('deny','allow'),'package_install':text()}),
        'budgets_proposed':obj({'cpu':number(),'ram_mib':number(),'disk_gib':number(),'db_connections':number(),'max_run_seconds':number(),'external_cost':{'type':'integer','minimum':0}}),
        'checks_proposed':obj({'unit_failures':{'type':'integer','minimum':0},'e2e_failures':{'type':'integer','minimum':0},'build_exit':{'type':'integer'},'migration_exit':{'type':'integer'},'response_p95_ms':number(),'error_rate_max':{'type':'number','minimum':0,'maximum':1}}),
        'secrets_ref':strings(),'note':text()})
    return obj(sections)

def normalize(value):
    return {k:json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')) if isinstance(v,dict) else v for k,v in value.items()}

def structure_errors(value,cfg):
    return sorted(({'path':list(e.absolute_path),'message':e.message} for e in Draft202012Validator(document_schema(cfg)).iter_errors(value)),key=lambda e:str(e['path']))

def check_document(value,cfg):
    """Return corrective findings. Sound checks cover only explicit machine-readable facts."""
    failures=[]
    def fail(k,issue,fix):failures.append({'criterion':k,'issue':issue,'fix':fix})
    errors=structure_errors(value,cfg)
    if errors:
        for k in cfg['sections']:
            subset=[e for e in errors if not e['path'] or e['path'][0]==k]
            if subset:fail(k,'Estructura inv�lida: '+str(subset[:2])[:850],'Reescribir esta secci�n como objeto conforme al esquema obligatorio; no incluir instrucciones de correcci�n como contrato.')
    def valid(k):return not any(not e['path'] or e['path'][0]==k for e in errors)
    s=value['subscriptions'] if valid('subscriptions') else None
    d=value['data'] if valid('data') else None
    m=value['manifest'] if valid('manifest') else None
    a=value['api'] if valid('api') else None
    if s and (s['production_guard']!='always_reject' or s['origin_source']!='server' or s['catalog_source']!='server' or s['rebill_calls']!=0 or s['cards']):
        fail('subscriptions','Simulaci�n permite origen/guardia o cobros incorrectos.','Rechazar siempre endpoint en producci�n; origen/cat�logo de servidor, cero Rebill y tarjetas.')
    if s and (s['lock_table']!='users' or s['lock_key']!='user_id' or s['idempotency']['scope']!=['user_id','key'] or s['idempotency']['different_payload_status']!=409 or s['allowed_body_fields']!=['plan_id']):
        fail('subscriptions','�mbito, bloqueo de primera activaci�n o conflicto incorrectos.','Bloquear users por user_id en transacci�n; scope [user_id,key]; cuerpo solo plan_id; distinto payload409.')
    t=d['tables'] if d else None;idem=t['idempotency'] if t else None
    if idem and (idem['primary_key']!=['user_id','key'] or any('payload_hash' in u or u==['key'] for u in idem['unique']) or not {'payload_hash','response_status','response_body','expires_at'}<=set(idem['columns'])):
        fail('data','Unicidad/respuesta idempotente no corresponde a cuenta y clave.','PK [user_id,key]; payload_hash NO unique; guardar payload_hash,response_status,response_body,expires_at.')
    if t and any('idempotency_key' in u or 'payload_hash' in u for u in t['subscriptions']['unique']):
        fail('data','Suscripciones todav�a tienen clave/hash global �nico.','Quitar unicidad global de clave/hash; idempotencia en tabla separada.')
    if s and t and (len(set(s['dates']))<2 or any(name not in t['subscriptions']['columns'] for name in s['dates'])):
        fail('data','Fechas de vigencia no coinciden entre protocolo y tabla.','Definir dos fechas distintas de inicio/fin y usar los mismos nombres en subscriptions.dates y tabla subscriptions.')
    if s and a and any(name not in a['dtos']['Subscription'] for name in s['dates']):
        fail('api','Fechas de vigencia no coinciden entre protocolo y DTO.','Usar los mismos nombres de fechas en protocolo y DTO Subscription; no imponer un nombre distinto si ya son consistentes.')
    if s and a and s['endpoint'].removeprefix('POST ') not in [r['path'] for r in a['routes'] if r['method']=='POST' and r['auth']=='owner']:
        fail('api','Endpoint de activaci�n y protocolo de suscripciones no coinciden.','Declarar la misma ruta POST owner en api y subscriptions.')
    for route in a['routes'] if a else []:
        for field_type in list(route['request'].values())+list(route['response'].values()):
            if field_type.startswith('$') and field_type[1:].removesuffix('[]') not in a['dtos']:
                fail('api','Referencia DTO inexistente: '+field_type,'Usar $NombreDTO definido, o un tipo primitivo concreto.');break
    if m and (m['deployment']!='disabled' or m['enabled'] or m['network']['default']!='deny' or m['budgets_proposed']['external_cost']!=0):
        fail('manifest','Manifiesto habilita ejecuci�n o exposici�n no autorizadas.','deployment disabled, enabled false, red deny por defecto y coste externo0; es solo propuesta.')
    from pathlib import PurePosixPath
    for name,c in m['commands'].items() if m else []:
        p=PurePosixPath(c['cwd'].replace('\\','/'))
        if p.is_absolute() or '..' in p.parts or ':' in c['cwd'] or not c['argv']:
            fail('manifest','cwd inv�lido en '+name,'Usar cwd relativo frontend/backend sin ../ ni rutas absolutas; argv array.');break
    # One finding per criterion is enough to trigger correction; retain all concrete issues in fix.
    merged={}
    for f in failures:
        k=f['criterion']
        if k not in merged:merged[k]=f
        else:
            merged[k]['issue']=(merged[k]['issue']+' '+f['issue'])[:1200]
            merged[k]['fix']=(merged[k]['fix']+' '+f['fix'])[:1200]
    return list(merged.values())
