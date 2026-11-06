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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        tools.configure_root(self.root)

    def tearDown(self):
        tools.configure_root(self.original_root)
        self.temp_dir.cleanup()

    def test_root_configuration_and_safe_path(self):
        self.assertEqual(tools.safe_path(".").resolve(), self.root.resolve())
        with self.assertRaises(ValueError):
            tools.safe_path("../outside")

    def test_read_only_dispatch_and_schemas_are_shared(self):
        self.assertIn("read_file", tools.TOOL_DISPATCH_READ_ONLY)
        names = {item["function"]["name"] for item in tools.FUNCTIONS_READ_ONLY}
        self.assertIn("read_file", names)
        self.assertIn("get_project_map", names)

    def test_list_files_empty_and_populated_directory(self):
        # Empty dir
        sub = self.root / "empty_dir"
        sub.mkdir()
        self.assertEqual(tools.list_files("empty_dir"), "(empty)")

        # Directory with file and nested dir
        (sub / "file.txt").write_text("hello", encoding="utf-8")
        (sub / "nested").mkdir()
        listing = tools.list_files("empty_dir")
        self.assertIn("empty_dir/file.txt", listing)
        self.assertIn("empty_dir/nested/", listing)

        # Non-directory path
        self.assertEqual(tools.list_files("empty_dir/file.txt"), "Not a directory.")

    def test_read_file_bounds_and_validations(self):
        file_path = self.root / "sample.txt"
        file_path.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")

        # Valid read with offset and limit
        chunk = tools.read_file("sample.txt", offset=1, limit=2)
        self.assertIn("Lines 2-3 of 5:", chunk)
        self.assertIn("line 2", chunk)
        self.assertIn("line 3", chunk)

        # Invalid offset (negative)
        self.assertIn("Invalid offset", tools.read_file("sample.txt", offset=-1))

        # Invalid offset (>= total lines)
        self.assertIn("Invalid offset", tools.read_file("sample.txt", offset=10))

        # Invalid limit (<= 0)
        self.assertIn("Invalid limit", tools.read_file("sample.txt", offset=0, limit=0))
        self.assertIn("Invalid limit", tools.read_file("sample.txt", offset=0, limit=-5))

        # Non-existent file
        self.assertEqual(tools.read_file("missing.txt"), "File does not exist.")

        # Non-UTF8 binary file
        bin_path = self.root / "binary.bin"
        bin_path.write_bytes(b"\x80\xFF\xFE\xFD")
        self.assertEqual(tools.read_file("binary.bin"), "File is not UTF-8 text.")

    def test_search_files_single_and_recursive(self):
        sub = self.root / "src"
        sub.mkdir()
        (sub / "a.py").write_text("def find_me():\n    return 42\n", encoding="utf-8")
        (sub / "b.txt").write_text("another find_me reference\n", encoding="utf-8")
        (sub / "c.py").write_text("no match here\n", encoding="utf-8")

        # Recursive search in directory
        results = tools.search_files("find_me", "src")
        self.assertIn("src/a.py:1: def find_me():", results)
        self.assertIn("src/b.txt:1: another find_me reference", results)
        self.assertNotIn("c.py", results)

        # Single file search
        single_result = tools.search_files("find_me", "src/a.py")
        self.assertIn("src/a.py:1: def find_me():", single_result)
        self.assertNotIn("src/b.txt", single_result)

        # Non-existent path
        self.assertEqual(tools.search_files("query", "non_existent"), "Path does not exist.")

        # No matches found
        self.assertEqual(tools.search_files("nonexistent_string", "src"), "No matches found.")


if __name__ == "__main__":
    unittest.main()
