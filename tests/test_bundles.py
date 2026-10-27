from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bundle_format import build, filename_metadata, safe_path, sha256, thumbnail, zip_bytes
from build_bundles import load_hen
from publish_bundles import checked_assets, main as publish


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / 'dist'
        self.name = 'Example_CUSA12345_01.00_Author.ShnExt'
        self.data = b'unchanged trainer container bytes'
        self.sources = [{'id': 'shnext', 'repo': 'Pharaoh2k/PSX-Cheats', 'commit': 'a' * 40}]

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, trainers=None, covers=None, overrides=None):
        return build(self.output, trainers or {'shnext': {self.name: self.data}}, covers or [], self.sources, overrides)

    def test_deterministic_payload_and_catalogue(self):
        manifest, catalogue, report = self.fixture()
        first = (self.output / 'manifest.json').read_bytes()
        self.fixture()
        self.assertEqual(first, (self.output / 'manifest.json').read_bytes())
        with zipfile.ZipFile(self.output / manifest['packs'][0]['file']) as archive:
            self.assertEqual(self.data, archive.read(self.name))
        self.assertEqual(['CUSA12345'], report['missingCoverTitleIds'])
        self.assertEqual('Author', catalogue['entries'][0]['author'])
        altered = self.fixture({'shnext': {self.name: self.data + b'x'}})[0]
        self.assertNotEqual(manifest['releaseTag'], altered['releaseTag'])

    def test_exact_duplicates_merge_but_versions_survive(self):
        files = {self.name: self.data, 'Other_CUSA12345_01.00_Author.ShnExt': self.data,
                 'Other_CUSA12345_02.00_Author.ShnExt': self.data}
        _, catalogue, report = self.fixture({'shnext': files})
        self.assertEqual(2, len(catalogue['entries']))
        self.assertEqual(1, len(report['aliases']))

    def test_conflicting_names_keep_both_payloads(self):
        _, catalogue, _ = self.fixture({'hen': {self.name: self.data}, 'shnext': {self.name: self.data + b'x'}})
        paths = [row['path'] for row in catalogue['entries']]
        self.assertEqual(2, len(set(paths)))
        self.assertIn('_shnext/' + self.name, paths)

    def test_unsafe_names_and_case_aliases_rejected(self):
        for name in ('../file', '/file', 'a//b', 'a\\b', 'C:/b', 'a\0b'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe'):
                safe_path(name)
        with self.assertRaisesRegex(ValueError, 'Case-colliding'):
            zip_bytes({'A.json': b'{}', 'a.json': b'{}'})

    def test_override_hash_guards_metadata(self):
        override = {'shnext/' + self.name: {'sha256': sha256(self.data), 'game': 'Verified name'}}
        self.assertEqual('Verified name', self.fixture(overrides=override)[1]['entries'][0]['game'])
        override['shnext/' + self.name]['sha256'] = '0' * 64
        self.assertEqual('Example', self.fixture(overrides=override)[1]['entries'][0]['game'])

    def test_legacy_sony_ids_and_blank_header_fallback(self):
        name = 'json/SCES01762_01.00_956074d6.json'
        data = b'{"id":"SCES01762","name":"Classic game","version":"01.00"}'
        row = self.fixture({'hen': {name: data}})[1]['entries'][0]
        self.assertEqual('SCES01762', row['titleId'])
        self.assertEqual('01.00', row['version'])
        override = {'shnext/' + self.name: {'sha256': sha256(self.data), 'titleId': ' ', 'game': '', 'version': '  '}}
        row = self.fixture(overrides=override)[1]['entries'][0]
        self.assertEqual('CUSA12345', row['titleId'])
        self.assertEqual('Example', row['game'])
        self.assertEqual('01.00', row['version'])

    def test_unsupported_payload_exclusion_is_hash_bound_and_reported(self):
        override = {'shnext/' + self.name: {'sha256': sha256(self.data), 'supported': False, 'reason': 'Known unsupported format'}}
        manifest, catalogue, report = self.fixture(overrides=override)
        self.assertEqual([], catalogue['entries'])
        self.assertEqual('Known unsupported format', report['excluded'][0]['reason'])
        self.assertEqual(self.name, report['excluded'][0]['entry'])
        changed = self.fixture({'shnext': {self.name: self.data + b'x'}}, overrides=override)
        self.assertEqual(1, len(changed[1]['entries']))
        self.assertEqual([], changed[2]['excluded'])

    def test_large_cover_is_resized_without_editing_input(self):
        source = self.root / 'CUSA12345.png'
        Image.new('RGBA', (800, 1200), (10, 20, 30, 128)).save(source)
        original = source.read_bytes()
        manifest, catalogue, report = self.fixture(covers=[source])
        self.assertEqual(original, source.read_bytes())
        cover = next(row for row in catalogue['entries'] if row['kind'] == 'cover')
        pack = next(pack for pack in manifest['packs'] if pack['id'] == cover['packId'])
        with zipfile.ZipFile(self.output / pack['file']) as archive, archive.open(cover['entry']) as stream, Image.open(stream) as image:
            self.assertEqual((200, 300), image.size)
            self.assertEqual('RGBA', image.mode)
        self.assertEqual([], report['missingCoverTitleIds'])
        source.write_bytes(b'not an image')
        with self.assertRaises(OSError):
            thumbnail(source)

    def test_opaque_png_and_jpeg_encode_jpeg88_without_editing_sources(self):
        for name, mode in [('CUSA12345.png', 'RGBA'), ('PPSA54321.jpg', 'RGB')]:
            with self.subTest(name=name):
                source = self.root / name
                color = (10, 20, 30, 255) if mode == 'RGBA' else (10, 20, 30)
                Image.new(mode, (256, 200), color).save(source)
                original = source.read_bytes()
                data, extension = thumbnail(source)
                self.assertEqual('.jpg', extension)
                self.assertEqual(original, source.read_bytes())
                target = self.root / 'result.jpg'
                target.write_bytes(data)
                with Image.open(target) as image:
                    self.assertEqual('JPEG', image.format)
                    self.assertEqual((256, 200), image.size)
                _, catalogue, _ = self.fixture(covers=[source])
                row = next(row for row in catalogue['entries'] if row['kind'] == 'cover')
                self.assertEqual(source.stem + '.jpg', row['entry'])
                self.assertEqual(row['entry'], row['path'])

    def test_transparent_palette_png_keeps_alpha_and_extension(self):
        source = self.root / 'CUSA12345.png'
        image = Image.new('P', (16, 16), 1)
        image.putpalette([0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254)
        image.putpixel((0, 0), 0)
        image.save(source, transparency=0)
        original = source.read_bytes()
        data, extension = thumbnail(source)
        self.assertEqual('.png', extension)
        target = self.root / 'transparent.png'
        target.write_bytes(data)
        with Image.open(target) as decoded:
            self.assertEqual(0, decoded.convert('RGBA').getpixel((0, 0))[3])
            self.assertEqual(255, decoded.convert('RGBA').getpixel((1, 1))[3])
        self.assertEqual(original, source.read_bytes())

    def test_asset_corruption_is_rejected_before_publication(self):
        manifest = self.fixture()[0]
        checked_assets(self.output)
        (self.output / manifest['packs'][0]['file']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'Asset bytes differ'):
            checked_assets(self.output)

    def test_invalid_json_trainer_stops_build(self):
        with self.assertRaises(json.JSONDecodeError):
            self.fixture({'hen': {'CUSA12345_01.00.json': b'not json'}})

    def hen_inputs(self, bad_hash=False):
        folder = self.root / 'hen'
        folder.mkdir()
        commit = 'a' * 40
        blob = hashlib.sha1(b'blob ' + str(len(self.data)).encode() + b'\0' + self.data).hexdigest()
        (folder / 'commit.json').write_text(json.dumps({'sha': commit, 'commit': {'tree': {'sha': 'tree'}}}))
        (folder / 'tree.json').write_text(json.dumps({'sha': 'tree', 'truncated': False, 'tree': [
            {'type': 'blob', 'mode': '100644', 'path': 'cheats/example.shn', 'size': len(self.data), 'sha': '0' * 40 if bad_hash else blob}]}))
        with zipfile.ZipFile(folder / 'archive.zip', 'w') as archive:
            archive.writestr('HEN-Cheats-Collection-' + commit + '/cheats/example.shn', self.data)
        return folder

    def test_hen_tree_blob_identity(self):
        folder = self.hen_inputs()
        self.assertEqual({'example.shn': self.data}, load_hen(folder)[1])
        tree = json.loads((folder / 'tree.json').read_bytes())
        tree['tree'][0]['sha'] = '0' * 40
        (folder / 'tree.json').write_text(json.dumps(tree))
        with self.assertRaisesRegex(ValueError, 'Git blob hash'):
            load_hen(folder)

    def test_publication_is_draft_first_and_commit_last(self):
        manifest, _, _ = self.fixture()
        calls, remote = [], {}
        def command(args, **kwargs):
            words = args[1:]
            calls.append(words)
            if words[:1] == ['api']:
                value = {'full_name': 'Pharaoh2k/PSX-Cheats', 'permissions': {'push': True}}
            elif words[:2] == ['release', 'view']:
                if not remote:
                    return subprocess.CompletedProcess(args, 1, '', 'release not found')
                value = {'isDraft': True, 'assets': [{'name': name, 'size': size} for name, size in remote.items()]}
            elif words[:2] == ['release', 'upload']:
                file = Path(words[3])
                remote[file.name] = file.stat().st_size
                value = None
            else:
                value = None
            return subprocess.CompletedProcess(args, 0, json.dumps(value), '')
        with patch('publish_bundles.subprocess.run', side_effect=command), patch.object(sys, 'argv', ['publish', '--output', str(self.output)]):
            publish()
        create = next(index for index, call in enumerate(calls) if call[:2] == ['release', 'create'])
        uploads = [index for index, call in enumerate(calls) if call[:2] == ['release', 'upload']]
        edit = next(index for index, call in enumerate(calls) if call[:2] == ['release', 'edit'])
        self.assertIn('--draft', calls[create])
        self.assertLess(create, min(uploads))
        self.assertGreater(edit, max(uploads))
        self.assertEqual(len(manifest['packs']) + 3, len(uploads))

    def test_interrupted_upload_never_publishes(self):
        self.fixture()
        calls = []
        def command(args, **kwargs):
            words = args[1:]
            calls.append(words)
            if words[:1] == ['api']:
                return subprocess.CompletedProcess(args, 0, json.dumps({'full_name': 'Pharaoh2k/PSX-Cheats', 'permissions': {'push': True}}), '')
            if words[:2] == ['release', 'view']:
                return subprocess.CompletedProcess(args, 1, '', 'release not found')
            if words[:2] == ['release', 'upload']:
                return subprocess.CompletedProcess(args, 1, '', 'controlled upload interruption')
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch('publish_bundles.subprocess.run', side_effect=command), patch.object(sys, 'argv', ['publish', '--output', str(self.output)]):
            with self.assertRaisesRegex(RuntimeError, 'controlled upload interruption'):
                publish()
        self.assertFalse(any(call[:2] == ['release', 'edit'] for call in calls))

    def test_unchanged_published_catalogue_is_not_overwritten(self):
        self.fixture()
        calls = []
        def command(args, **kwargs):
            words = args[1:]
            calls.append(words)
            if words[:1] == ['api']:
                value = {'full_name': 'Pharaoh2k/PSX-Cheats', 'permissions': {'push': True}}
            elif words[:2] == ['release', 'view']:
                value = {'isDraft': False, 'assets': []}
            elif words[:2] == ['release', 'download']:
                destination = Path(words[words.index('--dir') + 1]) / 'manifest.json'
                destination.write_bytes((self.output / 'manifest.json').read_bytes())
                value = None
            else:
                self.fail('Unexpected mutation for unchanged content: ' + str(words))
            return subprocess.CompletedProcess(args, 0, json.dumps(value), '')
        with patch('publish_bundles.subprocess.run', side_effect=command), patch.object(sys, 'argv', ['publish', '--output', str(self.output)]):
            publish()
        self.assertEqual(3, len(calls))


if __name__ == '__main__':
    unittest.main()
