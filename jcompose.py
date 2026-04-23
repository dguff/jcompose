#!/usr/bin/env python3

###############################################################################
# jcompose: A JSON composition tool with support for includes, jq filters, 
#            and schema validation.
# author: Daniele Guffanti <daniele.guffanti_at_mib_dot_infn_dot_it>
# Usage:
#   jcompose.py [options] <template.json>
# Options:
#   -o, --output <file>    Output file (default: stdout)
#   --debug                Enable debug mode
#   --schema <schema.json> Validate output against JSON schema
###############################################################################

import os
import json
import subprocess
import argparse
from pathlib import Path


########################################
# Utilities
########################################

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
        raise RuntimeError(proc.stderr)

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
    Recursively merges two JSON-like structures. 
    Dictionaries are merged by key; lists are concatenated.
    """
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

    return a

########################################
# Include parsing
########################################

def parse_include(s):
    """
    Parses the '@include' string syntax.
    Example: "file.json::.filter[replace]" -> ("file.json", ".filter", "replace")
    """
    mode = None

    if s.endswith("]") and "[" in s:
        idx = s.rfind("[")
        mode = s[idx+1:-1]
        s = s[:idx]

    if "::" in s:
        file, filt = s.split("::", 1)
    else:
        file, filt = s, None

    return file, filt, mode or "merge"

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
                    return Path(root) / fname

        raise FileNotFoundError(f"File not found: {fname}")

########################################
# Composer
########################################

class Composer:
    """
    The core engine responsible for traversing JSON structures and 
    expanding '@include' directives into a single unified document.
    """
    def __init__(self, resolver, debug=False):
        self.resolver = resolver
        self.cache = {}
        self.debug = debug

    def log(self, msg):
        if self.debug:
            print(msg)

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

    def expand(self, data, current_file, stack):
        """
        Recursively processes the JSON tree. When an '@include' is found, 
        it resolves the file, expands its content, applies filters, 
        and merges it into the parent structure while checking for circular dependencies.
        """
        if isinstance(data, dict):
            if "@include" in data:
                inc = data["@include"]
                file, filt, mode = parse_include(inc)

                resolved = self.resolver.resolve(file, Path(current_file).parent)

                if str(resolved) in stack:
                    raise RuntimeError(
                        "Cycle detected:\n" + " -> ".join(stack + [str(resolved)])
                    )

                self.log(f"[include] {file} (filter={filt}, mode={mode})")

                base = self.load_json(resolved, stack + [str(resolved)])
                base = self.expand(base, resolved, stack + [str(resolved)])

                # apply jq filter
                base = run_jq_filter(base, filt if filt else ".")

                overlay = {k: v for k, v in data.items() if k != "@include"}

                if mode == "replace":
                    return base

                return deep_merge(base, {
                    k: self.expand(v, current_file, stack)
                    for k, v in overlay.items()
                })

            # normal dict
            return {
                k: self.expand(v, current_file, stack)
                for k, v in data.items()
            }

        elif isinstance(data, list):
            return [self.expand(x, current_file, stack) for x in data]

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

    args = parser.parse_args()

    search_paths = build_search_paths(args.path)
    # print seach path: 
    print(f"Search paths: {search_paths}")

    resolver = Resolver(search_paths)
    composer = Composer(resolver, debug=args.debug)

    template_path = Path(args.template).resolve()

    with open(template_path) as f:
        data = json.load(f)

    result = composer.expand(data, template_path, [str(template_path)])

    if args.schema:
        validate_schema(result, args.schema)

    output = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
    else:
        print(output)

if __name__ == "__main__":
    main()
