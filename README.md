# jcompose
`jcompose` is a lightweight Python utility designed to make JSON configuration files modular and reusable. Instead of maintaining giant, monolithic JSON files, `jcompose` allows you to split them into smaller pieces, apply jq filters on the fly, and validate the final result against a schema.

##  Features

- **Modular Includes:** Use the @include directive to pull in other JSON files.

- **Deep Merging:** Automatically merges nested dictionaries and concatenates lists.

- **On-the-fly Filtering:** Leverage jq syntax directly within your include statements.

- **Smart Path Resolution:** Finds files using the JCONF_PATH environment variable.

- **Schema Validation:** Ensures the final composed JSON meets your jsonschema requirements.

## Requirements
- Python 3.6+

- `jq`: The script calls the `jq` binary for filtering.

    - Ubuntu/Debian: sudo apt install jq
    - macOS: brew install jq

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

When you include a file (e.g., `"@include": "params.json"`), `jcompose` looks in the following order:

1. Relative to the current file.

2. Inside any directory listed in the `--path` CLI argument.

3. Inside any directory listed in the `JCONF_PATH` environment variable (colon-separated).

4. The current working directory.

Example: `export JCONF_PATH="/home/user/assets:/opt/project/config"`

## Examples

