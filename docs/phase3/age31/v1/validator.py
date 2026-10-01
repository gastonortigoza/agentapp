"""Document validation only. No command dispatcher, no generated code execution."""
import copy,json,re,sys
from pathlib import Path,PurePosixPath
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'age31-loop-v10'))
from document_contract import check_document
def check_contract(doc,cfg,cases=None):
    # Nullable DTO is a defined union, not an unknown DTO name.
    normalized=copy.deepcopy(doc)
    for r in normalized.get('api',{}).get('routes',[]):
        for fields in (r.get('request',{}),r.get('response',{})):
            for k,v in fields.items():
                if isinstance(v,str) and v.startswith('$') and v.endswith('|null'):fields[k]=v[:-5]
    findings=check_document(normalized,cfg)
    def fail(k,issue):findings.append(dict(criterion=k,issue=issue,fix='Corregir contrato; no habilitar ejecución.'))
    if any(f['issue'].startswith('Estructura inválida:') for f in findings):return findings
    routes=doc['api']['routes'];keys=[(r['method'],r['path']) for r in routes]
    if len(set(keys))!=len(keys):fail('api','Duplicated method/path')
    if any(not r['path'].startswith('/api/') or '?' in r['path'] for r in routes):fail('api','Invalid route path')
    tables=doc['data']['tables']
    for name,t in tables.items():
        if not set(t['primary_key'])<=set(t['columns']):fail('data','PK column absent in '+name)
        for unique in t['unique']:
            if not set(unique)<=set(t['columns']):fail('data','Unique column absent in '+name)
        for fk in t['foreign_keys']:
            match=re.search(r'^(.+?) REFERENCES (\w+)\(([^)]+)\)',fk)
            if not match:fail('data','Malformed FK '+fk);continue
            local,target,remote=match.groups();lc=[x.strip() for x in local.strip('()').split(',')];rc=[x.strip() for x in remote.split(',')]
            if target not in tables or not set(lc)<=set(t['columns']) or not set(rc)<=set(tables.get(target,{}).get('columns',{})) or len(lc)!=len(rc):fail('data','Invalid FK '+name+' '+fk)
            elif rc!=tables[target]['primary_key'] and rc not in tables[target]['unique']:fail('data','FK target lacks matching unique '+fk)
    m=doc['manifest']
    exact={
      'fe':(['npm','run','dev','--','--host','127.0.0.1'],'frontend'),
      'be':(['npm','run','dev'],'backend'),
      'fe_build':(['npm','run','build'],'frontend'),
      'be_build':(['npm','run','build'],'backend'),
      'unit':(['npm','run','test:unit'],'.'),
      'e2e':(['npm','run','test:e2e'],'frontend'),
      'migrate':(['npm','run','db:migrate'],'backend')}
    for k,c in m['commands'].items():
        if (c['argv'],c['cwd'])!=exact[k]:fail('manifest','Command differs from proposed audited argv/cwd '+k)
        if c['retries']!=0:fail('manifest','Unexpected command retry '+k)
    # Arbitrary IPs/domains, shell launchers and extra scripts cannot enter this proposal.
    expected={'127.0.0.1:'+str(p) for p in [5173,3000,5432,9000,8025,1025]}
    if set(m['network']['allow'])!=expected:fail('manifest','Network references do not match declared services')
    if any(not s['bind'].startswith('127.0.0.1:') for s in m['services'].values()):fail('manifest','Service not loopback')
    if any(not re.fullmatch('[A-Z][A-Z0-9_]*',v) for v in m['secrets_ref']):fail('manifest','Secret value instead of name reference')
    public=set(doc['api']['dtos']['PublicCard'])|set(doc['api']['dtos']['PublicProfile'])
    if public & {'email','birth_date','user_id','password_hash','refresh_hash','access_token'}:fail('auth','Private field in public DTO')
    if cases:
        for c in cases:
            if (c['method'],c['path']) not in keys:fail('acceptance','Case refers to nonexistent route '+c['id'])
            if c['status']!='specified_not_executed':fail('acceptance','Unproven product test claim '+c['id'])
    return findings
def check_manifest_binding(envelope,doc,policy,business,contract_bytes,policy_bytes,business_bytes):
    import hashlib
    errors=[]
    h=lambda b:hashlib.sha256(b).hexdigest()
    if envelope.get('schema')!='agentapp.bound-execution-manifest/1' or envelope.get('revision')!=1:errors.append('binding schema/revision')
    if envelope.get('manifest')!=doc['manifest']:errors.append('manifest differs from contract')
    req=envelope.get('requirement',{});pol=envelope.get('effective_policy',{})
    if req.get('id')!=business['requirement_id'] or req.get('revision')!='technical-contract-v1' or req.get('contract_sha256')!=h(contract_bytes) or req.get('business_source_sha256')!=h(business_bytes):errors.append('requirement identity/hash')
    if pol.get('id')!=policy['id'] or pol.get('revision')!=policy['revision'] or pol.get('sha256')!=h(policy_bytes):errors.append('policy identity/hash')
    return errors
