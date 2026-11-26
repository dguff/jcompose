#!/usr/bin/env python3

###############################################################################
# jcompose: A JSON composition tool with support for includes, same-file
#           references, jq filters, and schema validation.
# author: Daniele Guffanti <daniele.guffanti_at_mib_dot_infn_dot_it>
# Usage:
#   jcompose.py [options] <template.json>
# Options:
#   -o, --output <file>    Output file (default: stdout)
#   --path <dirs>          Additional search paths (colon-separated)
#   --debug                Enable debug mode (messages go to stderr)
#   --schema <schema.json> Validate output against JSON schema
#
# Directives:
#   {"@include": "file.json[::jq_filter][(merge|replace)]", ...overlay}
#       Insert (part of) another file.
#   {"@ref": "jq_filter[(merge|replace)]", ...overlay}
#       Insert (part of) the *same* file. The filter is evaluated against the
#       root of the file in which the @ref appears, after that file's
#       @include directives have been expanded.
###############################################################################

import os
import sys
import copy
import json
import subprocess
import argparse
from pathlib import Path

INCLUDE_KEY = "@include"
REF_KEY = "@ref"
MODES = ("merge", "replace")
__version__ = "0.2.0"

########################################
# Utilities
########################################

def eprint(*args):
    print(*args, file=sys.stderr)


def run_jq_filter(data, filt):
    """
    Executes an external 'jq' process to filter JSON data.
    Returns the filtered JSON object or a list of objects if the output is a stream.
    """
    if not filt:
        return data

    proc = subprocess.run(
        ["jq", "-c", filt],
        input=json.dumps(data),
        text=True,
        capture_output=True
    )

    if proc.returncode != 0:
        raise RuntimeError(f"jq filter failed: {filt}\n{proc.stderr}")

    stdout = proc.stdout.strip()

    if not stdout:
        return None

    # Try parsing as a single JSON value
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass

    # Fallback: stream of JSON values
    return [json.loads(line) for line in stdout.splitlines()]


def deep_merge(a, b):
    """
    Recursively merges two JSON-like structures, with b taking precedence.
    Dictionaries are merged by key; lists are concatenated; for any other
    combination the value from b wins.
    """
    # An unresolved @ref placeholder must replace, not be merged into, the base
    if isinstance(b, dict) and REF_KEY in b:
        return b

    if isinstance(a, dict) and isinstance(b, dict):
        result = dict(a)
        for k, v in b.items():
            if k in result:
                result[k] = deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    if isinstance(a, list) and isinstance(b, list):
        return a + b

    return b


def contains_key(data, key):
    """True if any dict in the tree has the given key."""
    if isinstance(data, dict):
        return key in data or any(contains_key(v, key) for v in data.values())
    if isinstance(data, list):
        return any(contains_key(x, key) for x in data)
    return False

########################################
# Directive parsing
########################################

def split_mode(s):
    """
    Strips an optional trailing '(merge)' or '(replace)' from a directive.
    Only these exact suffixes are treated as modes, so jq filters ending in
    a parenthesis (e.g. 'map(.name)') are left intact.
    """
    for m in MODES:
        suffix = f"({m})"
        if s.endswith(suffix):
            return s[:-len(suffix)].rstrip(), m
    return s, "merge"


def parse_include(s):
    """
    Parses the '@include' string syntax.
    Example: "file.json::.filter(replace)" -> ("file.json", ".filter", "replace")
    """
    s, mode = split_mode(s)

    if "::" in s:
        file, filt = s.split("::", 1)
    else:
        file, filt = s, None

    return file, filt, mode


def parse_ref(s):
    """
    Parses the '@ref' string syntax.
    Example: ".materials(replace)" -> (".materials", "replace")
    """
    return split_mode(s)

########################################
# File resolution
########################################

def build_search_paths(cli_path=None):
    """
    Constructs an ordered list of unique directories to search for inclusions,
    prioritizing the --path CLI argument, then the JCONF_PATH environment variable.
    """
    paths = []

    # 1. CLI override
    if cli_path:
        paths.extend(cli_path.split(":"))

    # 2. Environment variable JCONF_PATH
    env_path = os.environ.get("JCONF_PATH")
    if env_path:
        paths.extend(env_path.split(":"))

    # 3. Always check current directory
    paths.append(".")

    # De-duplicate while preserving order
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


class Resolver:
    """
    Handles file location logic. Checks relative paths first, then performs
    a recursive search through all directories defined in the search paths.
    """
    def __init__(self, search_paths):
        self.search_paths = search_paths

    def resolve(self, fname, current_dir):
        """Finds the absolute Path of a file or raises FileNotFoundError."""
        # relative path
        if fname.startswith("./") or fname.startswith("../"):
            path = Path(current_dir) / fname
            if path.exists():
                return path.resolve()
            raise FileNotFoundError(f"Relative file not found: {fname}")

        # search paths
        for base in self.search_paths:
            for root, _, files in os.walk(base):
                if fname in files:
                    return (Path(root) / fname).resolve()

        raise FileNotFoundError(f"File not found: {fname}")

########################################
# Same-file references
########################################

class RefResolver:
    """
    Replaces '@ref' nodes in a document with values extracted from the
    document's own root via a jq filter.

    A referenced value may itself contain '@ref' nodes; these are resolved
    recursively, and circular references are reported as errors.
    """
    def __init__(self, root, file, log):
        self.root = root
        self.file = str(file)
        self.log = log
        self.cache = {}
        self.stack = []

    def resolve(self, data):
        if isinstance(data, dict):
            if REF_KEY in data:
                return self._resolve_ref(data)
            return {k: self.resolve(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.resolve(x) for x in data]
        return data

    def _resolve_ref(self, node):
        if INCLUDE_KEY in node:
            raise RuntimeError(
                f"In {self.file}: a node cannot contain both "
                f"'{INCLUDE_KEY}' and '{REF_KEY}': {json.dumps(node)}"
            )

        expr, mode = parse_ref(node[REF_KEY])
        if not expr:
            raise RuntimeError(f"In {self.file}: empty '{REF_KEY}' filter")

        if expr in self.stack:
            raise RuntimeError(
                f"Circular {REF_KEY} in {self.file}:\n"
                + " -> ".join(self.stack + [expr])
            )

        if expr in self.cache:
            value = copy.deepcopy(self.cache[expr])
        else:
            self.log(f"[ref] {expr} (in {self.file})")
            self.stack.append(expr)
            try:
                value = self.resolve(run_jq_filter(self.root, expr))
            finally:
                self.stack.pop()
            self.cache[expr] = copy.deepcopy(value)

        overlay = {k: self.resolve(v) for k, v in node.items() if k != REF_KEY}

        if mode == "replace" or not overlay:
            return value

        return deep_merge(value, overlay)

########################################
# Composer
########################################

class Composer:
    """
    The core engine responsible for traversing JSON structures and
    expanding '@include' and '@ref' directives into a single unified document.

    Each file is processed in two passes:
      1. '@include' directives are expanded (recursively, each included
         file going through both passes on its own);
      2. '@ref' directives are resolved against the file's include-expanded root.
    """
    def __init__(self, resolver, debug=False):
        self.resolver = resolver
        self.cache = {}
        self.debug = debug

    def log(self, msg):
        if self.debug:
            eprint(msg)

    def load_json(self, path, stack=None):
        """Loads JSON from disk with an internal cache to prevent redundant I/O."""
        path = str(path)
        if path in self.cache:
            self.log(f"[cache] {path}")
            return self.cache[path]

        self.log(f"[load] {path}")
        try:
            with open(path) as f:
                data = json.load(f)

        except json.JSONDecodeError as e:
            chain = " -> ".join(stack or [])
            raise RuntimeError(
                f"JSON parse error in file: {path}\n"
                f"Line {e.lineno}, column {e.colno}: {e.msg}\n"
                f"Include stack:\n{chain}"
            ) from e

        except Exception as e:
            chain = " -> ".join(stack or [])
            raise RuntimeError(
                f"Error loading file: {path}\n"
                f"Include stack:\n{chain}\n"
                f"{str(e)}"
            ) from e

        self.cache[path] = data
        return data

    def expand_file(self, path, stack):
        """Loads a file and fully expands it (includes, then refs)."""
        data = self.load_json(path, stack)
        data = self.expand_includes(data, path, stack)
        if contains_key(data, REF_KEY):
            data = RefResolver(data, path, self.log).resolve(data)
        return data

    def expand_includes(self, data, current_file, stack):
        """
        Recursively processes the JSON tree. When an '@include' is found,
        it resolves the file, expands its content, applies filters,
        and merges it into the parent structure while checking for circular
        dependencies. '@ref' nodes are left in place for the second pass.
        """
        if isinstance(data, dict):
            if INCLUDE_KEY in data:
                if REF_KEY in data:
                    raise RuntimeError(
                        f"In {current_file}: a node cannot contain both "
                        f"'{INCLUDE_KEY}' and '{REF_KEY}'"
                    )

                file, filt, mode = parse_include(data[INCLUDE_KEY])

                resolved = self.resolver.resolve(file, Path(current_file).parent)

                if str(resolved) in stack:
                    raise RuntimeError(
                        "Cycle detected:\n" + " -> ".join(stack + [str(resolved)])
                    )

                self.log(f"[include] {file} (filter={filt}, mode={mode})")

                base = self.expand_file(resolved, stack + [str(resolved)])

                # apply jq filter
                base = run_jq_filter(base, filt)

                overlay = {k: v for k, v in data.items() if k != INCLUDE_KEY}

                if mode == "replace" or not overlay:
                    return base

                return deep_merge(base, {
                    k: self.expand_includes(v, current_file, stack)
                    for k, v in overlay.items()
                })

            # normal dict (including unresolved @ref nodes)
            return {
                k: self.expand_includes(v, current_file, stack)
                for k, v in data.items()
            }

        elif isinstance(data, list):
            return [self.expand_includes(x, current_file, stack) for x in data]

        else:
            return data

########################################
# Schema validation
########################################

def validate_schema(data, schema_path):
    try:
        import jsonschema
    except ImportError:
        raise RuntimeError("jsonschema package required for validation")

    with open(schema_path) as f:
        schema = json.load(f)

    jsonschema.validate(instance=data, schema=schema)

########################################
# CLI
########################################

def main():
    parser = argparse.ArgumentParser(description="JSON composition tool")
    parser.add_argument("template", help="Input JSON template")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument(
        "--path",
        help="Additional search paths (colon-separated)"
        )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--schema", help="Validate against JSON schema")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    search_paths = build_search_paths(args.path)

    resolver = Resolver(search_paths)
    composer = Composer(resolver, debug=args.debug)
    composer.log(f"Search paths: {search_paths}")

    template_path = Path(args.template).resolve()

    try:
        result = composer.expand_file(template_path, [str(template_path)])
    except (RuntimeError, FileNotFoundError) as e:
        if args.debug:
            raise
        eprint(f"jcompose: error: {e}")
        sys.exit(1)

    if args.schema:
        validate_schema(result, args.schema)

    output = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
    else:
        print(output)

if __name__ == "__main__":
    main()
