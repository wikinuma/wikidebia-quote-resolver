#!/usr/bin/env python3
"""Private GitHub relay for the existing MIA campaign; no resolver changes."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

PIN = '7b8d3723c76af0cd937211392282c9cd909e8127'
REPO = 'wikidebates/wikidebia-mia-exchange'
HOME = Path.home() / '.local/share/wikidebia-mia'
HEX = re.compile(r'[0-9a-f]{64}\Z')
WAVE = re.compile(r'review-wave-([0-9a-f]{16})\.zip\Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def run(cmd, **kwargs):
    return subprocess.run(list(map(str, cmd)), check=True, text=True, **kwargs)


class Relay:
    def __init__(self, repo, home):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('Nom de depot invalide')
        self.repo = repo
        self.home = home.resolve()
        self.state = self.home / 'state'
        self.resolver = self.home / 'resolver'
        self.python = self.home / 'venv/bin/python'
        self.pending = self.home / 'relay-pending.json'
        self.env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'GH_HOST': 'github.com'}
        if not self.home.is_dir() or self.home.stat().st_mode & 0o077:
            raise ValueError('Dossier de campagne absent ou non prive')
        if run(['git', '-C', self.resolver, 'rev-parse', 'HEAD'], capture_output=True).stdout.strip() != PIN:
            raise ValueError('Commit du resolveur incorrect')
        run(['git', '-C', self.resolver, 'diff', '--quiet', 'HEAD', '--'])
        # A previous direct invocation without -B can leave ignored bytecode.
        # Remove only generated, ignored regular .pyc files, never source/data.
        ignored = run(['git', '-C', self.resolver, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'], capture_output=True).stdout
        for name in ignored.split('\0'):
            path = self.resolver / name
            if name and path.suffix == '.pyc' and path.parent.name == '__pycache__' and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(self.resolver):
                path.unlink()
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        dirty = run(['git', '-C', self.resolver, 'status', '--porcelain', '--untracked-files=all', '--ignored'], capture_output=True).stdout
        if dirty.strip():
            raise ValueError('Checkout du resolveur non immuable; aucun fichier source ou donnees ne sera ecrase')
        self.check_private()

    def api(self, path, method='GET', data=None):
        cmd = ['gh', 'api', '--hostname', 'github.com', '--method', method, 'repos/' + self.repo + path]
        if data is not None:
            cmd += ['--input', '-']
        result = run(cmd, input=encode(data) if data is not None else None, capture_output=True, env=self.env)
        return json.loads(result.stdout)

    def check_private(self):
        info = self.api('')
        if info.get('full_name', '').casefold() != self.repo.casefold() or info.get('private') is not True or info.get('visibility') != 'private' or info.get('archived') or info.get('fork'):
            raise ValueError('Refus: depot non strictement prive, archive ou fork')
        if info.get('default_branch') != 'main':
            raise ValueError('La branche main est requise pour ce relais dedie')

    def snapshot(self):
        self.check_private()
        head = self.api('/git/ref/heads/main')['object']['sha']
        tree = self.api('/git/commits/' + head)['tree']['sha']
        items = self.api('/git/trees/' + tree + '?recursive=1')
        if items.get('truncated'):
            raise ValueError('Arbre Git tronque')
        return head, tree, {x['path']: x for x in items['tree'] if x['type'] == 'blob'}

    def read_blob(self, entry):
        row = self.api('/git/blobs/' + entry['sha'])
        if row.get('encoding') != 'base64' or row.get('size', 0) > 2_000_000:
            raise ValueError('Blob invalide ou trop grand')
        body = base64.b64decode(row['content'])
        if hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest() != entry['sha']:
            raise ValueError('Empreinte Git du blob incorrecte')
        return body.decode('utf-8')

    def publish(self, files):
        head, tree, entries = self.snapshot()
        changes = []
        for path, content in files.items():
            if path in entries:
                if self.read_blob(entries[path]) != content:
                    raise ValueError('Fichier de relais existant different: ' + path)
                continue
            changes.append({'path': path, 'mode': '100644', 'type': 'blob', 'content': content})
        if not changes:
            return head
        new_tree = self.api('/git/trees', 'POST', {'base_tree': tree, 'tree': changes})['sha']
        commit = self.api('/git/commits', 'POST', {'message': 'MIA: private review wave', 'tree': new_tree, 'parents': [head]})['sha']
        self.check_private()
        if self.api('/git/ref/heads/main')['object']['sha'] != head:
            raise ValueError('Modification Git concurrente, relancer sans ecraser')
        updated = self.api('/git/refs/heads/main', 'PATCH', {'sha': commit, 'force': False})
        if updated['object']['sha'] != commit:
            raise ValueError('Commit de relais non confirme')
        return commit

    def prepare(self, *args):
        cmd = [self.python, '-B', self.resolver / 'prepare_citations.py', '--state', self.state, '--audit-budget', '5000', *args]
        result = subprocess.run(list(map(str, cmd)), cwd=self.resolver, env=self.env, text=True, stdout=subprocess.PIPE)
        print(result.stdout, end='', flush=True)
        result.check_returncode()
        return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]

    def make_wave(self):
        events = self.prepare('review-many', '--packets', '400', '--limit', '20')
        archives = [Path(x['review_archive']) for x in events if 'review_archive' in x]
        if not archives:
            print('Aucune vague a transmettre. Consulter le bilan du resolveur.')
            return
        archive = archives[-1].resolve()
        match = WAVE.fullmatch(archive.name)
        if not match or archive.parent != (self.state / 'outgoing').resolve():
            raise ValueError('Archive de revue inattendue')
        wave = match[1]
        prefix = 'waves/' + wave + '/'
        with zipfile.ZipFile(archive) as z:
            index = json.loads(z.read('index.json'))
        if index.get('auto_publish') is not False or not HEX.fullmatch(index['inventory_sha256']) or digest([p['packet_sha256'] for p in index['packets']])[:16] != wave:
            raise ValueError('Identite de vague incoherente')
        files = {}
        for i, entry in enumerate(index['packets'], 1):
            packet = entry['packet_sha256']
            if not HEX.fullmatch(packet) or not HEX.fullmatch(entry['view_sha256']):
                raise ValueError('Identifiant de paquet invalide')
            raw = run([self.python, '-B', self.resolver / 'review_bundle.py', archive, '--packet', i], capture_output=True, cwd=self.resolver, env=self.env).stdout
            view = json.loads(raw)
            if view['packet_sha256'] != packet or digest(view) != entry['view_sha256']:
                raise ValueError('Vue de revue alteree')
            files[prefix + 'packets/' + packet + '.json'] = pretty(view)
        if len(files) != len(index['packets']):
            raise ValueError('Paquets dupliques')
        manifest = {'schema': 'mia-private-relay-1', 'wave': wave, 'index': index, 'auto_publish': False}
        files[prefix + 'manifest.json'] = pretty(manifest)
        commit = self.publish(files)
        marker = {'wave': wave, 'manifest_sha256': digest(manifest), 'packets': [p['packet_sha256'] for p in index['packets']]}
        self.atomic(self.pending, pretty(marker))
        print('VAGUE PRIVEE DISPONIBLE:', self.repo, prefix, 'commit', commit, flush=True)

    @staticmethod
    def atomic(path, value):
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        try:
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def sync(self, launcher):
        if not self.pending.exists():
            self.make_wave()
            return
        pending = json.loads(self.pending.read_text())
        wave = pending['wave']
        if not re.fullmatch(r'[0-9a-f]{16}', wave):
            raise ValueError('Marqueur de reprise invalide')
        prefix = 'waves/' + wave + '/'
        _, _, entries = self.snapshot()
        manifest = json.loads(self.read_blob(entries[prefix + 'manifest.json']))
        if digest(manifest) != pending['manifest_sha256'] or [x['packet_sha256'] for x in manifest['index']['packets']] != pending['packets']:
            raise ValueError('Manifeste distant different du paquet local')
        path = prefix + 'decisions.json'
        if path in entries:
            replies = json.loads(self.read_blob(entries[path]))
        else:
            parts = [prefix + 'decisions/' + p + '.json' for p in pending['packets']]
            if not all(p in entries for p in parts):
                print('En attente des decisions ChatGPT pour', self.repo, prefix)
                return
            replies = [json.loads(self.read_blob(entries[p])) for p in parts]
        if not isinstance(replies, list) or sorted(x['packet_sha256'] for x in replies) != sorted(pending['packets']):
            raise ValueError('Decisions incompletes ou etrangeres')
        reply = self.home / ('relay-decisions-' + wave + '.json')
        self.atomic(reply, pretty(replies))
        self.prepare('apply-many', reply)
        # Export/test existing private registry locally; no receipts go to GitHub.
        run([sys.executable, '-B', launcher.resolve(), '--home', self.home, 'export'], env=self.env)
        self.pending.unlink()
        self.make_wave()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', default=REPO)
    p.add_argument('--home', type=Path, default=HOME)
    p.add_argument('--launcher', type=Path, default=Path('campagne_mia.py'))
    a = p.parse_args()
    os.umask(0o077)
    try:
        relay = Relay(a.repo, a.home)
        with (relay.home / 'relay.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not a.launcher.is_file():
                raise ValueError('Lanceur campagne_mia.py absent: indiquer --launcher CHEMIN')
            relay.sync(a.launcher)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print('ARRET:', exc, '(etat local conserve)', file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
