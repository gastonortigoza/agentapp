import json
from pathlib import Path
import pytest
import manifest
from local_pilot import pilot_spec,digest,ATTENTION_TESTS
from publication_policy import check_trusted_tests

ROOT=Path(__file__).resolve().parents[1]


def contract():
    data=json.loads((ROOT/'config/local-pilot.json').read_text(encoding='utf-8'))
    data['schema_version']='1.2'
    data['pipeline']['kind']='attention_summary_v1'
    requirement,tests,_=pilot_spec(data)
    data['pipeline']['requirement_sha256']=digest(requirement)
    data['pipeline']['tests_sha256']=digest(tests)
    return data


def test_new_pilot_binds_its_own_requirement_and_tests(tmp_path,monkeypatch):
    monkeypatch.setattr(manifest,'verify_base',lambda *args:None)
    data=contract()
    manifest.validate(data,tmp_path)
    data['pipeline']['kind']='status_summary_v1'
    with pytest.raises(ValueError):manifest.validate(data,tmp_path)


def test_old_schema_cannot_select_new_pilot(tmp_path):
    data=contract();data['schema_version']='1.1'
    with pytest.raises(ValueError):manifest.validate(data,tmp_path)


def test_attention_tests_cannot_be_relaxed():
    trusted=ATTENTION_TESTS.replace('from solution import','from attention_summary import').encode()
    check_trusted_tests('tests/test_attention_summary.py',trusted)
    with pytest.raises(ValueError):check_trusted_tests('tests/test_attention_summary.py',trusted.replace(b'needs_attention',b'ignored'))
