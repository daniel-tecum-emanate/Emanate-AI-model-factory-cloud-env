#!/usr/bin/env python3
"""validate_specs.py — schema-validate every factory/specs/*.yaml against schema.json.

T10 accept criterion (PRContext.md): exits 0 iff every spec file validates. Also used by
factory/tests/test_specs_schema.py so the same check runs under pytest.
"""

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

HERE = Path(__file__).resolve().parent


def load_schema():
    with open(HERE / "schema.json") as f:
        return json.load(f)


def spec_files():
    return sorted(p for p in HERE.glob("*.yaml"))


def validate_all(verbose=True):
    schema = load_schema()
    validator = Draft7Validator(schema)
    errors = {}
    for path in spec_files():
        with open(path) as f:
            data = yaml.safe_load(f)
        file_errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
        if file_errors:
            errors[path.name] = [e.message for e in file_errors]
    if verbose:
        n = len(spec_files())
        if errors:
            print(f"FAILED: {len(errors)}/{n} spec files failed schema validation")
            for name, msgs in errors.items():
                print(f"  {name}:")
                for m in msgs:
                    print(f"    - {m}")
        else:
            print(f"OK: all {n} spec files validate against schema.json")
    return errors


if __name__ == "__main__":
    errs = validate_all()
    sys.exit(1 if errs else 0)
