"""Transfer a sealed metadata-only campaign diagnostic to a private GitHub repo."""
import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from campaign_diagnostic import fingerprint


def load_bundle(directory):
    raw = (directory / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    digest = manifest.pop('diagnostic_sha256')
    if (manifest.get('schema') != 'wikidebia-campaign-diagnostic-1'
            or manifest.get('auto_publish') is not False
            or fingerprint(manifest) != digest or directory.name != digest):
        raise ValueError('invalid_diagnostic_manifest')
    files = {'manifest.json': raw}
    for name, spec in manifest['files'].items():
        if not re.fullmatch(r'(cases|scopes)-[0-9]+\.json', name):
            raise ValueError('invalid_diagnostic_filename')
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('invalid_diagnostic_file')
        data = path.read_bytes()
        if len(data) != spec['bytes'] or sha256(data).hexdigest() != spec['sha256']:
            raise ValueError('diagnostic_file_hash_mismatch')
        files[name] = data
    if {p.name for p in directory.iterdir()} != set(files):
        raise ValueError('unexpected_diagnostic_files')
    return digest, files


def token(repo):
    for name in ('GH_TOKEN', 'GITHUB_TOKEN'):
        if os.environ.get(name):
            return os.environ[name]
    if shutil.which('gh'):
        p = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'],
                           capture_output=True, text=True, timeout=20)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    env = {**os.environ, 'GIT_TERMINAL_PROMPT':'0', 'GCM_INTERACTIVE':'never'}
    p = subprocess.run(['git', '-c', 'credential.interactive=never', 'credential', 'fill'],
                       input=f'url=https://github.com/{repo}.git\n\n', env=env,
                       capture_output=True, text=True, timeout=20)
    if p.returncode == 0:
        for line in p.stdout.splitlines():
            if line.startswith('password=') and line[9:]:
                return line[9:]
    raise RuntimeError('no_existing_github_api_credential')


def make_api(repo, credential):
    def api(method, suffix='', body=None, missing=False):
        req = Request('https://api.github.com/repos/'+repo+suffix,
                      data=json.dumps(body).encode() if body is not None else None,
                      method=method, headers={'Authorization':'Bearer '+credential,
                          'Accept':'application/vnd.github+json', 'User-Agent':'wikidebia-diagnostic',
                          'X-GitHub-Api-Version':'2022-11-28', 'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=90) as response:
                return json.load(response)
        except HTTPError as e:
            if e.code == 404 and missing:
                return None
            raise RuntimeError(f'github_api_{method}_{e.code}') from None
    return api


def transfer(api, repo, digest, files, *, prefix='diagnostics'):
    if prefix not in {'diagnostics','source-probes'}:
        raise ValueError('invalid_relay_prefix')
    def check_private():
        meta = api('GET')
        if (meta.get('full_name', '').lower() != repo.lower() or meta.get('private') is not True
                or meta.get('visibility') != 'private' or meta.get('default_branch') != 'main'):
            raise ValueError('relay_is_not_expected_private_main_repo')

    def expected_blob(data):
        return sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

    root = prefix+'/'+digest
    endpoint = '/contents/'+root
    expected = {name:expected_blob(data) for name,data in files.items()}

    def check_remote(ref, missing=False):
        listing = api('GET', endpoint+'?ref='+quote(ref, safe=''), missing=missing)
        if listing is None:
            return False
        if not isinstance(listing, list) or any(r.get('type') != 'file' for r in listing):
            raise ValueError('remote_diagnostic_not_file_directory')
        if {r['name']:r['sha'] for r in listing} != expected:
            raise ValueError('remote_diagnostic_hash_mismatch')
        return True

    check_private()
    head = api('GET','/git/ref/heads/main')['object']['sha']
    if check_remote(head, missing=True):
        check_private()
        return {'status':'already_present_verified','commit':head,'path':root}
    tree = api('GET','/git/commits/'+head)['tree']['sha']
    entries = [{'path':root+'/'+name, 'mode':'100644', 'type':'blob', 'content':data.decode('utf-8')}
               for name,data in sorted(files.items())]
    check_private()
    updated = api('POST','/git/trees',{'base_tree':tree,'tree':entries})['sha']
    commit = api('POST','/git/commits',{'message':'Add sealed private campaign diagnostic' if prefix=='diagnostics' else 'Add sealed private source probe',
                                        'tree':updated,'parents':[head]})['sha']
    check_private()
    api('PATCH','/git/refs/heads/main',{'sha':commit,'force':False})
    check_private()
    check_remote(commit)
    return {'status':'uploaded_verified','commit':commit,'path':root}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--repo', required=True)
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', a.repo):
        p.error('invalid repo')
    digest, files = load_bundle(a.directory)
    result = transfer(make_api(a.repo, token(a.repo)), a.repo, digest, files)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
