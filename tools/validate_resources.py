from pathlib import Path
import json
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    schemas = {}
    registry = Registry()
    for path in (ROOT / "schemas").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["$id"] = path.as_uri()
        schemas[path.name] = schema
        registry = registry.with_resource(path.as_uri(), Resource.from_contents(schema))
    for filename, schema_name in (("app/interface.json", "interface.schema.json"), ("app/resource/pipeline/daily.json", "pipeline.schema.json")):
        schema = schemas[schema_name]
        instance = json.loads((ROOT / filename).read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, registry=registry).iter_errors(instance))
        if errors:
            for error in errors:
                print(f"{filename}: {list(error.absolute_path)} {error.message}")
            raise SystemExit(1)
        print(f"PASS {filename}")


if __name__ == "__main__":
    main()
