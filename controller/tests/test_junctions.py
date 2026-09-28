"""Comprobar escapes por junctions de Windows sin requerir symlink privilege."""
import os
import subprocess
import pytest
import agent
import manifest


@pytest.mark.skipif(os.name!='nt',reason='Prueba específica de Windows')
def test_windows_junction_escape(tmp_path):
    root=tmp_path/'root';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    link=root/'redirect'
    result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True,timeout=10)
    if result.returncode:
        pytest.skip('El host no permite crear junctions')
    try:
        with pytest.raises(ValueError):agent.safe_path(root,'redirect','state.json')
        with pytest.raises(ValueError):manifest.relative_path('redirect/source.py',root)
    finally:
        # Eliminar sólo la entrada junction, nunca el destino ni de forma recursiva.
        os.rmdir(link)
