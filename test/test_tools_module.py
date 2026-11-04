import pathlib
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules import tools


class SharedToolsModuleTests(unittest.TestCase):
    def setUp(self):
        self.original_root = tools.ROOT

    def tearDown(self):
        tools.configure_root(self.original_root)

    def test_root_configuration_and_safe_path(self):
        with tempfile.TemporaryDirectory() as directory:
            tools.configure_root(directory)
            self.assertEqual(tools.safe_path(".").resolve(), pathlib.Path(directory).resolve())
            with self.assertRaises(ValueError):
                tools.safe_path("../outside")

    def test_read_only_dispatch_and_schemas_are_shared(self):
        self.assertIn("read_file", tools.TOOL_DISPATCH_READ_ONLY)
        names = {item["function"]["name"] for item in tools.FUNCTIONS_READ_ONLY}
        self.assertIn("read_file", names)
        self.assertIn("get_project_map", names)


if __name__ == "__main__":
    unittest.main()
