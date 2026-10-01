"""Servidor MCP stdio de sólo lectura, limitado al corpus y repositorios locales."""
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from executor import git_read
from git_control import safe_name
from rag import KNOWLEDGE,search

mcp=FastMCP('local-ai-readonly')

@mcp.tool()
def list_documents()->list[str]:
    """Lista los documentos del corpus autorizado."""
    return sorted(p.name for p in KNOWLEDGE.glob('*.md') if not p.is_symlink())

@mcp.tool()
def read_document(name:str)->str:
    """Lee un Markdown del corpus; no acepta rutas externas ni secretos."""
    if Path(name).name!=name or not safe_name(name) or not name.endswith('.md'): raise ValueError('Nombre no permitido')
    p=(KNOWLEDGE/name).resolve(strict=True)
    if not p.is_relative_to(KNOWLEDGE.resolve()) or p.stat().st_size>100000: raise ValueError('Ruta o tamaño no permitido')
    return p.read_text(encoding='utf-8')

@mcp.tool()
def git_status(path:str,action:str='status')->dict:
    """Git de lectura: status, diff, log o branch, dentro de E:/IA/workspace."""
    return git_read(path,action)

@mcp.tool()
def search_documents(query:str)->list[dict]:
    """Recupera hasta cinco fragmentos con sus fuentes."""
    return search(query)

if __name__=='__main__': mcp.run(transport='stdio')
