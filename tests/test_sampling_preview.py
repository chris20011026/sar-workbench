"""Bounded real byte-layout previews, with no ISCE or source-data mutation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper import sampling_preview_worker as worker
try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None


@unittest.skipUnless(np is not None and Image is not None, 'requires science environment NumPy and Pillow')
class SamplingPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, data, order='LSB', prefix=0, padding=0, name='input.slc'):
        data = np.asarray(data, dtype=np.complex64)
        source = self.root / name
        with source.open('wb') as f:
            f.write(b'H' * prefix)
            for row in data:
                f.write(row.astype('>c8' if order == 'MSB' else '<c8').tobytes())
                f.write(b'P' * padding)
        return dict(image=dict(path=str(source), width=data.shape[1], length=data.shape[0],
                               image_offset=prefix, line_offset=data.shape[1]*8+padding,
                               byte_order=order), output=str(self.root/(name+'.png')), max_size=768,
                    stretch=dict(low=0, high=100))

    def rgba(self, request):
        with Image.open(request['output']) as image:
            return np.array(image)

    def test_physical_and_virtual_endianness_padding_render_identically(self):
        data = np.array([[1, 10, 100], [1j, 10j, 100j]], dtype=np.complex64)
        images = []
        for order, prefix, padding in [('LSB', 0, 0), ('MSB', 1264, 544), ('LSB', 720, 800)]:
            req = self.source(data, order, prefix, padding, f'{order}-{prefix}.slc')
            raw = Path(req['image']['path'])
            before = (raw.read_bytes(), worker.source_stamp(raw.stat()))
            report = worker.preview(req)
            self.assertEqual(report['vmin'], 0)
            self.assertEqual(report['vmax'], 40)
            self.assertEqual(report['scale'], '20 log10 |SLC|')
            self.assertEqual(report['orientation']['coordinates'], 'radar')
            self.assertFalse(report['orientation']['geocoded'])
            self.assertEqual(before, (raw.read_bytes(), worker.source_stamp(raw.stat())))
            images.append(self.rgba(req))
        for image in images[1:]:
            np.testing.assert_array_equal(images[0], image)
        np.testing.assert_array_equal(images[0][0, :, 0], [0, 128, 255])

    def test_phase_rotation_does_not_change_amplitude(self):
        data = np.array([[1+2j, 3+4j], [5+12j, 8+15j]], dtype=np.complex64)
        a = self.source(data, name='a.slc')
        b = self.source(data*1j, name='b.slc')
        first, second = worker.preview(a), worker.preview(b)
        np.testing.assert_array_equal(self.rgba(a), self.rgba(b))
        self.assertEqual((first['vmin'], first['vmax']), (second['vmin'], second['vmax']))

    def test_nan_inf_zero_are_transparent_and_excluded_from_stretch(self):
        req = self.source([[0, complex(float('nan'), 1), complex(float('inf'), 1), 1, 10]])
        result = worker.preview(req)
        self.assertAlmostEqual(result['valid_fraction'], .4)
        self.assertAlmostEqual(result['invalid_fraction'], .4)
        self.assertAlmostEqual(result['zeros_fraction'], .2)
        self.assertEqual((result['vmin'], result['vmax']), (0, 20))
        np.testing.assert_array_equal(self.rgba(req)[0, :, 3], [0, 0, 0, 255, 255])
        json.dumps(result, allow_nan=False)

    def test_constant_zero_all_invalid_and_extreme_values_are_finite(self):
        for value, alpha, constant in [(2+0j, 255, True), (0j, 0, False),
                                       (complex(float('nan'), 0), 0, False),
                                       (complex(3e38, 3e38), 255, True),
                                       (complex(1e-40, 0), 255, True)]:
            with self.subTest(value=value):
                req = self.source(np.full((3, 4), value, dtype=np.complex64))
                result = worker.preview(req)
                self.assertTrue(np.all(self.rgba(req)[:, :, 3] == alpha))
                self.assertEqual(result['constant'], constant)
                if alpha:
                    self.assertTrue(np.all(self.rgba(req)[:, :, 0] == 128))
                else:
                    self.assertIsNone(result['vmin'])
                    self.assertIsNone(result['vmax'])
                json.dumps(result, allow_nan=False)

    def test_downsampling_preserves_radar_orientation_and_bounds_requested_io(self):
        y, x = np.mgrid[:9, :17]
        req = self.source((y*17+x+1).astype('complex64'), 'MSB', 720, 544)
        req['max_size'] = 5
        real_pread = os.pread
        reads = []
        def counted(fd, count, offset):
            reads.append((count, offset))
            return real_pread(fd, count, offset)
        with patch.object(worker.os, 'pread', side_effect=counted):
            result = worker.preview(req)
        self.assertEqual((result['width'], result['height']), (5, 3))
        self.assertEqual((result['range_step'], result['azimuth_step']), (4, 4))
        self.assertEqual(result['sampled_bytes'], 5*3*8)
        self.assertEqual(sum(item[0] for item in reads), 5*3*8)
        self.assertEqual(len(reads), 15)
        self.assertEqual(reads[5][1], 720+4*(17*8+544))
        self.assertGreater(int(self.rgba(req)[-1, 0, 0]), int(self.rgba(req)[0, -1, 0]))
        self.assertLessEqual(result['sample_count'], req['max_size']**2)

    def test_large_sparse_image_samples_only_bounded_bytes(self):
        width, length = 1_000_000, 1_000_000
        raw = self.root/'large.slc'
        # No terabyte fixture is created: layout validation sees the descriptor
        # size through fstat, while deterministic pread supplies selected pixels.
        req = self.source([[1]])
        req['image'].update(width=width, length=length, line_offset=width*8)
        info = worker.layout(req)
        reads = []
        def read(fd, count, offset):
            reads.append((count, offset))
            return np.ones(count//8, dtype='<c8').tobytes()
        with patch.object(worker.os, 'pread', side_effect=read):
            data, count, calls = worker.read_samples(-1, info)
        self.assertLessEqual(data.size, 768**2)
        self.assertEqual(count, data.size*8)
        self.assertEqual(count, sum(n for n, _ in reads))
        self.assertEqual(calls, len(reads))
        self.assertTrue(all(n == 8 for n, _ in reads))
        self.assertLess(reads[-1][1], info['required'])

    def test_bad_layout_and_truncated_source_fail_without_output(self):
        req = self.source([[1, 2], [3, 4]])
        for field, value in [('width', 0), ('length', True), ('line_offset', 15),
                             ('image_offset', -1), ('byte_order', 'native'),
                             ('length', 2**63), ('width', 3)]:
            bad = {**req, 'image': {**req['image'], field: value}}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                worker.preview(bad)
            self.assertFalse(Path(req['output']).exists())
        raw = Path(req['image']['path'])
        raw.write_bytes(raw.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, '大小不足'):
            worker.preview(req)
        self.assertFalse(Path(req['output']).exists())

    def test_invalid_stretch_and_preview_bound_fail(self):
        req = self.source([[1]])
        for extra in [dict(max_size=769), dict(max_size=0), dict(max_size=1.5),
                      dict(stretch=dict(low=98, high=2)), dict(stretch=dict(low=-1, high=99)),
                      dict(stretch=dict(low=0, high=float('nan'))),
                      dict(stretch=dict(low=True, high=99))]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                worker.preview({**req, **extra})

    def test_short_read_and_source_mutation_do_not_publish(self):
        req = self.source([[1, 2], [3, 4]])
        with patch.object(worker.os, 'pread', return_value=b''), self.assertRaisesRegex(ValueError, '不完整'):
            worker.preview(req)
        self.assertFalse(Path(req['output']).exists())
        real_pread = os.pread
        raw = Path(req['image']['path'])
        def changed(fd, count, offset):
            data = real_pread(fd, count, offset)
            os.utime(raw, ns=(raw.stat().st_atime_ns, raw.stat().st_mtime_ns+1_000_000))
            return data
        with patch.object(worker.os, 'pread', side_effect=changed), self.assertRaisesRegex(ValueError, '已改變'):
            worker.preview(req)
        self.assertFalse(Path(req['output']).exists())
        self.assertFalse(list(self.root.glob('.slc-preview-*')))

    def test_cannot_overwrite_source_through_same_path_hardlink_or_symlink(self):
        req = self.source([[1]], name='raw.png')
        raw = Path(req['image']['path'])
        link = self.root/'alias.png'
        link.hardlink_to(raw)
        symlink = self.root/'symbolic.png'
        symlink.symlink_to(raw)
        before = raw.read_bytes()
        for output in (raw, link, symlink):
            with self.subTest(output=output), self.assertRaises(ValueError):
                worker.preview({**req, 'output': str(output)})
        self.assertEqual(raw.read_bytes(), before)

    def test_cli_writes_report_and_rejects_report_source_alias(self):
        req = self.source([[1, 10]])
        request_file = self.root/'request.json'
        request_file.write_text(json.dumps(req))
        report = self.root/'report.json'
        worker.main([str(request_file), str(report)])
        value = json.loads(report.read_text())
        self.assertEqual(value['status'], 'complete')
        self.assertEqual(value['source_stamp'], worker.source_stamp(Path(req['image']['path']).stat()))
        for output in (request_file, Path(req['image']['path']), Path(req['output'])):
            before = output.read_bytes()
            with self.subTest(output=output), self.assertRaises(ValueError):
                worker.main([str(request_file), str(output)])
            self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
