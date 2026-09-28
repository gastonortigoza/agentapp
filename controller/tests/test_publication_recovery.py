from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import controller_cli
import git_control
import github_publish as publish
from publication_journal import Journal


@pytest.fixture
def publication(tmp_path, monkeypatch):
    remote=tmp_path/'remote.git'
    subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    source=tmp_path/'source';source.mkdir()
    (source/'hello.py').write_text('print("hello")\n')
    monkeypatch.setattr(publish,'ROOT',source)
    monkeypatch.setattr(publish,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(git_control,'WORKSPACE',tmp_path)
    monkeypatch.setattr(publish,'enabled',lambda:'owner/repo')
    monkeypatch.setattr(publish.connection,'configured_repo',lambda:'owner/repo')
    monkeypatch.setattr(publish.connection,'api',lambda _: {'default_branch':'main'})
    monkeypatch.setattr(publish.controller_gate,'require_green',lambda:None)
    actual=publish.run_git
    pushes=[]
    def git(path,*args):
        if args[0]=='push':pushes.append(args)
        return actual(path,*[str(remote) if x=='https://github.com/owner/repo.git' else x for x in args])
    monkeypatch.setattr(publish,'run_git',git)
    proposal=publish.prepare(source,['hello.py'],'crewai/recovery')
    return proposal,git,pushes,remote


def perform(proposal):
    return publish.publish(proposal['job_id'],proposal['review_digest'])


def state(proposal):
    return publish.publication_status(proposal['job_id'])


def test_crash_after_remote_effect_reconciles_without_second_push(publication,monkeypatch):
    proposal,git,pushes,_=publication
    def crash(path,*args):
        result=git(path,*args)
        if args[0]=='push':raise SystemExit('simulated process death')
        return result
    monkeypatch.setattr(publish,'run_git',crash)
    with pytest.raises(SystemExit):perform(proposal)
    assert state(proposal)['state']=='in_flight'
    monkeypatch.setattr(publish,'run_git',git)
    assert perform(proposal)['commit']==proposal['manifest']['commit']
    assert len(pushes)==1 and state(proposal)['state']=='confirmed'


def test_absent_remote_requires_explicit_resolution_and_same_identity(publication,monkeypatch):
    proposal,git,pushes,_=publication
    def timeout(path,*args):
        if args[0]=='push':raise subprocess.TimeoutExpired('git',120)
        return git(path,*args)
    monkeypatch.setattr(publish,'run_git',timeout)
    with pytest.raises(subprocess.TimeoutExpired):perform(proposal)
    previous=state(proposal)
    assert previous['state']=='uncertain' and previous['attempts']==1
    monkeypatch.setattr(publish,'run_git',git)
    with pytest.raises(ValueError,match='incierto'):perform(proposal)
    with pytest.raises(ValueError,match='SHA'):
        publish.publication_control(proposal['job_id'],'confirm-applied',state(proposal)['version'])
    publish.publication_control(proposal['job_id'],'confirm-not-applied',state(proposal)['version'])
    assert perform(proposal)['commit']==proposal['manifest']['commit']
    current=state(proposal)
    assert current['intent']==previous['intent'] and current['attempts']==2
    assert len(pushes)==1
    assert any(e['actor'].endswith(__import__('getpass').getuser()) for e in current['events'])


def test_pause_before_claim_prevents_network_write(publication,monkeypatch):
    proposal,_,pushes,_=publication
    original=Journal.claim
    def pause(self,*a,**kw):
        self.pause(True,self.status()['version'],'operator')
        return original(self,*a,**kw)
    monkeypatch.setattr(Journal,'claim',pause)
    with pytest.raises(ValueError,match='pausada'):perform(proposal)
    assert pushes==[] and state(proposal)['state']=='prepared'
    assert state(proposal)['paused']==1


def test_pause_after_dispatch_preserved_on_confirmation(publication,monkeypatch):
    proposal,git,pushes,_=publication
    def pause(path,*args):
        if args[0]=='push':
            publish.publication_control(proposal['job_id'],'pause',state(proposal)['version'])
        return git(path,*args)
    monkeypatch.setattr(publish,'run_git',pause)
    perform(proposal)
    assert state(proposal)['paused']==1 and state(proposal)['state']=='confirmed'
    assert len(pushes)==1


def test_persistence_failure_before_dispatch_never_pushes(publication,monkeypatch):
    proposal,_,pushes,_=publication
    original=Journal._event
    def failure(self,db,row,kind,*args):
        original(self,db,row,kind,*args)
        if kind=='dispatched':raise OSError('disk failure')
    monkeypatch.setattr(Journal,'_event',failure)
    with pytest.raises(OSError):perform(proposal)
    assert pushes==[] and state(proposal)['state']=='prepared'
    assert state(proposal)['attempts']==0


def test_confirmation_transaction_failure_keeps_uncertain(publication,monkeypatch):
    proposal,_,pushes,_=publication
    original=Journal._event
    def failure(self,db,row,kind,*args):
        original(self,db,row,kind,*args)
        if kind=='confirm-applied':raise OSError('disk failure')
    monkeypatch.setattr(Journal,'_event',failure)
    with pytest.raises(OSError):perform(proposal)
    assert len(pushes)==1 and state(proposal)['state']=='uncertain'
    monkeypatch.setattr(Journal,'_event',original)
    perform(proposal)
    assert len(pushes)==1 and state(proposal)['state']=='confirmed'


def test_two_workers_only_one_push(publication,monkeypatch):
    proposal,git,pushes,_=publication
    entered=threading.Event();release=threading.Event()
    def wait(path,*args):
        if args[0]=='push':
            entered.set()
            assert release.wait(10)
        return git(path,*args)
    monkeypatch.setattr(publish,'run_git',wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner=pool.submit(perform,proposal)
        try:
            assert entered.wait(10)
            with pytest.raises(ValueError,match='Otro proceso'):perform(proposal)
            with pytest.raises(ValueError,match='activo'):
                publish.publication_control(proposal['job_id'],'defer',state(proposal)['version'])
        finally:release.set()
        owner.result(timeout=10)
    assert len(pushes)==1


def test_real_process_exit_after_push(publication):
    proposal,_,_,remote=publication
    script='''import os,sys
from pathlib import Path
import github_publish as p
p.JOBS=Path(sys.argv[1]); p.enabled=lambda:'owner/repo'
p.controller_gate.require_green=lambda:None
actual=p.run_git
p.github_app_auth.CONFIG=Path(sys.argv[1])/'unconfigured-app.json'
def git(path,*args):
    result=actual(path,*[sys.argv[4] if x=='https://github.com/owner/repo.git' else x for x in args])
    if args[0]=='push':os._exit(9)
    return result
p.run_git=git
p.publish(sys.argv[2],sys.argv[3])
'''
    result=subprocess.run([sys.executable,'-c',script,str(publish.JOBS),proposal['job_id'],
                           proposal['review_digest'],str(remote)],
                          cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=30)
    assert result.returncode==9,result.stderr
    assert state(proposal)['state']=='in_flight'
    assert perform(proposal)['commit']==proposal['manifest']['commit']
    assert state(proposal)['attempts']==1


def test_red_suite_blocks_push(publication,monkeypatch):
    proposal,_,pushes,_=publication
    def fail():raise ValueError('red suite')
    monkeypatch.setattr(publish.controller_gate,'require_green',fail)
    with pytest.raises(ValueError,match='red suite'):perform(proposal)
    assert pushes==[]


@pytest.fixture
def journal(tmp_path):
    item=Journal(tmp_path/'journal.sqlite')
    item.prepare({'repository':'owner/repo','branch':'crewai/test','commit':'a'*40})
    return item


def evidence(sha=None):
    return {'repository':'owner/repo','branch':'crewai/test','sha':sha,'retry_safety':'git_create_only_lease'}


def test_late_result_after_absence_resolution_is_confirmed(journal):
    journal.claim();journal.uncertain()
    journal.resolve('confirm-not-applied',evidence(),'operator')
    journal.resolve('confirm-applied',evidence('a'*40),'git-reconciliation')
    assert journal.status()['state']=='confirmed' and journal.status()['attempts']==1


def test_unrelated_sha_and_fake_absence_rejected(journal):
    journal.claim();journal.uncertain()
    for decision,record in [('confirm-applied',evidence('b'*40)),
                            ('confirm-not-applied',evidence('a'*40)),
                            ('confirm-not-applied',{**evidence(),'retry_safety':'human_says_so'}),
                            ('confirm-applied',{**evidence('a'*40),'repository':'other/repo'})]:
        with pytest.raises(ValueError):journal.resolve(decision,record,'operator')
    assert journal.status()['state']=='uncertain'


def test_stale_resolution_and_terminal_state(journal):
    journal.claim();journal.uncertain();old=journal.status()['version']
    journal.resolve('defer',evidence(),'operator',old)
    with pytest.raises(ValueError,match='obsoleta'):
        journal.resolve('confirm-applied',evidence('a'*40),'operator',old)
    journal.resolve('confirm-applied',evidence('a'*40),'operator')
    with pytest.raises(ValueError):journal.claim()
    with pytest.raises(ValueError):journal.pause(False,journal.status()['version'],'operator')


def test_bounded_attempts_and_changed_intent(journal):
    for _ in range(3):
        journal.claim();journal.uncertain()
        journal.resolve('confirm-not-applied',evidence(),'operator')
    with pytest.raises(ValueError,match='agotado'):journal.claim()
    with pytest.raises(ValueError,match='cambió'):
        journal.prepare({'repository':'owner/other','branch':'crewai/test','commit':'a'*40})


def test_status_cli_without_starting_publication(publication,capsys):
    proposal,_,pushes,_=publication
    assert controller_cli.main(['publication-status',proposal['job_id']])==0
    record=json.loads(capsys.readouterr().out)
    assert record['state']=='not_started' and pushes==[]
