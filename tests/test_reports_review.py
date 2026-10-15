"""Regression checks for portable report text and optional path redaction."""
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from alosstack_helper.reports import docx_report, scrub_paths, source_path, source_identities


class ReportTextReviewTests(unittest.TestCase):
    def snapshot(self):
        return dict(title='研究報告', project=dict(name='Synthetic project', workflow='alosStack',
                    origin='工作台處理紀錄'), created='2026-10-15', author='', version='test',
                    notes='', analyses=[], include_paths=False, source_records=[])

    def document(self, snapshot):
        with tempfile.TemporaryDirectory() as directory:
            docx_report(snapshot, Path(directory))
            with zipfile.ZipFile(Path(directory) / 'report.docx') as archive:
                for name in archive.namelist():
                    if name.endswith(('.xml', '.rels')):
                        ET.fromstring(archive.read(name))
                return archive.read('word/document.xml').decode()

    def test_inherited_metadata_controls_cannot_corrupt_docx(self):
        snapshot = self.snapshot()
        snapshot['project']['name'] = 'before\x00\x01\x0b\x0c\x1fafter'
        document = self.document(snapshot)
        self.assertIn('beforeafter', document)
        for char in ('\x00', '\x01', '\x0b', '\x0c', '\x1f'):
            self.assertNotIn(char, document)

    def test_lone_surrogates_and_noncharacters_removed_valid_unicode_kept(self):
        snapshot = self.snapshot()
        snapshot['project']['name'] = '中文\ud800\udfff\ufffe\uffff😀'
        snapshot['notes'] = 'a\tb\nc\rd'
        document = self.document(snapshot)
        self.assertIn('中文😀', document)
        self.assertIn('a\tb', document)

    def test_markup_in_source_names_remains_text(self):
        snapshot = self.snapshot()
        snapshot['project']['name'] = '<script>text</script> & "name"'
        document = self.document(snapshot)
        root = ET.fromstring(document)
        texts = [e.text for e in root.iter() if e.tag.endswith('}t')]
        self.assertIn(snapshot['project']['name'], texts)
        self.assertNotIn('<script>', document)

    def test_nested_keys_and_values_are_masked_without_losing_duplicate_names(self):
        original = {'/Users/one/folder/a.xml': 'first', '/Volumes/drive/another/a.xml': 'second',
                    'nested': [{'input': '/Users/private/project/velocity.h5'}]}
        masked = scrub_paths(original)
        self.assertEqual(len(masked), 3)
        self.assertEqual(masked['[本機路徑]/a.xml'], 'first')
        self.assertEqual(masked['[本機路徑]/a.xml [2]'], 'second')
        self.assertEqual(masked['nested'][0]['input'], '[本機路徑]/velocity.h5')
        self.assertIn('/Users/one/folder/a.xml', original)

    def test_embedded_and_multiple_unquoted_paths_are_masked(self):
        text = 'Loaded /Users/private/velocity.h5; input=/Volumes/drive/source.tif, next /tmp/cache/file.txt'
        masked = scrub_paths(text)
        for name in ('velocity.h5', 'source.tif', 'file.txt'):
            self.assertIn('[本機路徑]/' + name, masked)
        self.assertNotIn('/Users/', masked)
        self.assertNotIn('/Volumes/', masked)
        self.assertNotIn('/tmp/', masked)
        self.assertEqual(scrub_paths('/Users/one/a.h5 /Volumes/two/b.h5'),
                         '[本機路徑]/a.h5 [本機路徑]/b.h5')

    def test_quoted_or_unquoted_paths_with_spaces_hide_all_parent_folders(self):
        for text in ('read "/Users/Private Person/My Project/result file.h5" now',
                     'read /Users/Private Person/My Project/result file.h5 now'):
            result = scrub_paths(text)
            self.assertNotIn('Private Person', result)
            self.assertNotIn('My Project', result)
            self.assertIn('result file.h5', result)

    def test_windows_paths_are_also_masked(self):
        text = r'input=C:\Users\Private\SAR\velocity.h5; other=\\nas\private\phase.tif'
        result = scrub_paths(text)
        self.assertNotIn('Private', result)
        self.assertNotIn('nas', result)
        self.assertIn('[本機路徑]/velocity.h5', result)
        self.assertIn('[本機路徑]/phase.tif', result)
        self.assertEqual(scrub_paths('//nas/private/phase.tif'), '[本機路徑]/phase.tif')

    def test_urls_units_relative_paths_and_nonstrings_preserved(self):
        values = ['https://example.org/path/to/source?dataset=one',
                  's3://public-data/path/image', 'mm/year', 'range/azimuth',
                  'relative/file.h5', './relative/file.h5', '../relative/file.h5', 123, None, True]
        self.assertEqual(scrub_paths(values), values)
        text = 'Loaded /Users/private/velocity.h5; see https://example.org/paper.pdf'
        result = scrub_paths(text)
        self.assertIn('https://example.org/paper.pdf', result)
        self.assertNotIn('/Users/private', result)

    def test_file_urls_hide_local_hosts_drives_and_parent_names(self):
        values = ['file:///Users/Private Person/project/velocity.h5',
                  'FILE://localhost/Users/Private%20Person/project/velocity.h5',
                  'file://private-nas/share/velocity.h5',
                  'file:///C:/Users/Private/project/velocity.h5']
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(scrub_paths(value),'[本機路徑]/velocity.h5')
        text = 'source=file:///Users/Private Person/project/velocity.h5; see https://example.org/paper.pdf'
        result = scrub_paths(text)
        self.assertNotIn('Private',result)
        self.assertNotIn('file://',result)
        self.assertIn('https://example.org/paper.pdf',result)
        self.assertEqual(scrub_paths({values[0]:'file://private-nas/share/timeseries.h5'}),
                         {'[本機路徑]/velocity.h5':'[本機路徑]/timeseries.h5'})
        self.assertEqual(scrub_paths('file://private-nas'),'[本機路徑]')
        self.assertEqual(scrub_paths('file://private-nas/'),'[本機路徑]')


class ReportSourcePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project/analyses/aaaaaaaaaaaa'
        (self.root/'inputs').mkdir(parents=True)
        for name in ('velocity.h5','maskTempCoh.h5','temporalCoherence.h5',
                     'inputs/geometryRadar.h5','timeseries.h5'):
            (self.root/name).write_bytes(b'synthetic identity-only fixture')
        self.job={'timeseries_file':str(self.root/'timeseries.h5')}

    def test_container_identity_inspection_has_no_scientific_runtime_dependency(self):
        values=source_identities(self.root,self.job)
        self.assertEqual(len(values),5)
        self.assertTrue(all(value['size']>0 for value in values.values()))
        self.assertEqual(source_path(self.root,self.root/'velocity.h5'),self.root/'velocity.h5')

    def test_symlink_parent_is_rejected_even_when_target_remains_inside_analysis(self):
        (self.root/'inputs').rename(self.root/'stored-inputs')
        (self.root/'inputs').symlink_to(self.root/'stored-inputs',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'符號連結'):source_identities(self.root,self.job)

    def test_external_symlink_and_lexical_parent_traversal_are_rejected(self):
        outside=self.root.parent/'foreign.h5';outside.write_bytes(b'outside')
        (self.root/'velocity.h5').unlink();(self.root/'velocity.h5').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'符號連結'):source_identities(self.root,self.job)
        with self.assertRaises(ValueError):source_path(self.root,self.root/'../foreign.h5')
        with self.assertRaises(ValueError):source_path(self.root,outside)

    def test_source_root_symlink_is_rejected(self):
        alias=self.root.parent/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'符號連結'):source_path(alias,alias/'velocity.h5')


if __name__ == '__main__':
    unittest.main()
