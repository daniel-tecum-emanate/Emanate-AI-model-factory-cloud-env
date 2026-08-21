"""Every factory/specs/*.yaml validates and matches the reviewed catalog count."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPECS_DIR = HERE.parent / "specs"
sys.path.insert(0, str(SPECS_DIR))

from validate_specs import spec_files, validate_all  # noqa: E402

EXPECTED_SPEC_COUNT = 41  # 33 original + 8 model-training swarm roles (2026-08-04)


def test_every_spec_validates_against_schema():
    errors = validate_all(verbose=False)
    assert errors == {}, f"schema validation failures: {errors}"


def test_spec_file_count_matches_catalog():
    assert len(spec_files()) == EXPECTED_SPEC_COUNT


def test_every_spec_declares_a_status():
    import yaml

    for path in spec_files():
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "status" in data, f"{path.name} missing status"
        assert data["status"] in ("draft", "reviewed")


def test_every_slug_matches_its_filename():
    import yaml

    for path in spec_files():
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["slug"] == path.stem, f"{path.name}: slug {data['slug']!r} != filename"
