# :building_construction: jcompose :bricks:
`jcompose` is a lightweight Python utility designed to make JSON configuration files modular and reusable. Instead of maintaining giant, monolithic JSON files, `jcompose` allows you to split them into smaller pieces, apply `jq` filters on the fly, and validate the final result against a schema.

##  Features

- **Modular Includes:** Use the `@include` directive to pull in other JSON files.

- **Deep Merging:** Automatically merges nested dictionaries and concatenates lists.

- **On-the-fly Filtering:** Leverage `jq` syntax directly within your include statements.

- **Smart Path Resolution:** Finds files using the `JCONF_PATH` environment variable.

- **Schema Validation:** Ensures the final composed JSON meets your jsonschema requirements.

## Requirements
- Python 3.6+

- `jq`: The script calls the `jq` binary for filtering.

    - Ubuntu/Debian: `sudo apt install jq`
    - macOS: `brew install jq`

- `jsonschema`: Required only if you use the `--schema` flag.

```bash
pip install jsonschema
```

## Installation
1. Local Clone

```bash
git clone git@github.com:dguff/jcompose.git
cd jcompose
chmod +x jcompose.py
```

2. System-wide Installation (Recommended)
To run `jcompose` from any directory, you have two main options:

Option A: Create a symbolic link (Cleanest)

```￼￼
# Link the script to a directory already in your PATH
sudo ln -s "$(pwd)/jcompose.py" /usr/local/bin/jcompose
```

Option B: Add the directory to your `PATH`
Add this line to your `~/.bashrc`, `~/.zshrc`, or `~/.profile`:

```Bash
export PATH="$PATH:/path/to/your/jcompose-folder"
```

## How it Finds Files (`JCONF_PATH`)

How `jcompose` locates an included file depends on how its name is written:

- **Explicit relative paths** (starting with `./` or `../`, e.g. `"@include": "./params.json"`) are resolved relative to the directory of the file containing the `@include`, and nowhere else.

- **Plain names** (e.g. `"@include": "params.json"`) are searched recursively (including subdirectories) in the following order, and the first match wins:

  1. Inside any directory listed in the `--path` CLI argument.

  2. Inside any directory listed in the `JCONF_PATH` environment variable (colon-separated).

  3. The current working directory.

  Plain names are **not** looked up relative to the including file: use `./` for that.

Example: `export JCONF_PATH="/home/user/assets:/opt/project/config"`

## Examples
### Basic include
You can include a JSON file into your template:

`template.json`
```json
{
  "world": {
    "@include": "world.json"
  }
}
```

`world.json`
```json
{
  "name": "Earth",
  "size": 100
}
```

output:
```json
{
  "world": {
    "name": "Earth",
    "size": 100
  }
}
```
---
### Include with jq filter

You can extract only part of a JSON file using a `jq` filter following the `::` separator. This example produces the same output as above.

`template.json`
```json
{
  "world": {
    "@include": "world.json::.world"
  }
}
```

`world.json`
```json
{
  "world": {
    "name": "Earth",
    "size": 100
  },
  "other": {}
}
```
---
### Inline jq transformations
Filters can include full `jq` expressions, allowing you to modify data during inclusion:

`template.json`
```json
{
  "world": {
    "@include": "world.json::.world | .name = \"Mars\""
  }
}
```
Output:
```json
{
  "world": {
    "name": "Mars",
    "size": 100
  }
}
```
---
### Merging objects
Included objects are merged with local values:
`template.json`
```json
{
  "world": {
    "@include": "world.json::.world",
    "size": 200
  }
}
```
Output:
```json
{
  "world": {
    "name": "Earth",
    "size": 200
  }
}
```
---
### Arrays
`jq` filters can return arrays, which are fully supported. Furthermore, specific elements can be modfied by applying online `jq` expressions:
`data.json`
```json
{
  "items": [
    { "name": "a" },
    { "name": "b" }
  ]
}
```
`template.json`
```json
{
  "list": {
    "@include": "data.json::.items | .[1].name = \"c\"" 
  }
}
```
Output:
```json
{
  "list": [
    { "name": "a" },
    { "name": "c" }
  ]
}
```
---
### Same-file references (`@ref`)
Use `@ref` with a `jq` filter to reuse values from the **same file**. The filter is evaluated against the root of the file where the `@ref` is written, after that file's `@include`s have been expanded, so you can also reference included content.

`template.json`
```json
{
  "materials": ["air", "lava", "water"],
  "defaults": { "temp": 87, "pressure": 1.0 },
  "myDetector": {
    "material_list": { "@ref": ".materials" },
    "n_materials":   { "@ref": ".materials | length" },
    "conditions":    { "@ref": ".defaults", "temp": 90 }
  }
}
```
Output (`myDetector` only):
```json
{
  "material_list": ["air", "lava", "water"],
  "n_materials": 3,
  "conditions": { "temp": 90, "pressure": 1.0 }
}
```
Local keys override the referenced values, as with `@include`.

Notes:
- References can point to values that are themselves `@ref`s; circular references are reported as errors.
- Inside an included file, `@ref` refers to *that* file's root, not to the file including it.
- As with `@include`, a trailing `(replace)` discards the local keys: `{"@ref": ".defaults(replace)"}`.
- A filter that computes over a region containing unresolved `@ref`s (e.g. `.list | length` where `.list` holds `@ref` nodes) sees the placeholders, not the resolved values.
