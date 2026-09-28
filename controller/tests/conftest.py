"""Las pruebas locales no consumen credenciales reales de la App instalada."""
import pytest
import github_app_auth


@pytest.fixture(autouse=True)
def isolated_app_credentials(tmp_path,monkeypatch):
    monkeypatch.setattr(github_app_auth,'CONFIG',tmp_path/'unconfigured-app.json')
    monkeypatch.setattr(github_app_auth,'SECRET_DIR',tmp_path/'synthetic-credentials')
