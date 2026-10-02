"""Preparation/qualification tests do not execute generated geography source."""
import copy
import shutil
import subprocess
import pytest
import controller_gate
import manifest
import phase3_geography_scaffold as scaffold
import phase3_review as review
import phase3_prepare as preparation


def test_frozen_cases_sql_parent_constraints_and_minimal_locked_dependencies():
    files=scaffold.seed_files();assert len(scaffold.CASES)==14
    assert set(files)==set(scaffold.FILES)|set(scaffold.PACKAGES)
    assert not any(n.endswith(('auth.ts','AuthPages.tsx','geography.ts')) for n in files)
    sql=files['schema.sql'].decode()
    _,docs,_=preparation.load_bundle();tables=docs['contract.json']['data']['tables']
    for name in ('countries','provinces','zones'):
        assert set(tables[name]['columns'])<=set(sql.replace('(', ' ').replace(')', ' ').replace(',', ' ').split())
    assert "CHECK (code='AR')" in sql and 'FOREIGN KEY(province_id,country_id)' in sql
    assert 'REFERENCES agentapp.provinces(id,country_id)' in sql
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA agentapp TO age50_geo_app' in sql
    assert 'GRANT SELECT,INSERT,DELETE ON ALL TABLES IN SCHEMA agentapp TO age50_geo_fixture' in sql
    assert scaffold.POLICY['execution_authorized'] is False and scaffold.POLICY['host_ports'] is False
    assert scaffold.POLICY['postgres_image'].endswith('5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722')
    assert scaffold.STAGES['api']['argv'][4:6]==['--test-reporter=tap','--test-concurrency=1']


def test_actual_node_checks_frozen_harness_syntax_without_importing_candidate():
    node=shutil.which('node')
    if not node:pytest.skip('Node syntax checker unavailable')
    p=subprocess.run([node,'--check',str(scaffold.SEED/'geography-acceptance.test.mjs')],capture_output=True,timeout=10)
    assert p.returncode==0,p.stderr.decode('utf-8','replace')


@pytest.mark.parametrize('change',['test_bytes','schema_bytes','extra','link'])
def test_changed_tests_schema_extras_or_links_cannot_qualify(tmp_path,monkeypatch,change):
    target=tmp_path/'inputs';shutil.copytree(scaffold.SEED,target)
    monkeypatch.setattr(scaffold,'SEED',target)
    if change=='test_bytes':(target/'geography-acceptance.test.mjs').write_text('test.skip("fake")',encoding='utf-8')
    elif change=='schema_bytes':(target/'schema.sql').write_text('DROP DATABASE postgres',encoding='utf-8')
    elif change=='extra':(target/'unsafe.mjs').write_text('unsafe',encoding='utf-8')
    else:
        path=target/'schema.sql';path.unlink()
        try:path.symlink_to(scaffold.__file__)
        except OSError:pytest.skip('OS symlink creation permission unavailable')
    with pytest.raises(ValueError):scaffold.seed_files()


@pytest.mark.parametrize('passwords',[('a'*64,'a'*64),('a'*64,"';DROP DATABASE postgres;--"),('a'*63,'b'*64)])
def test_bootstrap_rejects_credentials_that_share_roles_or_escape_sql(passwords):
    with pytest.raises(ValueError):scaffold.database_sql(scaffold.seed_files()['schema.sql'],*passwords)


def test_distinct_ephemeral_roles_and_atomic_bootstrap_do_not_grant_app_write():
    sql=scaffold.database_sql(scaffold.seed_files()['schema.sql'],'a'*64,'b'*64).decode()
    assert sql.startswith('BEGIN;') and sql.endswith('COMMIT;\n')
    assert 'REVOKE CONNECT,TEMP ON DATABASE age50_geography_test FROM PUBLIC' in sql
    assert 'NOCREATEDB NOCREATEROLE NOREPLICATION' in sql
    assert 'GRANT SELECT,INSERT,DELETE ON ALL TABLES IN SCHEMA agentapp TO age50_geo_app' not in sql


def tap():
    return ('TAP version 13\n'+''.join(f'ok {i} - {name}\n' for i,name in enumerate(scaffold.CASES,1))+
      '# tests 14\n# pass 14\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n').encode()


@pytest.mark.parametrize('change',['skipped','missing','duplicate','failure','exit_code'])
def test_test_reports_cannot_pass_with_skips_missing_repeated_cases_or_failed_process(change):
    stdout=tap();code=0
    if change=='skipped':stdout=stdout.replace(b'# skipped 0',b'# skipped 1')
    elif change=='missing':stdout=stdout.replace(('ok 1 - '+scaffold.CASES[0]+'\n').encode(),b'')
    elif change=='duplicate':stdout+=('ok 15 - '+scaffold.CASES[0]+'\n').encode()
    elif change=='failure':stdout=stdout.replace(b'# fail 0',b'# fail 1')
    else:code=1
    with pytest.raises(ValueError):scaffold.check_test_totals(code,stdout)


def test_complete_synthetic_test_report_is_scoped_not_official_or_full_acceptance():
    value=scaffold.check_test_totals(0,tap())
    assert value['tests']==14 and not value['official_catalogue_loaded'] and not value['full_product_acceptance']


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    j=review.Journal(tmp_path/'journal.sqlite')
    history={'queue_id':'queue','limits':{'calls':48},'shared_used':{'calls':18,'executions':0},
      'source_review_ids':['queue-geo-failed'],'formal_audited_through':3,'closed_attempts':4}
    monkeypatch.setattr(scaffold,'frozen_history',lambda *a:copy.deepcopy(history))
    monkeypatch.setattr(controller_gate,'require_green',lambda:'current-controller')
    monkeypatch.setattr(scaffold.dependencies,'cached_tarballs',lambda *a:{'a'*64+'.tgz':b'synthetic cached resource'})
    row=scaffold.prepare(j,'queue',tmp_path/'cache')
    return j,history,row


def test_fixture_preparation_is_unique_and_never_creates_agent_runs_or_execution(prepared,tmp_path):
    j,history,row=prepared
    assert scaffold.prepare(j,'queue',tmp_path/'cache')==row
    assert scaffold.get(j,'queue')==row
    assert row['binding']['history']['shared_used']==history['shared_used']
    with j.transaction() as db:
        assert db.execute('SELECT count(*) FROM plan_runs').fetchone()[0]==0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='phase3_geography_executions'").fetchone()
    assert row['state']=='prepared_fixture_only' and not row['execution_authorized'] and not row['source_review_accepted']


def test_scaffold_preparation_keeps_historical_qualification_after_controller_change(prepared,tmp_path,monkeypatch):
    j,_,row=prepared
    monkeypatch.setattr(controller_gate,'require_green',lambda:'future-qualified-controller')
    assert scaffold.prepare(j,'queue',tmp_path/'cache')==row
    assert scaffold.get(j,'queue')['binding']['suite_identity']=='current-controller'


def test_rejected_source_is_not_materialized_or_promoted_to_product(prepared,tmp_path,monkeypatch):
    j,_,_=prepared
    monkeypatch.setattr(j,'get',lambda *a:{'binding':{'section':'geography_revalidation'},'state':'blocked_budget'})
    # The real handoff refuses the terminal state before asking for any source bytes.
    with pytest.raises(ValueError,match='review_not_accepted'):scaffold.source_preflight(j,'queue','queue-geo-failed',tmp_path/'nonexistent','cache')
    assert not (tmp_path/'nonexistent').exists()


def test_history_drift_invalidates_prepared_receipt(prepared):
    j,history,_=prepared;history['shared_used']['calls']=19
    with pytest.raises(ValueError):scaffold.get(j,'queue')


def test_history_captures_each_original_under_its_own_child_id(monkeypatch):
    parent={'id':'queue'}
    first={'child_id':'first'};prior={'child_id':'correction','audit':{'packet':{'through_round':3}}}
    latest={'child_id':'revalidation','state':'awaiting_discrepancy','pending_audit':None}
    monkeypatch.setattr(scaffold.queue,'load',lambda *a:parent)
    monkeypatch.setattr(scaffold.base,'load',lambda *a:first)
    monkeypatch.setattr(scaffold.correction,'load',lambda *a:prior)
    monkeypatch.setattr(scaffold.revalidation,'load',lambda *a:latest)
    monkeypatch.setattr(scaffold.revalidation,'check',lambda *a:None)
    monkeypatch.setattr(scaffold.revalidation,'packet',lambda *a:{'from_round':4,'through_round':4})
    monkeypatch.setattr(scaffold.base,'child_originals',lambda *a:{'auth-original':{'run_sha256':'auth-sha'}})
    monkeypatch.setattr(scaffold.correction,'originals',lambda db,rid:{'run_sha256':rid+'-sha','ops_sha256':rid+'-ops','events_sha256':rid+'-events'})
    monkeypatch.setattr(scaffold.base,'aggregate',lambda *a:{'calls':18,'executions':0})
    h=scaffold.frozen_history(None,'queue')
    assert set(h['originals'])=={'auth-original','first','correction','revalidation'}
    for rid in ('first','correction','revalidation'):
        assert h['originals'][rid]['run_sha256']==rid+'-sha'
        assert h['originals'][rid]['events_sha256']==rid+'-events'
