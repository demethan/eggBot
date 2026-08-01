import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "fry_capability_audit.py"
SPEC = importlib.util.spec_from_file_location("fry_capability_audit", MODULE_PATH)
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class CapabilityClassificationTests(unittest.TestCase):
    def test_whitelist_read_classification(self):
        self.assertEqual(AUDIT.classify_read(200), "supported")
        self.assertEqual(AUDIT.classify_read(404), "supported_not_found")
        self.assertEqual(AUDIT.classify_read(403), "permission_denied")
        self.assertEqual(AUDIT.classify_read(500), "http_500")

    def test_non_mutating_write_probe_classification(self):
        self.assertEqual(AUDIT.classify_write_probe(400), "supported")
        self.assertEqual(AUDIT.classify_write_probe(403), "permission_denied")
        self.assertEqual(AUDIT.classify_write_probe(404), "unsupported")
        self.assertEqual(AUDIT.classify_write_probe(200), "unsafe_unexpected_success")


if __name__ == "__main__":
    unittest.main()
