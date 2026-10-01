import copy
import json
from pathlib import Path
import pytest
import manifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def contract():
    return json.loads((ROOT/'config/pilot-manifest.json').read_text(encoding='utf-8'))


def test_valid_contract_and_zero_retries(contract,tmp_path):
    assert manifest.validate(contract,tmp_path)['commands']['unit']['max_retries'] == 0


@pytest.mark.parametrize('field', ['schema_version','project','runtimes','commands','paths','limits','roles','budgets','checks','approval_policy','deployment_policy','allowed_resources','secrets_refs'])
def test_missing_fields(contract,tmp_path,field):
    del contract[field]
    with pytest.raises(ValueError): manifest.validate(contract,tmp_path)


@pytest.mark.parametrize('value', [-1,0,None,True,float('inf'),float('nan'),'10'])
def test_bad_budget_values(contract,tmp_path,value):
    contract['budgets']['active_seconds'] = value
    with pytest.raises(ValueError): manifest.validate(contract,tmp_path)


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_bad_json(text):
    with pytest.raises(ValueError): manifest.parse(text)


@pytest.mark.parametrize('text', ['a: 1\na: 2','a: yes','a: 012','a: &x 1\nb: *x','a: .inf'])
def test_bad_yaml(text):
    with pytest.raises(ValueError): manifest.parse(text,True)


def test_yaml_valid_representation(contract,tmp_path):
    import yaml
    assert manifest.validate(manifest.parse(yaml.safe_dump(contract),True),tmp_path) == contract


@pytest.mark.parametrize('path', ['../escape','/absolute','C:/secret','a\\b','a/../b','a//b','a/','a.'])
def test_bad_paths(contract,tmp_path,path):
    contract['commands']['unit']['cwd'] = path
    with pytest.raises(ValueError): manifest.validate(contract,tmp_path)


def test_cross_references_extra_fields_and_permissions(contract,tmp_path):
    bad = copy.deepcopy(contract); bad['commands']['unit']['surprise'] = True
    with pytest.raises(ValueError): manifest.validate(bad,tmp_path)
    bad = copy.deepcopy(contract); bad['checks']['unit']['commands'] = ['missing']
    with pytest.raises(ValueError): manifest.validate(bad,tmp_path)
    bad = copy.deepcopy(contract); bad['checks'] = {}
    with pytest.raises(ValueError): manifest.validate(bad,tmp_path)
    bad = copy.deepcopy(contract); bad['paths']['denied_write'] = ['pilot']
    with pytest.raises(ValueError): manifest.validate(bad,tmp_path)
    bad = copy.deepcopy(contract); bad['allowed_resources']['coolify'] = ['foreign-resource']
    with pytest.raises(ValueError): manifest.validate(bad,tmp_path)
    assert not manifest.can_write({},tmp_path,'source.py')
    contract['paths']['allowed_write'] = ['.']
    assert not manifest.can_write(contract,tmp_path,'tests/example.py')
    assert manifest.can_write(contract,tmp_path,'pilot/example.py')


def test_base_moved(contract,tmp_path,monkeypatch):
    class Result:
        returncode=0
        stdout='a'*40
    monkeypatch.setattr(manifest.subprocess,'run',lambda *a,**k:Result())
    with pytest.raises(ValueError): manifest.verify_base(contract,tmp_path)


def test_authorization_ceiling(contract,tmp_path):
    contract['budgets']['active_seconds'] += 1
    with pytest.raises(ValueError): manifest.validate(contract,tmp_path)


def test_seeded_malformed_manifest_fuzz(contract,tmp_path):
    import random
    randomizer=random.Random(20260921)
    for _ in range(100):
        candidate=copy.deepcopy(contract)
        key=randomizer.choice(list(candidate))
        if randomizer.choice([True,False]):
            del candidate[key]
        else:
            # [] es válido para servicios y secretos deshabilitados.
            candidate[key]=randomizer.choice([None,True,123])
        with pytest.raises(ValueError):manifest.validate(candidate,tmp_path)
