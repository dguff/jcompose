import unittest
import os
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from jcompose import (
    deep_merge, parse_include, parse_ref, split_mode, build_search_paths,
    Resolver, RefResolver, Composer,
)

HAVE_JQ = shutil.which("jq") is not None
requires_jq = unittest.skipUnless(HAVE_JQ, "jq binary not found")


class TempFilesMixin:
    """Creates a temporary directory and helpers to write and compose JSON files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        path = self.tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def compose(self, name, data):
        path = self.write(name, data).resolve()
        composer = Composer(Resolver([str(self.tmp_path)]))
        return composer.expand_file(path, [str(path)])


########################################
# Pure functions (no jq needed)
########################################

class TestDeepMerge(unittest.TestCase):

    def test_dicts_merge_recursively(self):
        dict_a = {"a": 1, "b": {"c": 2}}
        dict_b = {"b": {"d": 3}, "e": 4}
        expected = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
        self.assertEqual(deep_merge(dict_a, dict_b), expected)

    def test_lists_concatenate(self):
        self.assertEqual(deep_merge([1, 2], [3, 4]), [1, 2, 3, 4])

    def test_scalar_overlay_wins(self):
        self.assertEqual(deep_merge({"size": 100}, {"size": 200}), {"size": 200})

    def test_type_mismatch_overlay_wins(self):
        self.assertEqual(deep_merge({"x": {"a": 1}}, {"x": 5}), {"x": 5})

    def test_inputs_not_mutated(self):
        a = {"b": {"c": 1}}
        deep_merge(a, {"b": {"d": 2}})
        self.assertEqual(a, {"b": {"c": 1}})

    def test_ref_placeholder_replaces_instead_of_merging(self):
        base = {"cond": {"temp": 87}}
        overlay = {"cond": {"@ref": ".defaults"}}
        self.assertEqual(deep_merge(base, overlay), {"cond": {"@ref": ".defaults"}})


class TestParsing(unittest.TestCase):

    def test_parse_include(self):
        self.assertEqual(parse_include("file.json"), ("file.json", None, "merge"))
        self.assertEqual(parse_include("file.json::.filter"), ("file.json", ".filter", "merge"))
        self.assertEqual(parse_include("file.json(replace)"), ("file.json", None, "replace"))
        self.assertEqual(parse_include("file.json::.filt(replace)"), ("file.json", ".filt", "replace"))
        self.assertEqual(parse_include("file.json::.filt(merge)"), ("file.json", ".filt", "merge"))

    def test_parse_include_keeps_jq_parentheses(self):
        self.assertEqual(
            parse_include("file.json::.items | map(.name)"),
            ("file.json", ".items | map(.name)", "merge"),
        )

    def test_parse_include_splits_on_first_separator(self):
        self.assertEqual(
            parse_include("file.json::.a | {x: .b}"),
            ("file.json", ".a | {x: .b}", "merge"),
        )

    def test_parse_ref(self):
        self.assertEqual(parse_ref(".materials"), (".materials", "merge"))
        self.assertEqual(parse_ref(".materials(replace)"), (".materials", "replace"))
        self.assertEqual(parse_ref(".materials | map(ascii_upcase)"),
                         (".materials | map(ascii_upcase)", "merge"))

    def test_split_mode_ignores_unknown_suffix(self):
        self.assertEqual(split_mode(".a | f(x)"), (".a | f(x)", "merge"))


class TestSearchPaths(unittest.TestCase):

    def test_search_paths_precedence(self):
        with mock.patch.dict(os.environ, {"JCONF_PATH": "/env/path1:/env/path2"}):
            paths = build_search_paths(cli_path="/cli/path")
        self.assertEqual(paths, ["/cli/path", "/env/path1", "/env/path2", "."])

    def test_search_paths_deduplicated(self):
        with mock.patch.dict(os.environ, {"JCONF_PATH": "/a:/b"}):
            paths = build_search_paths(cli_path="/a:.")
        self.assertEqual(paths, ["/a", ".", "/b"])

    def test_search_paths_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(build_search_paths(), ["."])


class TestResolver(TempFilesMixin, unittest.TestCase):

    def test_plain_name_found_in_subdirectory(self):
        self.write("deep/nested/params.json", {})
        found = Resolver([str(self.tmp_path)]).resolve("params.json", self.tmp_path)
        self.assertEqual(found, (self.tmp_path / "deep/nested/params.json").resolve())

    def test_explicit_relative_path(self):
        self.write("sub/params.json", {})
        found = Resolver([]).resolve("./sub/params.json", self.tmp_path)
        self.assertEqual(found, (self.tmp_path / "sub/params.json").resolve())

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            Resolver([str(self.tmp_path)]).resolve("nope.json", self.tmp_path)


########################################
# @include (needs jq for filters)
########################################

class TestInclude(TempFilesMixin, unittest.TestCase):

    def test_composition_integration(self):
        """Full test using temporary files to simulate a real include."""
        self.write("base.json", {"base_key": "base_val", "to_merge": {"x": 1}})
        result = self.compose("template.json", {
            "top_key": "top_val",
            "to_merge": {"y": 2},
            "@include": "base.json",
        })
        self.assertEqual(result, {
            "base_key": "base_val",
            "top_key": "top_val",
            "to_merge": {"x": 1, "y": 2},
        })

    def test_local_value_overrides_included(self):
        self.write("world.json", {"name": "Earth", "size": 100})
        result = self.compose("template.json", {
            "world": {"@include": "world.json", "size": 200}
        })
        self.assertEqual(result["world"], {"name": "Earth", "size": 200})

    def test_replace_mode_drops_overlay(self):
        self.write("world.json", {"name": "Earth"})
        result = self.compose("template.json", {
            "world": {"@include": "world.json(replace)", "size": 200}
        })
        self.assertEqual(result["world"], {"name": "Earth"})

    def test_include_cycle_detected(self):
        self.write("a.json", {"x": {"@include": "b.json"}})
        self.write("b.json", {"y": {"@include": "a.json"}})
        with self.assertRaisesRegex(RuntimeError, "Cycle detected"):
            self.compose("template.json", {"@include": "a.json"})

    @requires_jq
    def test_include_with_filter(self):
        self.write("world.json", {"world": {"name": "Earth", "size": 100}, "other": {}})
        result = self.compose("template.json", {
            "world": {"@include": "world.json::.world | .name = \"Mars\""}
        })
        self.assertEqual(result["world"], {"name": "Mars", "size": 100})

    @requires_jq
    def test_include_filter_returning_array(self):
        self.write("data.json", {"items": [{"name": "a"}, {"name": "b"}]})
        result = self.compose("template.json", {
            "names": {"@include": "data.json::.items | map(.name)"}
        })
        self.assertEqual(result["names"], ["a", "b"])


########################################
# @ref (same-file references)
########################################

@requires_jq
class TestRef(TempFilesMixin, unittest.TestCase):

    def test_ref_resolver_directly(self):
        doc = {"materials": ["air", "lava"], "det": {"m": {"@ref": ".materials"}}}
        result = RefResolver(doc, "inline", log=lambda msg: None).resolve(doc)
        self.assertEqual(result["det"]["m"], ["air", "lava"])

    def test_basic_ref(self):
        result = self.compose("template.json", {
            "materials": ["air", "lava", "water"],
            "myDetector": {"material_list": {"@ref": ".materials"}},
        })
        self.assertEqual(result["myDetector"]["material_list"], ["air", "lava", "water"])
        # the referenced source is left untouched
        self.assertEqual(result["materials"], ["air", "lava", "water"])

    def test_ref_with_jq_expressions(self):
        result = self.compose("template.json", {
            "materials": ["air", "lava", "water"],
            "first": {"@ref": ".materials[0]"},
            "count": {"@ref": ".materials | length"},
            "upper": {"@ref": ".materials | map(ascii_upcase)"},
        })
        self.assertEqual(result["first"], "air")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["upper"], ["AIR", "LAVA", "WATER"])

    def test_ref_inside_list(self):
        result = self.compose("template.json", {
            "materials": ["air", "lava"],
            "items": [{"@ref": ".materials[1]"}, "steel"],
        })
        self.assertEqual(result["items"], ["lava", "steel"])

    def test_ref_overlay_merges(self):
        result = self.compose("template.json", {
            "defaults": {"temp": 87, "pressure": 1.0},
            "cond": {"@ref": ".defaults", "temp": 90},
        })
        self.assertEqual(result["cond"], {"temp": 90, "pressure": 1.0})
        self.assertEqual(result["defaults"]["temp"], 87)

    def test_ref_replace_mode(self):
        result = self.compose("template.json", {
            "defaults": {"temp": 87},
            "cond": {"@ref": ".defaults(replace)", "temp": 90},
        })
        self.assertEqual(result["cond"], {"temp": 87})

    def test_chained_refs(self):
        result = self.compose("template.json", {
            "a": [1, 2],
            "b": {"@ref": ".a"},
            "c": {"@ref": ".b"},
        })
        self.assertEqual(result["c"], [1, 2])

    def test_ref_into_included_content(self):
        self.write("geom.json", {"box": {"size": [1, 2, 3]}})
        result = self.compose("template.json", {
            "geom": {"@include": "geom.json::.box"},
            "det": {"size": {"@ref": ".geom.size"}},
        })
        self.assertEqual(result["det"]["size"], [1, 2, 3])

    def test_ref_scope_is_the_included_file(self):
        """A @ref inside an included file resolves against that file, even if filtered."""
        self.write("geom.json", {
            "unit": "cm",
            "box": {"unit": {"@ref": ".unit"}},
        })
        result = self.compose("template.json", {
            "unit": "mm",
            "geom": {"@include": "geom.json::.box"},
        })
        self.assertEqual(result["geom"], {"unit": "cm"})

    def test_ref_in_include_overlay(self):
        self.write("world.json", {"name": "Earth", "size": 100})
        result = self.compose("template.json", {
            "default_size": 300,
            "world": {"@include": "world.json", "size": {"@ref": ".default_size"}},
        })
        self.assertEqual(result["world"], {"name": "Earth", "size": 300})

    def test_ref_missing_path_gives_null(self):
        result = self.compose("template.json", {"x": {"@ref": ".does_not_exist"}})
        self.assertIsNone(result["x"])

    def test_ref_cycle_detected(self):
        with self.assertRaisesRegex(RuntimeError, "Circular @ref"):
            self.compose("template.json", {"a": {"@ref": ".b"}, "b": {"@ref": ".a"}})

    def test_ref_to_own_ancestor_detected(self):
        with self.assertRaisesRegex(RuntimeError, "Circular @ref"):
            self.compose("template.json", {"a": {"b": {"@ref": ".a"}}})

    def test_ref_and_include_in_same_node_rejected(self):
        self.write("x.json", {})
        with self.assertRaisesRegex(RuntimeError, "cannot contain both"):
            self.compose("template.json", {"n": {"@include": "x.json", "@ref": ".a"}})

    def test_invalid_jq_filter_reported(self):
        with self.assertRaisesRegex(RuntimeError, "jq filter failed"):
            self.compose("template.json", {"x": {"@ref": ".a | bad("}})


if __name__ == "__main__":
    unittest.main()
