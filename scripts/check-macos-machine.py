#!/usr/bin/env python3
"""Validate the tracked candidate mount policy, not private deployment inputs."""
import json
from pathlib import Path
import sys


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def main():
    try:
        import jsonschema
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / 'scripts/macos-machine.schema.json').read_text())
        value = json.loads((root / 'config/macos/machine.json').read_text(), object_pairs_hook=unique)
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(value)
    except ImportError:
        print('macos candidate schema: FAIL (python3 jsonschema is required)', file=sys.stderr)
        return 1
    except Exception:
        print('macos candidate schema: FAIL (candidate or schema refused)', file=sys.stderr)
        return 1
    print('macos candidate schema: pass (not deployment or runtime proof)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
