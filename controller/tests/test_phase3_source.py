import copy
import pytest
import controller_gate
import manifest
import phase3_source as source
import phase3_review as review
import phase3_execution as execution
import phase3_handoff as handoff
import phase3_prepare as preparation
from test_phase3_contract import result

@pytest.mark.parametrize('section',['public_api','public_profile'])
def test_source_correction_is_full_replacement_and_consumable_by_its_own_kind(tmp_path,monkeypatch,section):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'exact')
    monkeypatch.setattr(review,'ROOT',tmp_path)
    j=review.Journal(tmp_path/'review.sqlite')
    seed={'path':source.PATHS[section],'content':'incomplete original'}
    # Static markers deliberately are not a claim of executable correctness.
    code='/api/profiles/:profile_id eligible photos is_main created_at invalid_request profile_not_found request_id' if section=='public_api' else '/api/profiles/ encodeURIComponent photos AbortController Reintentar Perfil no disponible Volver al directorio whatsapp_url main alt='
    gold={'path':source.PATHS[section],'content':code}
    j.create('source',seed,section=section)
    lock,docs,_=preparation.load_bundle()
    def call(row,op,*a):
        request={'model':row['binding']['model'],'digest':row['binding']['digest'],'messages':[{'role':'user','content':'Bounded fixture'}],'format':review.output_schema(row,op,lock,docs)}
        op['_record_request'](request)
        body=gold if op['role']=='developer' else {'checks':{k:{'passed':True,'pointer':'/content','quote':row['candidate']['content'][:15]} for k in source.rules(section)},'findings':[]}
        return result(body,prompt_sha256=manifest.identity(request['messages']))
    row=review.run(j,'source',call)
    assert row['state']=='reviewed_application_file' and row['candidate']==gold
    assert row['calls']==3 and row['corrections']==1
    assert handoff.consume(j,'source',section)[0]==row
    assert j.records('source','plan_ops')[0]['candidate']==seed
    with pytest.raises(ValueError):handoff.consume(j,'source','application')

@pytest.mark.parametrize('ids',[None,'historical-single-review',{}, {'application':'a'}, {'application':'a','public_api':'b','public_profile':'c','extra':'d'}, {'application':'a','public_api':'a','public_profile':'a'}])
def test_missing_extra_duplicate_source_id_blocks_before_consuming_or_dispatch(ids,monkeypatch):
    monkeypatch.setattr(handoff,'consume',lambda *a:pytest.fail('Must block before consume'))
    with pytest.raises(ValueError):execution.consume_sources(None,ids,{})

def test_all_three_originals_bind_exact_paths_and_bytes(monkeypatch):
    ids={k:k for k in source.SECTIONS};files={p:k.encode() for k,p in source.PATHS.items()}
    monkeypatch.setattr(handoff,'consume',lambda j,run,section:({'candidate':{'path':source.PATHS[section],'content':section}},[],None,None))
    binding=execution.consume_sources(None,ids,files)
    assert set(binding)==set(source.SECTIONS)
    for section in source.SECTIONS:
        changed=copy.deepcopy(files);changed[source.PATHS[section]]+=b' '
        with pytest.raises(ValueError,match='Reviewed source'):execution.consume_sources(None,ids,changed)

def test_reviewed_payload_cannot_select_a_different_allowed_path(monkeypatch):
    monkeypatch.setattr(handoff,'consume',lambda *a:({'candidate':{'path':source.PATHS['public_api'],'content':'module'}},[],None,None))
    with pytest.raises(ValueError,match='Reviewed source'):execution.consume_sources(None,dict(zip(source.SECTIONS,source.SECTIONS)),{source.PATHS['public_api']:b'module'})

@pytest.mark.parametrize('section',['public_api','public_profile'])
def test_new_source_quotes_rules_and_schema_are_verified(section):
    candidate={'path':source.PATHS[section],'content':'original code'}
    value={'checks':{k:{'passed':True,'pointer':'/content','quote':'invented code'} for k in source.rules(section)},'findings':[]}
    with pytest.raises(ValueError,match='Invented'):source.validate_review(value,candidate,section)
    candidate['path']='../../host.py'
    assert source.defects(candidate,section)
