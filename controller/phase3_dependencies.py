"""Audited registry acquisition; never runs application code or lifecycle hooks."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import tarfile
import threading
import urllib.request

import manifest
from phase3_sandbox import POLICY,packages

NPM_URL='https://registry.npmjs.org/npm/-/npm-10.9.3.tgz'
NPM_INTEGRITY='sha512-6Eh1u5Q+kIVXeA8e7l2c/HpnFFcwrkt37xDMujD5be1gloWa9p6j3Fsv3mByXXmqJHy+2cElRMML8opNT7xIJQ=='
NPM_LAUNCHER=b'#!/bin/sh\nif [ -x /usr/local/bin/node ]; then exec /usr/local/bin/node /cache/npm/bin/npm-cli.js "$@"; fi\nexec /usr/bin/node /cache/npm/bin/npm-cli.js "$@"\n'


class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('Registry redirects denied')


def digest(data):return 'sha512-'+base64.b64encode(hashlib.sha512(data).digest()).decode()


def references(lock):
    refs={}
    for entry in lock['packages'].values():
        if not entry.get('integrity'):continue
        compatible=True
        for field,value in (('os','linux'),('cpu','x64'),('libc','glibc')):
            choices=entry.get(field,[])
            if not isinstance(choices,list) or len(choices)>32 or any(not isinstance(c,str) for c in choices):raise ValueError('Unsupported dependency platform metadata')
            positives=[c for c in choices if not c.startswith('!')]
            if ('!'+value in choices) or (positives and value not in positives and 'any' not in positives):compatible=False
        if compatible:refs[entry['integrity']]=entry['resolved']
        elif entry.get('optional') is not True:raise ValueError('Required dependency does not support Linux x64 glibc')
    refs[NPM_INTEGRITY]=NPM_URL
    return refs


def registry(url,integrity):
    # URL allowlist was already checked in the concrete lock; enforce again here.
    import re
    if not re.fullmatch(r'https://registry\.npmjs\.org/[A-Za-z0-9_@./%+-]+\.tgz',url):raise ValueError('Registry denied')
    opener=urllib.request.build_opener(RegistryRedirect())
    with opener.open(urllib.request.Request(url,headers={'User-Agent':'agentapp-local-acquisition/1'}),timeout=20) as response:
        data=response.read(16*1024*1024+1)
    if len(data)>16*1024*1024 or digest(data)!=integrity:raise ValueError('Dependency size/integrity failure')
    return data


def acquisition(files,destination):
    """Store immutable registry tarballs, with resumable content-addressed reads."""
    lock=packages(files);root=Path(destination);root.mkdir(parents=True,exist_ok=True)
    refs=references(lock)
    if len(refs)>POLICY['max_packages']:raise ValueError('Dependency acquisition count limit')
    bytes_acquired=0;budget_lock=threading.Lock()
    def get(item):
        nonlocal bytes_acquired
        integrity,url=item;name=hashlib.sha256(integrity.encode()).hexdigest()+'.tgz';path=root/name
        if path.exists() and (path.is_symlink() or path.is_junction() or path.stat().st_size>16*1024*1024):raise ValueError('Acquisition cache path/size denied')
        data=path.read_bytes() if path.exists() else registry(url,integrity)
        if digest(data)!=integrity:raise ValueError('Stored dependency drift')
        with budget_lock:
            bytes_acquired+=len(data)
            if bytes_acquired>POLICY['cache_bytes']:raise ValueError('Acquisition byte limit')
            if not path.exists():
                with path.open('xb') as output:output.write(data)
        return {'url':url,'integrity':integrity,'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as pool:records=list(pool.map(get,refs.items()))
    if sum(r['bytes'] for r in records)>POLICY['cache_bytes']:raise ValueError('Acquisition byte limit')
    result={'schema':'agentapp.phase3-acquisition/1','lock_sha256':hashlib.sha256(files['package-lock.json']).hexdigest(),
            'network':'HTTPS registry.npmjs.org only, redirects denied','scripts_executed':False,'records':records}
    receipt=root/'acquisition.json';text=manifest.canonical(result)+'\n'
    if receipt.exists():
        if receipt.read_text(encoding='utf-8')!=text:raise ValueError('Acquisition receipt drift')
    else:receipt.write_text(text,encoding='utf-8',newline='\n')
    return result


def cache_archive(files,root):
    """Construct npm content-addressed cache directly from verified tarballs.

    pacote reads by the lock's integrity; no registry metadata, executable hooks,
    host package-manager invocation or unbounded dependency installation.
    """
    lock=packages(files);root=Path(root)
    receipt_path=root/'acquisition.json'
    if receipt_path.stat().st_size>512000:raise ValueError('Acquisition receipt too large')
    receipt=manifest.parse(receipt_path.read_text(encoding='utf-8'))
    if receipt['lock_sha256']!=hashlib.sha256(files['package-lock.json']).hexdigest():raise ValueError('Acquisition lock drift')
    expected=references(lock)
    records=receipt['records']
    if len(records)!=len(expected) or {r['integrity']:r['url'] for r in records}!=expected:raise ValueError('Acquisition registry drift')
    output=io.BytesIO();size=0
    with tarfile.open(fileobj=output,mode='w') as tar:
        def add(path,data,mode=0o444):
            nonlocal size
            size+=len(data)
            if size>POLICY['cache_bytes']:raise ValueError('Offline cache byte limit')
            info=tarfile.TarInfo('cache/'+path);info.size=len(data);info.mode=mode;info.uid=info.gid=1000;info.mtime=0
            tar.addfile(info,io.BytesIO(data))
        add('bin/npm',NPM_LAUNCHER,0o555)
        for record in records:
            name=record['path']
            if not __import__('re').fullmatch(r'[0-9a-f]{64}\.tgz',name):raise ValueError('Cache path denied')
            path=root/name
            if path.is_symlink() or path.is_junction() or path.stat().st_size>16*1024*1024:raise ValueError('Cache link/size denied')
            data=path.read_bytes()
            if digest(data)!=record['integrity'] or hashlib.sha256(data).hexdigest()!=record['sha256'] or len(data)!=record['bytes']:raise ValueError('Cache bytes drift')
            hexhash=hashlib.sha512(data).hexdigest()
            add('_cacache/content-v2/sha512/'+hexhash[:2]+'/'+hexhash[2:4]+'/'+hexhash[4:],data)
            if record['integrity']==NPM_INTEGRITY:
                with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as source:
                    for member in source.getmembers():
                        parts=PurePosixPath(member.name).parts
                        if not parts or parts[0]!='package' or any(p in ('.','..') for p in parts):raise ValueError('npm tool archive path denied')
                        if member.isdir():continue
                        if not member.isfile() or member.size>16*1024*1024:raise ValueError('npm tool archive type denied')
                        add('npm/'+ '/'.join(parts[1:]),source.extractfile(member).read(),0o555 if member.mode&0o111 else 0o444)
    return output.getvalue(),receipt
