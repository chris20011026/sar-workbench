"""Synthetic CEOS products verify full conversion preservation and real ISCE2 parsing.

No satellite data or machine-specific environment paths are included. The reader
tests run when this suite is invoked in an ISCE2 environment; path safety tests
also run in the lightweight workbench environment.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from alosstack_helper import alos4_quality as quality
from alosstack_helper.alos4_conversion import convert_img, original_output_name

try:
    with contextlib.redirect_stdout(io.StringIO()):
        import isce
        from isceobj.Sensor import xmlPrefix
    SCHEMA = Path(xmlPrefix) / 'alos2_slc'
except ImportError:
    SCHEMA = None


def _put(data, offset, size, value):
    value = str(value).encode('ascii')
    if len(value) > size:
        raise ValueError('fixture field too long')
    data[offset:offset+size] = value.ljust(size, b' ')


def _record(name, values, size):
    """Create synthetic upstream-schema records, including orbital structs."""
    data = bytearray()
    def append(nodes, row=None):
        for element in nodes:
            key = element.get('name')
            if element.tag == 'struct':
                count = int(element.get('nloop') or values[element.get('loop')])
                for index in range(count):
                    append(element, index)
                continue
            kind, n = int(element.get('type')), int(element.get('num'))
            value = values.get(key, 0)
            if isinstance(value, list): value = value[row]
            if kind == 0: data.extend(b' ' * n)
            elif kind in (1, 2, 5): data.extend(str(value).encode('ascii').ljust(n, b' ')[:n])
            else: data.extend(struct.pack('>' + {3:'B', 4:'I', 6:'H', 9:'Q'}[kind], int(value)))
    append(ET.parse(SCHEMA / name).getroot())
    if len(data) > size: raise ValueError('fixture record too small')
    data.extend(b' ' * (size - len(data)))
    struct.pack_into('>I', data, 8, size)
    return data


def fixture(root, lines=11, width=17):
    source, output = root / 'source', root / 'output'
    source.mkdir(); output.mkdir()
    suffix = 'ALOS41370480260101UWD-RA0107-1.1__-'
    led_name = 'LED-' + suffix
    target_led = original_output_name(led_name)
    scene = _record('scene_record.xml', {
        'Scene centre time': '20260101120000000', 'Scene id': target_led[4:20],
        'Scene reference number': target_led[20:].rsplit('-', 1)[0],
        'Sensor platform mission identifier': 'ALOS2',
        'Sensor ID and mode of operation for this channel': 'ALOS2 -L -0115-',
        'Sensor clock angle': 90, 'Radar wavelength': .2424443,
        'Range sampling rate in MHz': 98.2421869, 'Pulse Repetition Frequency in mHz': 2791764,
        'Orbit number': 4415, 'Product level code': '1.1',
        'Time direction indicator along line direction': 'ASCEND',
        'Line spacing': 3, 'Pixel spacing': 1.5,
    }, 4096)
    # The source and converted summary deliberately differ only in documented
    # identifier/layout locations. Real decoder reads the output summary.
    p3scene = bytearray(scene) + bytearray(b' ' * 584)
    struct.pack_into('>I', p3scene, 8, 4680)
    _put(p3scene, 20, 48, suffix[:-7]); _put(p3scene, 396, 16, 'ALOS4')
    _put(p3scene, 412, 32, 'ALOS4 -L -0815-')
    p3scene[1822:1830] = scene[1534:1542]
    p3scene[2282:2298] = scene[1686:1702]
    p3scene[2298:2314] = scene[1702:1718]
    orbit = _record('platform_position_record.xml', {
        'Orbital elements designator': '2', 'Number of data points': 2,
        'Year of data point': 2026, 'Month of data point': 1, 'Day of data point': 1,
        'Day in the year': 1, 'Seconds of day': 43200, 'Time interval between data points': 60,
        'Position vector X': [7000000, 7001000], 'Position vector Y': [1000000, 1001000],
        'Position vector Z': [2000000, 2001000], 'Velocity vector X': 10,
        'Velocity vector Y': 7000, 'Velocity vector Z': 100,
    }, 4680)
    facility = _record('facility_record.xml', {}, 5000)
    p3facility = bytearray(facility) + bytearray(b' ' * 4500)
    struct.pack_into('>I', p3facility, 8, 9500)
    common = {}
    for index in (3, 4, 5, 6, 7, 8, 9):
        data = bytearray(b' ' * quality.SOURCE_LED_RECORDS[index])
        struct.pack_into('>I', data, 8, len(data)); common[index] = data
    radiometry = bytearray(common[4][:9860]); struct.pack_into('>I', radiometry, 8, 9860)
    before = [_record('leader_file.xml', {}, 720), p3scene, orbit, common[3], common[4],
              common[5], common[6], common[7], common[8], common[9], p3facility]
    after = [_record('leader_file.xml', {}, 720), scene, orbit, common[3], radiometry,
             common[5], common[6], common[7], common[8], common[9], facility]
    (source / led_name).write_bytes(b''.join(before)); (output / target_led).write_bytes(b''.join(after))
    for prefix, sizes in [('VOL-', [360] * 6), ('TRL-', [720])]:
        records = []
        for size in sizes:
            data = bytearray(b' ' * size); struct.pack_into('>I', data, 8, size); records.append(data)
        (source / (prefix + suffix)).write_bytes(b''.join(records))
        (output / original_output_name(prefix + suffix)).write_bytes(b''.join(records))
    samples = {}
    for pol, receiver in [('HH', 0), ('HV', 1)]:
        record = 800 + width * 8
        desc = _record('image_file.xml', {
            'Number of SAR DATA records': lines, 'SAR DATA record length': record,
            'Number of bits per sample': 32, 'Number of samples per data group': 2,
            'Number of bytes per data group': 8, 'Number of SAR channels in this file': 1,
            'Number of lines per data set': lines, 'Number of pixels per line per SAR channel': width,
            'Number of physical records per line': 1, 'Number of physical records per multi-channel line': 1,
            'Number of bytes of prefix data per record': 800, 'Number of bytes of SAR data per record': width * 8,
            'SAR Data format type identifier': 'COMPLEX*8', 'SAR Data format type code': 'C*8',
        }, 720)
        image = source / ('IMG-' + pol + '-' + suffix)
        samples[pol] = bytearray()
        with image.open('wb') as stream:
            stream.write(desc)
            for row in range(lines):
                prefix = bytearray(800)
                for offset, value in [(0, row+2), (8, record), (12, row+1), (16, 1), (24, width),
                                      (32, 1), (36, 2026), (40, 1), (44, 43200000+row*10),
                                      (56, 2791764), (116, 78638943)]:
                    struct.pack_into('>I', prefix, offset, value)
                struct.pack_into('>HH', prefix, 52, 0, receiver)
                data = struct.pack('>' + 'f' * width * 2, *[row + receiver + x / 7 for x in range(width * 2)])
                samples[pol].extend(data); stream.write(prefix); stream.write(data)
        with contextlib.redirect_stdout(io.StringIO()):
            convert_img(image, output / original_output_name(image.name))
    return source, output, samples


class QualityPathTests(unittest.TestCase):
    def test_symbolic_product_directory_rejected_with_failed_report(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as d:
            root = Path(d); (root/'real').mkdir(); (root/'out').mkdir()
            (root/'link').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaises(quality.QualityError) as caught:
                quality.verify_conversion(root/'link', root/'out')
            self.assertEqual(caught.exception.report['status'], 'failed')
            self.assertIn('符號連結', str(caught.exception))
            json.dumps(caught.exception.report, allow_nan=False)


@unittest.skipIf(SCHEMA is None, 'requires installed ISCE2 for actual CEOS reader checks')
class ConversionQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.source, self.output, self.samples = fixture(self.root)

    def verify(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return quality.verify_conversion(self.source, self.output, **kwargs)

    def test_every_sample_and_file_hash_matches_and_real_reader_runs_without_writes(self):
        before = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                  for folder in (self.source, self.output) for p in folder.iterdir()}
        events = []
        result = self.verify(progress=events.append)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['scientific_validation']['status'], 'not_performed')
        self.assertTrue(result['reader']['metadata_only']); self.assertFalse(result['reader']['slc_extraction'])
        self.assertAlmostEqual(result['reader']['range_sampling_hz'], 98242186.9)
        self.assertEqual(result['reader']['orbit_vectors'], 2)
        for image in result['images']:
            digest = hashlib.sha256(self.samples[image['polarization']]).hexdigest()
            self.assertEqual(image['source_samples_sha256'], digest)
            self.assertEqual(image['output_samples_sha256'], digest)
            self.assertEqual(image['source_sha256'], before[self.source/image['source']][1])
            self.assertEqual(image['output_sha256'], before[self.output/image['output']][1])
        after = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                 for folder in (self.source, self.output) for p in folder.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(result['samples']['total_sample_bytes'], sum(map(len, self.samples.values())))
        self.assertTrue(any(e['phase'] == 'samples' for e in events))
        self.assertEqual(events[-1]['phase'], 'complete')
        json.dumps(result, allow_nan=False)

    def test_one_corrupt_sample_in_last_line_fails_not_sparse_sampling(self):
        path = next(self.output.glob('IMG-HH-*'))
        data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
        with self.assertRaisesRegex(quality.QualityError, '第 11 行複數樣本') as caught:
            self.verify()
        self.assertEqual(caught.exception.report['status'], 'failed')

    def test_upstream_reader_needs_no_locally_patched_sampling_helper(self):
        from isceobj.Sensor.MultiMode.ALOS2 import ALOS2
        # Keep the actual readLeader and CEOS decoder. Only make a non-upstream
        # convenience method unavailable, as it is in a clean installation.
        with patch.object(ALOS2, 'getRangeSamplingRate', None, create=True):
            result = self.verify()
        self.assertEqual(result['reader']['status'], 'passed')
        self.assertAlmostEqual(result['reader']['range_sampling_hz'], 98242186.9)

    def test_prefix_mutation_or_truncation_is_rejected(self):
        path = next(self.output.glob('IMG-HV-*')); original = path.read_bytes()
        with path.open('r+b') as f: f.seek(720 + 56); f.write(struct.pack('>I', 12))
        with self.assertRaisesRegex(quality.QualityError, 'prefix'): self.verify()
        path.write_bytes(original[:-1])
        with self.assertRaisesRegex(quality.QualityError, '截斷'): self.verify()

    def test_changed_leader_sampling_rate_not_hidden_by_readable_ceos(self):
        path = next(self.output.glob('LED-*')); data = bytearray(path.read_bytes())
        _put(data, 720 + 710, 16, 32.7473956); path.write_bytes(data)
        with self.assertRaisesRegex(quality.QualityError, '採樣率'): self.verify()

    def test_invalid_ascii_cannot_be_silently_decoded_as_zero(self):
        path = next(self.source.glob('LED-*')); target = next(self.output.glob('LED-*'))
        for p in (path, target):
            data = bytearray(p.read_bytes()); data[720+710:720+726] = b'not-a-number    '; p.write_bytes(data)
        with self.assertRaisesRegex(quality.QualityError, '有效數值'): self.verify()

    def test_changed_polarization_and_date_rejected_even_when_both_streams_match(self):
        source = next(self.source.glob('IMG-HV-*')); target = next(self.output.glob('IMG-HV-*'))
        for p in (source, target):
            with p.open('r+b') as f: f.seek(720+54); f.write(struct.pack('>H', 0))
        with self.assertRaisesRegex(quality.QualityError, '極化'): self.verify()

    def test_changed_record_date_and_missing_output_are_explicit(self):
        source = next(self.source.glob('IMG-HH-*')); target = next(self.output.glob('IMG-HH-*'))
        for p in (source, target):
            with p.open('r+b') as f: f.seek(720+40); f.write(struct.pack('>I', 80))
        with self.assertRaisesRegex(quality.QualityError, '日期'): self.verify()
        target.unlink()
        with self.assertRaisesRegex(quality.QualityError, '不完整'): self.verify()

    def test_symlink_image_and_source_output_hardlink_refused(self):
        image = next(self.output.glob('IMG-*')); original = image.read_bytes()
        image.unlink(); image.symlink_to(next(self.source.glob('IMG-*')))
        with self.assertRaisesRegex(quality.QualityError, '符號連結'): self.verify()
        image.unlink(); image.write_bytes(original)
        trailer = next(self.output.glob('TRL-*')); trailer.unlink(); trailer.hardlink_to(next(self.source.glob('TRL-*')))
        with self.assertRaisesRegex(quality.QualityError, '硬連結'): self.verify()

    def test_source_changed_during_progress_invalidates_full_audit(self):
        modified = False
        def progress(event):
            nonlocal modified
            if event['phase'] == 'samples' and not modified:
                modified = True
                path = next(self.source.glob('VOL-*'))
                original = path.stat()
                with path.open('r+b') as f: f.seek(18); f.write(b'X')
                # Even restored size/mtime must not hide an in-place mutation.
                os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaisesRegex(quality.QualityError, '檢查期間') as caught:
            self.verify(progress=progress)
        self.assertEqual(caught.exception.report['input_verification']['status'], 'not_confirmed')

    def test_orbit_loop_bounds_and_non_numeric_vectors_fail_before_reader(self):
        source = next(self.source.glob('LED-*')); target = next(self.output.glob('LED-*'))
        originals = {p: p.read_bytes() for p in (source, target)}
        for p, scene_length in ((source, 4680), (target, 4096)):
            data = bytearray(originals[p]); _put(data, 720+scene_length+140, 4, 9999); p.write_bytes(data)
        with self.assertRaisesRegex(quality.QualityError, '軌道向量數超過'): self.verify()
        for p, scene_length in ((source, 4680), (target, 4096)):
            data = bytearray(originals[p]); _put(data, 720+scene_length+386, 22, 'NaN'); p.write_bytes(data)
        with self.assertRaisesRegex(quality.QualityError, '向量.*有效數值'): self.verify()

    def test_progress_covers_chunks_and_cancellation_does_not_report_pass(self):
        events = []
        with patch.object(quality, 'CHUNK_BYTES', 256):
            result = self.verify(progress=events.append)
        chunks = [event for event in events if event['phase'] == 'samples']
        self.assertGreater(len(chunks), 4)
        self.assertEqual(sum(event['bytes_checked'] for event in chunks if event['completed'] == event['total']),
                         result['samples']['total_sample_bytes'])
        def cancel(event):
            if event['phase'] == 'samples': raise RuntimeError('user cancelled')
        with self.assertRaisesRegex(quality.QualityError, 'user cancelled') as caught:
            self.verify(progress=cancel)
        self.assertEqual(caught.exception.report['status'], 'failed')
        self.assertEqual(caught.exception.report['samples']['status'], 'failed')

    def test_reader_failure_never_becomes_a_pass(self):
        from isceobj.Sensor.MultiMode.ALOS2 import ALOS2
        with patch.object(ALOS2, 'readLeader', side_effect=RuntimeError('reader stopped')):
            with self.assertRaisesRegex(quality.QualityError, 'ISCE2.*reader stopped') as caught:
                self.verify()
        self.assertEqual(caught.exception.report['samples']['status'], 'passed')
        self.assertEqual(caught.exception.report['status'], 'failed')
        self.assertEqual(caught.exception.report['reader']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
