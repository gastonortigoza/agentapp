"""Descarga acotada de las fuentes públicas elegidas para ingeniería social."""
import hashlib
import json
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup

TARGET=Path('E:/IA/knowledge/social-sources.json')
ALLOWED={'www.incibe.es','www.nist.gov','pages.nist.gov','www.ftc.gov'}

def refresh():
    rows=json.loads(TARGET.read_text(encoding='utf-8')); started=time.monotonic()
    for row in rows:
        url=row['source_url']
        if urlparse(url).scheme!='https' or urlparse(url).hostname not in ALLOWED: raise ValueError('Fuente fuera de la lista')
        try:
            with httpx.Client(timeout=30,follow_redirects=False,trust_env=False) as client:
                with client.stream('GET',url) as response:
                    response.raise_for_status(); data=bytearray()
                    for block in response.iter_bytes():
                        data.extend(block)
                        if len(data)>3_000_000: raise ValueError('Fuente demasiado grande')
            soup=BeautifulSoup(bytes(data),'html.parser')
            for tag in soup(['script','style','nav','footer','header']): tag.decompose()
            terms=['ingeniería social','smishing','vishing','otp','phishing','known','telephone','verify','urgencia','persuas']
            paragraphs=[' '.join(p.get_text(' ',strip=True).split()) for p in soup.find_all(['p','li'])]
            selected=[]
            for p in paragraphs:
                if any(t in p.lower() for t in terms) and 40<len(p)<2500:
                    selected.append(p)
                    if sum(map(len,selected))>=2500: break
            if not selected: raise ValueError('No se obtuvo texto relevante')
            row.update(source_excerpt='\n'.join(selected)[:3500],content_sha256=hashlib.sha256(data).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat(),fetch_status='ok')
        except Exception as exc:
            row.update(fetch_status='error',fetch_error=type(exc).__name__)
    TARGET.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    result={'duration_seconds':time.monotonic()-started,'sources':[{k:r.get(k) for k in ['id','fetch_status','fetch_error']} for r in rows]}
    Path('E:/IA/logs/source-fetch.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))

if __name__=='__main__': refresh()
