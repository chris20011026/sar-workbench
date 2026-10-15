"""Portable OOXML structure checks; no renderer or document dependency required.

The TableText name regression was reproduced with an actual LibreOffice render:
an unnamed referenced style moved every cell paragraph outside its table.
"""
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from alosstack_helper.reports import docx_report


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class ReportDocxLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        (folder / 'figure.png').write_bytes(PNG)
        self.rows = [['專案', '研究 <A&B>'], ['長設定名稱', '完整資料 ' * 80]]
        sections = [('p', '表格前的介紹'), ('h', '分析結果'),
                    ('table', ['項目', '內容'], self.rows),
                    ('p', '表格後的說明'), ('image', 'figure.png', '成果圖')]
        with patch('alosstack_helper.reports.report_sections', return_value=sections):
            docx_report({'title': '研究報告'}, folder)
        with zipfile.ZipFile(folder / 'report.docx') as document:
            self.doc = ET.fromstring(document.read('word/document.xml'))
            self.styles = ET.fromstring(document.read('word/styles.xml'))

    def test_all_referenced_paragraph_styles_have_unique_display_names(self):
        styles = {s.get(f'{{{W}}}styleId'): s for s in self.styles.findall('w:style', NS)
                  if s.get(f'{{{W}}}type') == 'paragraph'}
        names = []
        for style_id, style in styles.items():
            with self.subTest(style=style_id):
                name = style.find('w:name', NS)
                self.assertIsNotNone(name, 'LibreOffice requires a named paragraph style')
                self.assertTrue(name.get(f'{{{W}}}val', '').strip())
                names.append(name.get(f'{{{W}}}val'))
        self.assertEqual(len(names), len(set(names)))
        for reference in self.doc.findall('.//w:pStyle', NS):
            self.assertIn(reference.get(f'{{{W}}}val'), styles)
        self.assertEqual(styles['TableText'].find('w:name', NS).get(f'{{{W}}}val'), 'Table Text')

    def test_cell_text_keeps_table_style_and_body_paragraphs_stay_outside(self):
        table = self.doc.find('w:body/w:tbl', NS)
        self.assertIsNotNone(table)
        observed = []
        for row in table.findall('w:tr', NS):
            values = []
            for cell in row.findall('w:tc', NS):
                paragraphs = cell.findall('w:p', NS)
                self.assertTrue(paragraphs)
                for paragraph in paragraphs:
                    self.assertEqual(paragraph.find('w:pPr/w:pStyle', NS).get(f'{{{W}}}val'), 'TableText')
                values.append(''.join(cell.itertext()))
            observed.append(values)
        self.assertEqual(observed, [['項目', '內容'], *self.rows])
        body_text = [''.join(p.itertext()) for p in self.doc.findall('w:body/w:p', NS)]
        self.assertIn('表格前的介紹', body_text)
        self.assertIn('表格後的說明', body_text)
        self.assertNotIn('研究 <A&B>', body_text)

    def test_table_width_padding_wrapping_and_repeated_header_are_portable(self):
        table = self.doc.find('w:body/w:tbl', NS)
        props = table.find('w:tblPr', NS)
        width = int(props.find('w:tblW', NS).get(f'{{{W}}}w'))
        columns = [int(c.get(f'{{{W}}}w')) for c in table.findall('w:tblGrid/w:gridCol', NS)]
        self.assertEqual(sum(columns), width)
        self.assertIsNotNone(table.find('w:tr/w:trPr/w:tblHeader', NS))
        self.assertFalse(table.findall('.//w:trHeight', NS), 'fixed heights could clip wrapped content')
        self.assertFalse(table.findall('.//w:noWrap', NS))
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            self.assertEqual(props.find(f'w:tblBorders/w:{edge}', NS).get(f'{{{W}}}color'), 'D9D9D9')
        order = [element.tag.rsplit('}', 1)[-1] for element in props]
        self.assertLess(order.index('tblBorders'), order.index('tblLayout'))
        for row in table.findall('w:tr', NS):
            for index, cell in enumerate(row.findall('w:tc', NS)):
                self.assertEqual(int(cell.find('w:tcPr/w:tcW', NS).get(f'{{{W}}}w')), columns[index])
                order = [element.tag.rsplit('}', 1)[-1] for element in cell.find('w:tcPr', NS)]
                if 'shd' in order:
                    self.assertLess(order.index('shd'), order.index('vAlign'))


if __name__ == '__main__':
    unittest.main()
