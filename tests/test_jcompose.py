import unittest
import os
import json
import tempfile
from pathlib import Path
from jcompose import deep_merge, parse_include, build_search_paths, Resolver, Composer

class TestJComposer(unittest.TestCase):

    def test_deep_merge(self):
        dict_a = {"a": 1, "b": {"c": 2}}
        dict_b = {"b": {"d": 3}, "e": 4}
        expected = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
        self.assertEqual(deep_merge(dict_a, dict_b), expected)

        list_a = [1, 2]
        list_b = [3, 4]
        self.assertEqual(deep_merge(list_a, list_b), [1, 2, 3, 4])

    def test_parse_include(self):
        self.assertEqual(parse_include("file.json"), ("file.json", None, "merge"))
        self.assertEqual(parse_include("file.json::.filter"), ("file.json", ".filter", "merge"))
        self.assertEqual(parse_include("file.json[replace]"), ("file.json", None, "replace"))
        self.assertEqual(parse_include("file.json::.filt[replace]"), ("file.json", ".filt", "replace"))

    def test_search_paths_precedence(self):
        os.environ["JCONF_PATH"] = "/env/path1:/env/path2"
        paths = build_search_paths(cli_path="/cli/path")
        
        self.assertEqual(paths[0], "/cli/path")
        self.assertEqual(paths[1], "/env/path1")
        self.assertIn(".", paths)

    def test_composition_integration(self):
        """Full test using temporary files to simulate a real include."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create a base file
            base_file = tmp_path / "base.json"
            base_data = {"base_key": "base_val", "to_merge": {"x": 1}}
            base_file.write_text(json.dumps(base_data))
            
            # Create a template that includes the base
            template_data = {
                "top_key": "top_val",
                "@include": f"{base_file.name}"
            }
            template_file = tmp_path / "template.json"
            template_file.write_text(json.dumps(template_data))

            resolver = Resolver([tmpdir])
            composer = Composer(resolver)
            
            result = composer.expand(template_data, template_file, [str(template_file)])
            
            self.assertEqual(result["base_key"], "base_val")
            self.assertEqual(result["top_key"], "top_val")

if __name__ == "__main__":
    unittest.main()
