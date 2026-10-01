import json
import pytest
import agent


def evidence(tmp_path, state=None, events=()):
    root = tmp_path / 'repo'
    run = root / 'runs' / 'run-1'
    run.mkdir(parents=True)
    logs = tmp_path / 'logs'
    logs.mkdir()
    if state is not None:
        (run / 'state.json').write_text(json.dumps({'run_id': 'run-1', **state}), encoding='utf-8')
    (logs / 'run-1.jsonl').write_text(''.join(json.dumps({'run_id': 'run-1', **e})+'\n' for e in events), encoding='utf-8')
    return root, logs


def test_historical_pass_does_not_claim_active_or_remaining_budget(tmp_path):
    root, logs = evidence(tmp_path, {'status': 'pass', 'current_round': 1})
    row = agent.status(root, logs)[0]
    assert row['recorded_status'] == 'pass'
    assert row['activity'] == 'unknown' and row['remaining_budget'] is None


def test_partial_log_preserves_valid_events_and_reports_problem(tmp_path):
    root, logs = evidence(tmp_path, events=[{'kind': 'agent_start'}])
    with (logs/'run-1.jsonl').open('a') as handle:
        handle.write('{"unfinished":')
    result = agent.history('run-1', root, logs)
    assert len(result['events']) == 1 and result['warnings']


def test_no_sensitive_payloads_and_bounded_history(tmp_path):
    root, logs = evidence(tmp_path, events=[{'kind': 'llm', 'stdout': 'synthetic-secret', 'message': 'synthetic-secret', 'eval_count': n} for n in range(5)])
    result = agent.history('run-1', root, logs, limit=2)
    assert [e['eval_count'] for e in result['events']] == [3, 4]
    assert result['truncated'] and result['valid_events'] == 5
    assert 'synthetic-secret' not in json.dumps(result)


@pytest.mark.parametrize('run_id', ['../secret', 'a/b', 'C:\\secret', '.', '', 'a\n'])
def test_path_traversal_rejected(tmp_path, run_id):
    with pytest.raises(ValueError):
        agent.history(run_id, tmp_path, tmp_path)


def test_symlink_escape_rejected(tmp_path):
    root = tmp_path/'logs'
    root.mkdir()
    outside = tmp_path/'outside'
    outside.mkdir()
    try:
        (root/'escape').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('El host no permite crear symlinks')
    with pytest.raises(ValueError):
        agent.safe_path(root, 'escape', 'state.json')


def test_corrupt_state_and_foreign_event_do_not_appear_valid(tmp_path):
    root, logs = evidence(tmp_path, {'run_id': 'another', 'status': 'pass'}, [{'run_id': 'another', 'kind': 'llm'}])
    assert agent.status(root, logs)[0]['recorded_status'] == 'unknown'
    assert agent.history('run-1', root, logs)['events'] == []


def test_missing_run_and_invalid_limit(tmp_path):
    with pytest.raises(ValueError):
        agent.history('missing', tmp_path, tmp_path)
    with pytest.raises(ValueError):
        agent.history('run-1', tmp_path, tmp_path, limit=0)


def test_empty_installation(tmp_path):
    assert agent.status(tmp_path/'repo', tmp_path/'logs') == []


def test_malformed_field_types_do_not_crash(tmp_path):
    root, logs = evidence(tmp_path, {'status': []}, [{'kind': {}, 'agent': [], 'status': {}}])
    assert agent.status(root, logs)[0]['recorded_status'] == 'unknown'
    assert agent.history('run-1', root, logs)['events'] == [{'kind': 'other'}]


def test_cli_summary_combines_sources_without_double_counting_and_preserves_warnings(tmp_path, monkeypatch, capsys):
    import controller
    root, logs = evidence(tmp_path, {'status': 'pass'})
    (logs/'broken.jsonl').write_text('{', encoding='utf-8')
    (root/'.state').mkdir()
    (root/'.state/controller.sqlite').touch()
    monkeypatch.setattr(agent, 'ROOT', root)
    monkeypatch.setattr(agent, 'LOGS', logs)
    monkeypatch.setattr(controller, 'read_runs', lambda *args: [
        {'run_id': 'run-1', 'recorded_status': 'delivered', 'mode': 'local'},
        {'run_id': 'simulation', 'recorded_status': 'delivered', 'mode': 'stub'}])
    assert agent.main(['status', '--summary', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['total'] == 3
    assert result['by_status'] == {'delivered': 2, 'unknown': 1}
    assert result['by_mode']['local']['total'] == result['by_mode']['stub']['total'] == 1
    assert result['warnings'][0]['run_id'] == 'broken'
    assert agent.main(['status', '--json']) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3
    assert agent.main(['status', '--summary']) == 0
    assert 'Aviso (broken)' in capsys.readouterr().out


def test_cli_summary_empty_installation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agent, 'ROOT', tmp_path)
    monkeypatch.setattr(agent, 'LOGS', tmp_path/'logs')
    assert agent.main(['status', '--summary', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['total'] == 0 and result['by_status'] == {} and result['warnings'] == []
    assert not (tmp_path/'.state').exists()
