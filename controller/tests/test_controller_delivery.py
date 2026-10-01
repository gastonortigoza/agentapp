import copy
import hashlib
import json

import pytest

import controller_delivery as export
from local_control import sandbox
from test_local_agents import local, run


def test_closed_delivery_exports_with_evidence_without_reopening(local,tmp_path,monkeypatch):
    store,row,contract,*_=local
    assert run(local)['state']=='delivered'
    before=store.get(row['id']);events=store.events(row['id'])
    code,proof=export.delivery(store.path,row['id'])
    assert proof['parent_run_id']==row['id']
    assert proof['code_sha256']==hashlib.sha256(code.encode()).hexdigest()
    monkeypatch.setattr(export.github_publish,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(export.github_publish.connection,'configured_repo',lambda:contract['project']['repository'])
    seen=[];job=tmp_path/'jobs'/('b'*32);job.mkdir(parents=True)
    monkeypatch.setattr(export.github_publish,'job_path',lambda _:job)
    def prepare(source,names,branch):
        seen.append((names,branch))
        assert (source/names[0]).read_text(encoding='utf-8')==code
        assert 'from status_summary import' in (source/names[1]).read_text()
        return {'job_id':'b'*32,'manifest':{'job_id':'b'*32},'review_digest':'old'}
    monkeypatch.setattr(export.github_publish,'prepare',prepare)
    proposal=export.prepare(store.path,row['id'],'crewai/delivery')
    assert proposal['execution_authorized'] is False
    assert proposal['manifest']['controller_delivery']==proof
    assert proposal['review_digest']==export.github_publish.digest(proposal['manifest'])
    assert seen==[(list(export.FILES),'crewai/delivery')]
    assert store.get(row['id'])==before and store.events(row['id'])==events


def test_unfinished_delivery_cannot_be_exported(local):
    store,row,*_=local
    with pytest.raises(ValueError,match='cerrada'):export.delivery(store.path,row['id'])


def test_changed_candidate_blocks_before_preparing_git(local,monkeypatch):
    store,row,*_=local
    assert run(local)['state']=='delivered'
    (sandbox(row['id'])/'solution.py').write_text('modified')
    monkeypatch.setattr(export.github_publish,'prepare',lambda *_:pytest.fail('No Git preparation'))
    with pytest.raises(ValueError,match='cambiaron'):export.prepare(store.path,row['id'],'crewai/test')


def test_other_repository_is_rejected(local,monkeypatch):
    store,row,*_=local
    assert run(local)['state']=='delivered'
    monkeypatch.setattr(export.github_publish.connection,'configured_repo',lambda:'other/repo')
    monkeypatch.setattr(export.github_publish,'prepare',lambda *_:pytest.fail('No Git preparation'))
    with pytest.raises(ValueError,match='otro repositorio'):export.prepare(store.path,row['id'],'crewai/test')


def test_arbitrary_files_are_not_an_export_parameter(local):
    store,row,*_=local
    with pytest.raises(TypeError):
        export.prepare(store.path,row['id'],'crewai/test',files=['.github/workflows/ci.yml'])
