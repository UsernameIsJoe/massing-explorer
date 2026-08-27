"""
Validate SiteContext / MassingResponse JSON files without Rhino.

Usage:
    python tools/validate_json.py examples/site-context.example.json
    python tools/validate_json.py examples/massing-response.example.json --type massing
"""

import argparse
import json
import os
import sys

try:
    import jsonschema
except ImportError:
    print("Install dependencies: pip install -r requirements.txt")
    sys.exit(1)


SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "..", "schemas")

SCHEMAS = {
    "site": "site-context.schema.json",
    "massing": "massing-response.schema.json",
}


def validate(path, schema_type="site"):
    with open(path, "r") as f:
        data = json.load(f)

    schema_path = os.path.join(SCHEMA_DIR, SCHEMAS[schema_type])
    with open(schema_path, "r") as f:
        schema = json.load(f)

    jsonschema.validate(instance=data, schema=schema)
    print("OK: {} is valid {}".format(path, schema_type))


def main():
    parser = argparse.ArgumentParser(description="Validate Massing Explorer JSON files")
    parser.add_argument("file", help="JSON file to validate")
    parser.add_argument(
        "--type",
        choices=["site", "massing"],
        default="site",
        help="Schema type (default: site)",
    )
    args = parser.parse_args()
    validate(args.file, args.type)


if __name__ == "__main__":
    main()
