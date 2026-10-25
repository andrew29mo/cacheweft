"""Qualification output failure cannot leave an earlier success looking current."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from review.validate import ROOT,write_qualification

class QualificationWriterTests(unittest.TestCase):
    def test_failure_replaces_stale_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'review.json';out.write_text('{"passed":true}')
            with patch('review.validate.qualify',side_effect=ValueError('invalid recorded data')):
                result=write_qualification(out)
            self.assertIs(result['passed'],False)
            self.assertIs(json.loads(out.read_text())['passed'],False)
            self.assertIn('invalid recorded data',out.with_suffix('.html').read_text())
    def test_html_output_cannot_collide_with_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'review.html'
            with self.assertRaises(ValueError):write_qualification(out)
            self.assertFalse(out.exists())
    def test_measured_artifact_cannot_be_output(self):
        target=ROOT/'evidence/closeout.json';before=target.read_bytes()
        with self.assertRaises(ValueError):write_qualification(target)
        self.assertEqual(target.read_bytes(),before)

if __name__=='__main__':unittest.main()
