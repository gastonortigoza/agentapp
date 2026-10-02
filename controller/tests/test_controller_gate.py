import json
import pytest
import controller_gate as gate


def test_missing_and_failed_receipts_block(tmp_path,monkeypatch):
    monkeypatch.setattr(gate,'source_identity',lambda root:'current')
    receipt=tmp_path/'receipt.json'
    with pytest.raises(ValueError):gate.require_green(tmp_path,receipt)
    for data in [[],{'exit_code':1,'source_identity':'current'}, {'exit_code':0,'source_identity':'old'}]:
        receipt.write_text(json.dumps(data))
        with pytest.raises(ValueError):gate.require_green(tmp_path,receipt)
    receipt.write_text(json.dumps({'exit_code':0,'source_identity':'current'}))
    assert gate.require_green(tmp_path,receipt)=='current'


def test_source_change_invalidates_identity(tmp_path):
    (tmp_path/'pyproject.toml').write_text('project')
    (tmp_path/'uv.lock').write_text('lock')
    (tmp_path/'controller.py').write_text('before')
    before=gate.source_identity(tmp_path)
    (tmp_path/'controller.py').write_text('after')
    assert gate.source_identity(tmp_path)!=before


def test_failed_self_test_removes_old_green(tmp_path,monkeypatch):
    receipt=tmp_path/'receipt.json';receipt.write_text('{}')
    monkeypatch.setattr(gate,'source_identity',lambda root:'same')
    class Result:
        returncode=1
        stdout='intentional failure'
    monkeypatch.setattr(gate.subprocess,'run',lambda *a,**k:Result())
    with pytest.raises(ValueError):gate.self_test(tmp_path,receipt)
    assert not receipt.exists()


def test_timeout_preserves_partial_diagnostics_and_invalidates_old_green(tmp_path,monkeypatch,capsys):
    receipt=tmp_path/'receipt.json';receipt.write_text('{}')
    monkeypatch.setattr(gate,'source_identity',lambda root:'same')
    def timeout(*a,**kwargs):
        raise gate.subprocess.TimeoutExpired(a[0],kwargs['timeout'],output=b'completed partial tests',stderr=b'partial diagnostic')
    monkeypatch.setattr(gate.subprocess,'run',timeout)
    with pytest.raises(gate.subprocess.TimeoutExpired):gate.self_test(tmp_path,receipt)
    assert not receipt.exists()
    output=capsys.readouterr().out
    assert 'completed partial tests' in output and 'partial diagnostic' in output
