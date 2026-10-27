"""Publish a complete immutable generation, making it visible only after upload."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from bundle_format import MAX_PACK_BYTES, safe_path, sha256

REPOSITORY = 'Pharaoh2k/PSX-Cheats'


def checked_assets(folder):
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    if manifest.get('schemaVersion') != 1 or not re.fullmatch(r'bundles-[0-9a-f]{24}', manifest.get('releaseTag', '')):
        raise ValueError('Unsupported manifest')
    catalog = manifest['catalogue']
    assets = manifest['packs'] + [catalog]
    names = set()
    for asset in assets:
        name = safe_path(asset['file'])
        if '/' in name or name.casefold() in names or asset['sha256'] not in name or not re.fullmatch('[0-9a-f]{64}', asset['sha256']):
            raise ValueError('Invalid immutable asset name')
        names.add(name.casefold())
        path = folder / name
        if not 0 < asset['length'] <= MAX_PACK_BYTES or path.stat().st_size != asset['length'] or sha256(path.read_bytes()) != asset['sha256']:
            raise ValueError('Asset bytes differ from manifest: ' + name)
    if manifest['releaseTag'] != 'bundles-' + catalog['sha256'][:24]:
        raise ValueError('Release tag does not identify catalogue content')
    rows = json.loads((folder / catalog['file']).read_bytes())
    if rows.get('schemaVersion') != 1:
        raise ValueError('Unsupported catalogue')
    report = json.loads((folder / 'build-report.json').read_bytes())
    if report['releaseTag'] != manifest['releaseTag']:
        raise ValueError('Build report belongs to another generation')
    return manifest, [folder / asset['file'] for asset in assets] + [folder / 'build-report.json', folder / 'manifest.json']


def verify_repository(remote):
    if remote.get('full_name') != REPOSITORY:
        raise ValueError('Repository identity does not match')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'dist')
    parser.add_argument('--gh', default='gh')
    args = parser.parse_args()
    manifest, assets = checked_assets(args.output)
    tag = manifest['releaseTag']

    def gh(*arguments, allow_missing=False):
        result = subprocess.run([args.gh, *arguments], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        if result.returncode:
            if allow_missing and ('HTTP 404' in result.stderr or 'release not found' in result.stderr.lower()):
                return None
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    remote = json.loads(gh('api', 'repos/' + REPOSITORY, '--jq', '{full_name}'))
    verify_repository(remote)
    existing = gh('release', 'view', tag, '--repo', REPOSITORY, '--json', 'isDraft,assets', allow_missing=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        if existing:
            state = json.loads(existing)
            if not state['isDraft']:
                gh('release', 'download', tag, '--repo', REPOSITORY, '--pattern', 'manifest.json', '--dir', str(temporary))
                published = json.loads((temporary / 'manifest.json').read_bytes())
                if published['catalogue']['sha256'] != manifest['catalogue']['sha256']:
                    raise ValueError('Existing generation has different catalogue content')
                print('Unchanged catalogue; existing release retained: ' + tag, flush=True)
                return
        else:
            source = next(item for item in manifest['sources'] if item['id'] == 'shnext')
            if source['repo'] != REPOSITORY or not re.fullmatch('[0-9a-f]{40}', source['commit']):
                raise ValueError('Invalid source commit')
            notes = temporary / 'notes.txt'
            notes.write_text('Trainer bundles and cover snapshot. The manifest identifies every asset by SHA256. Missing covers remain eligible for the app\'s Sony fallback.\n', encoding='utf-8')
            gh('release', 'create', tag, '--repo', REPOSITORY, '--draft', '--target', source['commit'],
               '--title', 'Library bundles ' + tag.removeprefix('bundles-'), '--notes-file', str(notes))
            state = {'assets': []}
        existing_names = {asset['name'] for asset in state['assets']}
        for asset in assets:
            if asset.name in existing_names:
                destination = temporary / asset.name
                gh('release', 'download', tag, '--repo', REPOSITORY, '--pattern', asset.name, '--dir', str(temporary))
                if sha256(destination.read_bytes()) != sha256(asset.read_bytes()):
                    raise ValueError('Existing draft asset differs; no assets were overwritten: ' + asset.name)
            else:
                gh('release', 'upload', tag, str(asset), '--repo', REPOSITORY)
        state = json.loads(gh('release', 'view', tag, '--repo', REPOSITORY, '--json', 'isDraft,assets'))
        sizes = {asset['name']: asset['size'] for asset in state['assets']}
        if any(sizes.get(asset.name) != asset.stat().st_size for asset in assets):
            raise ValueError('Uploaded asset set is incomplete')
        gh('release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest')
    print('Published complete generation: ' + tag, flush=True)


if __name__ == '__main__':
    main()
