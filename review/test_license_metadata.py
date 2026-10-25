"""Only licensing fields may bridge the historical package configuration."""
from pathlib import Path
import unittest
from review.validate import ROOT,original,license_metadata_only

class LicenseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.baseline=original('pyproject.toml');self.current=(ROOT/'pyproject.toml').read_bytes()
    def test_only_mit_fields_are_accepted(self):self.assertTrue(license_metadata_only(self.baseline,self.current))
    def test_dependency_change_is_rejected(self):
        self.assertFalse(license_metadata_only(self.baseline,self.current.replace(b'dependencies = []',b'dependencies = ["unexpected"]')))
    def test_extra_license_file_is_rejected(self):
        self.assertFalse(license_metadata_only(self.baseline,self.current.replace(b'license-files = ["LICENSE"]',b'license-files = ["LICENSE", "AGENTS.md"]')))

if __name__=='__main__':unittest.main()
