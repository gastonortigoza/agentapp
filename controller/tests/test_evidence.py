import pytest
from pydantic import ValidationError
from osint_flow import Findings,Claim,validate_claims
from mcp_local import read_document

def test_unknown_source_rejected():
    findings=Findings(claims=[Claim(claim='test',source_ids=['invented'])])
    with pytest.raises(ValueError):validate_claims(findings,[{'id':'E1'}])

def test_free_confidence_rejected():
    with pytest.raises(ValidationError):Claim(claim='test',source_ids=['E1'],confidence=.99)

def test_document_escape_rejected():
    with pytest.raises(ValueError):read_document('../../open-webui/data/webui.db')
