"""Build verified release assets without changing source files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import zipfile
from bundle_format import LOADABLE, MAX_PACK_BYTES, MAX_TRAINER_BYTES, build, safe_path

HEN_REPOSITORY = 'TeeKay87/HEN-Cheats-Collection'
HEN_FOLDER = 'cheats'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OSError('Unexpected HTTP redirect')


def download(url, destination, maximum):
    headers = {'User-Agent': 'PSX-Cheats-bundle-publisher', 'Accept': 'application/vnd.github+json'}
    if urllib.parse.urlparse(url).netloc == 'api.github.com' and os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    opener = urllib.request.build_opener(NoRedirect())
    temporary = destination.with_suffix(destination.suffix + '.partial')
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=90) as response, temporary.open('wb') as output:
            size = 0
            while chunk := response.read(81920):
                size += len(chunk)
                if size > maximum:
                    raise ValueError('Remote input exceeds its bound')
                output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def obtain_hen(cache, ref):
    cache.mkdir(parents=True, exist_ok=True)
    download('https://api.github.com/repos/' + HEN_REPOSITORY + '/commits/' + urllib.parse.quote(ref, safe=''), cache / 'commit.json', 2 * 1024 * 1024)
    commit = json.loads((cache / 'commit.json').read_bytes())
    commit_id = commit['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit_id):
        raise ValueError('Invalid remote commit')
    pinned = cache / commit_id
    pinned.mkdir(exist_ok=True)
    (pinned / 'commit.json').write_bytes((cache / 'commit.json').read_bytes())
    if not (pinned / 'tree.json').exists():
        download('https://api.github.com/repos/' + HEN_REPOSITORY + '/git/trees/' + commit['commit']['tree']['sha'] + '?recursive=1', pinned / 'tree.json', 32 * 1024 * 1024)
    if not (pinned / 'archive.zip').exists():
        download('https://codeload.github.com/' + HEN_REPOSITORY + '/zip/' + commit_id, pinned / 'archive.zip', MAX_PACK_BYTES)
    return pinned


def load_hen(inputs):
    for name, maximum in [('commit.json', 2 * 1024 * 1024), ('tree.json', 32 * 1024 * 1024), ('archive.zip', MAX_PACK_BYTES)]:
        if (inputs / name).stat().st_size > maximum:
            raise ValueError('Cached remote input exceeds its bound')
    commit = json.loads((inputs / 'commit.json').read_bytes())
    tree = json.loads((inputs / 'tree.json').read_bytes())
    commit_id = commit['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit_id) or tree['truncated'] or tree['sha'] != commit['commit']['tree']['sha']:
        raise ValueError('Incomplete or mismatched source identity')
    files, names = {}, set()
    selected = [item for item in tree['tree'] if item['type'] == 'blob' and item.get('mode') in ('100644', '100755')
                and item['path'].startswith(HEN_FOLDER + '/') and Path(item['path']).suffix.lower() in LOADABLE]
    if len(selected) > 100_000:
        raise ValueError('Too many source entries')
    with zipfile.ZipFile(inputs / 'archive.zip') as source:
        archive_names = source.namelist()
        if len(archive_names) != len(set(archive_names)):
            raise ValueError('Duplicate archive paths')
        total = 0
        for item in selected:
            relative = safe_path(item['path'][len(HEN_FOLDER) + 1:])
            if relative.casefold() in names:
                raise ValueError('Case-colliding source paths')
            names.add(relative.casefold())
            entry = source.getinfo('HEN-Cheats-Collection-' + commit_id + '/' + item['path'])
            if not 0 < item['size'] <= MAX_TRAINER_BYTES or entry.file_size != item['size']:
                raise ValueError('Source entry size mismatch')
            total += entry.file_size
            if total > 256 * 1024 * 1024:
                raise ValueError('Expanded source exceeds bound')
            data = source.read(entry)
            git_hash = hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
            if git_hash != item['sha']:
                raise ValueError('Source Git blob hash mismatch: ' + relative)
            files[relative] = data
    if not files:
        raise ValueError('Source contains no trainers')
    return commit_id, files


def local_files(folder):
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in trainer source')
        if not path.is_file():
            continue
        relative = safe_path(path.relative_to(folder).as_posix())
        if path.suffix.lower() != '.shnext' or not 0 < path.stat().st_size <= MAX_TRAINER_BYTES:
            raise ValueError('ShnExt must contain only bounded .ShnExt trainers: ' + relative)
        files[relative] = path.read_bytes()
    if not files:
        raise ValueError('ShnExt contains no trainers')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--hen-ref', default='master')
    parser.add_argument('--hen-input', type=Path, help='Pinned commit.json, tree.json and archive.zip for offline builds')
    parser.add_argument('--export-hen', type=Path, help='Write validated trainer bytes to a new directory for independent parser checks')
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--source-commit', help='Override source commit for isolated verification only')
    args = parser.parse_args()
    repo = args.repo.resolve()
    inputs = args.hen_input or obtain_hen(repo / '.cache' / 'hen', args.hen_ref)
    hen_commit, hen = load_hen(inputs)
    if args.export_hen:
        args.export_hen.mkdir(parents=True, exist_ok=False)
        for relative, data in hen.items():
            target = args.export_hen / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    if args.inventory_only:
        print(json.dumps({'henCommit': hen_commit, 'henFiles': len(hen), 'henBytes': sum(map(len, hen.values()))}), flush=True)
        return
    commit = args.source_commit or subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True,
              **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})).strip()
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Source must have a valid Git commit')
    covers = [path for path in (repo / 'Covers').rglob('*') if path.is_file()]
    if any(path.is_symlink() or path.suffix.lower() not in ('.jpg', '.jpeg', '.png') for path in covers):
        raise ValueError('Covers must contain only regular JPEG or PNG files')
    metadata = repo / 'metadata.json'
    overrides = json.loads(metadata.read_bytes()) if metadata.exists() else {}
    sources = [{'id': 'shnext', 'repo': 'Pharaoh2k/PSX-Cheats', 'commit': commit, 'folder': 'ShnExt'},
               {'id': 'hen', 'repo': HEN_REPOSITORY, 'commit': hen_commit, 'folder': HEN_FOLDER},
               {'id': 'sony-snapshot', 'repo': 'Pharaoh2k/PSX-Cheats', 'commit': commit, 'folder': 'Covers'}]
    manifest, catalogue, report = build(args.output or repo / 'dist', {'shnext': local_files(repo / 'ShnExt'), 'hen': hen}, covers, sources, overrides)
    print(json.dumps({'releaseTag': manifest['releaseTag'], 'trainerCount': report['trainerCount'], 'coverCount': report['coverCount'],
                      'packCount': len(manifest['packs']), 'packBytes': report['packBytes'], 'aliases': len(report['aliases']),
                      'excludedUnsupported': len(report['excluded']),
                      'missingCoverTitleIds': len(report['missingCoverTitleIds'])}), flush=True)


if __name__ == '__main__':
    main()
