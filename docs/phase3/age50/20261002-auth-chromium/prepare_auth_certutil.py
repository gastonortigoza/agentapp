"""Acquire NSS tooling from signed Ubuntu metadata; never install on Windows."""
import hashlib,json,lzma,pathlib,re,sys,urllib.request
HERE=pathlib.Path(__file__).resolve().parent;OUT=HERE/'auth-browser-tools';STATE=HERE/'auth-browser-tools-acquisition.json'
assert not OUT.exists() and not STATE.exists(),'Inspect immutable prior acquisition instead'
OUT.mkdir();row={'state':'acquiring','source':'Ubuntu noble main official HTTPS','files':{},'host_install':False,'signature_verified':False}
STATE.write_text(json.dumps(row,indent=2))
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('Redirect rejected')
opener=urllib.request.build_opener(NoRedirect())
def get(url,name,limit):
 assert url.startswith('https://archive.ubuntu.com/ubuntu/')
 with opener.open(url,timeout=20) as response:data=response.read(limit+1)
 assert len(data)<=limit
 (OUT/name).write_bytes(data);row['files'][name]={'url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
 STATE.write_text(json.dumps(row,indent=2));return data
try:
 release=get('https://archive.ubuntu.com/ubuntu/dists/noble/InRelease','InRelease',1024*1024)
 index=get('https://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.xz','Packages.xz',20*1024*1024)
 block=release.decode().split('SHA256:\n',1)[1].split('\nSHA512:',1)[0]
 matching=[line.split() for line in block.splitlines() if line.strip().endswith(' main/binary-amd64/Packages.xz')]
 assert len(matching)==1 and matching[0][:2]==[hashlib.sha256(index).hexdigest(),str(len(index))]
 data=lzma.decompress(index,memlimit=256*1024*1024);assert len(data)<=100*1024*1024
 candidates=[p for p in data.decode().split('\n\n') if p.startswith('Package: libnss3-tools\n')]
 assert len(candidates)==1
 fields=dict(line.split(': ',1) for line in candidates[0].splitlines() if ': ' in line and not line.startswith(' '))
 assert fields['Architecture']=='amd64' and re.fullmatch(r'pool/main/n/nss/[A-Za-z0-9_.+-]+\.deb',fields['Filename'])
 package=get('https://archive.ubuntu.com/ubuntu/'+fields['Filename'],'libnss3-tools.deb',16*1024*1024)
 assert hashlib.sha256(package).hexdigest()==fields['SHA256'] and len(package)==int(fields['Size'])
 row.update(state='acquired_pending_signature_verification',package_metadata=fields)
except Exception as exc:row.update(state='needs_attention',error=str(exc));raise
finally:STATE.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'state':row['state'],'version':fields['Version'],'package_sha256':fields['SHA256'],'host_install':False}))
