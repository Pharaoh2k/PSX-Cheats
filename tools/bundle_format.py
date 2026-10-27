"""Portable ZIP/catalogue format. Trainer payloads are never rewritten."""
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
from PIL import Image, ImageOps

SCHEMA = 1
MAX_PACK_BYTES = 64 * 1024 * 1024
MAX_TRAINER_BYTES = 32 * 1024 * 1024
TITLE_ID = re.compile(r'^[A-Z]{4}[0-9]{5}$', re.IGNORECASE)
COVER_TITLE_ID = re.compile(r'^(?:CUSA|PPSA)[0-9]{5}$', re.IGNORECASE)
LOADABLE = {'.shnext', '.json', '.shn', '.mc4', '.tlh'}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def safe_path(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or '\\' in value or ':' in value:
        raise ValueError('Unsafe relative path')
    if any(ord(char) < 32 for char in value) or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('Unsafe relative path')
    return value


def filename_metadata(path):
    parts = Path(path).stem.split('_')
    for index, part in enumerate(parts):
        if TITLE_ID.fullmatch(part) and index + 1 < len(parts):
            return {'titleId': part.upper(), 'game': ' '.join(parts[:index]),
                    'version': parts[index + 1], 'author': parts[index + 2] if index + 2 < len(parts) and index > 0 else ''}
    return {'titleId': '', 'game': Path(path).stem, 'version': '', 'author': ''}


def trainer_metadata(source_id, path, data, overrides):
    result = filename_metadata(path)
    if Path(path).suffix.lower() == '.json':
        value = json.loads(data.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('Trainer JSON root must be an object: ' + path)
        for target, key in [('game', 'name'), ('titleId', 'id'), ('version', 'version'), ('author', 'author')]:
            if isinstance(value.get(key), str) and value[key].strip():
                result[target] = value[key].strip()
        if not result['author']:
            credits = value.get('credits')
            if isinstance(credits, list):
                result['author'] = ', '.join(item for item in credits if isinstance(item, str))
            elif isinstance(credits, str):
                result['author'] = credits
    override = overrides.get(source_id + '/' + path)
    if override and override.get('sha256') == sha256(data):
        for name in result:
            if isinstance(override.get(name), str) and override[name].strip():
                result[name] = override[name].strip()
    result['titleId'] = result['titleId'].upper()
    if result['titleId'] and not TITLE_ID.fullmatch(result['titleId']):
        raise ValueError('Invalid trainer title ID: ' + path)
    return result


def thumbnail(path):
    data = path.read_bytes()
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        if source.format not in ('JPEG', 'PNG') or source.width * source.height > 16_000_000:
            raise ValueError('Unsupported cover format or dimensions: ' + path.name)
        target_format = source.format
        extension = '.jpg' if target_format == 'JPEG' else '.png'
        if max(source.size) <= 300 and not source.getexif():
            return data, extension
        image = ImageOps.exif_transpose(source)
        image.thumbnail((300, 300), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        if target_format == 'JPEG':
            image.convert('RGB').save(output, 'JPEG', quality=90, optimize=True)
        else:
            image.save(output, 'PNG', optimize=True, compress_level=9)
        return output.getvalue(), extension


def zip_bytes(entries):
    output = io.BytesIO()
    seen = set()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for name, data in sorted(entries.items()):
            safe_path(name)
            if name.casefold() in seen:
                raise ValueError('Case-colliding ZIP entries')
            seen.add(name.casefold())
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            target.writestr(item, data, compresslevel=6)
    data = output.getvalue()
    if len(data) > MAX_PACK_BYTES:
        raise ValueError('Pack exceeds 64 MiB; split the source before publication')
    return data


def write_asset(output, prefix, extension, data):
    digest = sha256(data)
    name = prefix + '-' + digest + extension
    target = output / name
    if target.exists() and target.read_bytes() != data:
        raise ValueError('Immutable asset collision')
    if not target.exists():
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
    return {'file': name, 'length': len(data), 'sha256': digest}


def build(output, trainers, covers, sources, overrides=None):
    output.mkdir(parents=True, exist_ok=True)
    overrides = overrides or {}
    groups, rows, aliases, excluded, paths = {}, [], [], [], set()
    for source_id, files in sorted(trainers.items()):
        group, merged = {}, {}
        for entry, data in sorted(files.items()):
            safe_path(entry)
            if not 0 < len(data) <= MAX_TRAINER_BYTES or Path(entry).suffix.lower() not in LOADABLE:
                raise ValueError('Invalid trainer size or extension: ' + entry)
            override = overrides.get(source_id + '/' + entry)
            if override and override.get('sha256') == sha256(data) and override.get('supported') is False:
                excluded.append({'sourceId': source_id, 'entry': entry, 'sha256': sha256(data),
                                 'reason': override.get('reason', 'Unsupported trainer payload')})
                continue
            header = trainer_metadata(source_id, entry, data, overrides)
            filename_header = filename_metadata(entry)
            key = (sha256(data), filename_header['titleId'], filename_header['version'].casefold())
            if filename_header['titleId'] and key in merged:
                aliases.append({'sourceId': source_id, 'entry': entry, 'canonicalEntry': merged[key]})
                continue
            merged[key] = entry
            logical = Path(entry).name
            if logical.casefold() in paths:
                logical = '_' + source_id + '/' + entry
            if logical.casefold() in paths:
                raise ValueError('Logical trainer path collision: ' + logical)
            paths.add(logical.casefold())
            group[entry] = data
            rows.append(dict(path=logical, packId=source_id, entry=entry, length=len(data), sha256=sha256(data),
                             gitBlobSha1=hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest(),
                             sourceId=source_id, kind='trainer', **header))
        groups[source_id] = ('trainer', source_id, group)
    cover_titles = set()
    for path in sorted(covers, key=lambda item: (item.stem.upper(), {'.jpg': 0, '.jpeg': 1, '.png': 2}.get(item.suffix.lower(), 3))):
        title = path.stem.upper()
        if not COVER_TITLE_ID.fullmatch(title):
            raise ValueError('Cover filename must be a title ID: ' + path.name)
        if title in cover_titles:
            raise ValueError('Multiple cover files for title ID: ' + title)
        cover_titles.add(title)
        data, extension = thumbnail(path)
        entry = title + extension
        pack_id = 'covers-' + sha256(title.encode('ascii'))[0]
        group = groups.setdefault(pack_id, ('cover', 'sony-snapshot', {}))[2]
        group[entry] = data
        rows.append(dict(path=entry, packId=pack_id, entry=entry, length=len(data), sha256=sha256(data),
                         sourceId='sony-snapshot', kind='cover', titleId=title, game='', version='', author=''))
    packs = []
    for pack_id, (kind, source_id, entries) in sorted(groups.items()):
        if entries:
            packs.append(dict(id=pack_id, kind=kind, sourceId=source_id, count=len(entries),
                              **write_asset(output, pack_id, '.zip', zip_bytes(entries))))
    catalogue = {'schemaVersion': SCHEMA, 'entries': sorted(rows, key=lambda row: (row['kind'], row['path'].casefold()))}
    catalog_asset = write_asset(output, 'catalogue', '.json', encoded(catalogue))
    identity = catalog_asset['sha256']
    manifest = {'schemaVersion': SCHEMA, 'releaseTag': 'bundles-' + identity[:24],
                'sources': sources, 'packs': packs, 'catalogue': catalog_asset}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    report = {'releaseTag': manifest['releaseTag'], 'trainerCount': sum(row['kind'] == 'trainer' for row in rows),
              'coverCount': len(cover_titles), 'aliases': aliases, 'excluded': excluded, 'excludedUnsupportedCount': len(excluded),
              'packBytes': sum(pack['length'] for pack in packs),
              'missingCoverTitleIds': sorted({row['titleId'] for row in rows if row['kind'] == 'trainer' and row['titleId']} - cover_titles)}
    (output / 'build-report.json').write_bytes(encoded(report))
    return manifest, catalogue, report
