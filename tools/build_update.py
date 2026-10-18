"""Build the verified script/resource update asset from a complete portable package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.updater import ASSET, Updater, managed_name, read_json, runtime_id, sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    package = args.package_dir.resolve()
    inventory = read_json(package / 'PACKAGE.json')
    worker = Updater(package)
    if inventory['version'] != worker.current:
        raise RuntimeError('Package version mismatch')
    for name, digest in inventory['files'].items():
        target = package / name
        if not target.resolve().is_relative_to(package) or target.is_symlink() or sha(target) != digest:
            raise RuntimeError('Incomplete or changed portable package: ' + name)
    files = {name: digest for name, digest in inventory['files'].items() if managed_name(name)}
    files['PACKAGE.json'] = sha(package / 'PACKAGE.json')
    manifest = {'schema': 1, 'project': 'CangwuMaa', 'platform': 'windows-x64', 'version': worker.current,
                'runtime_id': runtime_id(package), 'files': files}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / ASSET
    if output.exists():
        raise RuntimeError('Choose an output directory without an existing update archive')
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files): archive.write(package / name, name)
        archive.writestr('UPDATE.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with tempfile.TemporaryDirectory(prefix='cangwu-update-build-') as temporary:
        worker.unpack(output, Path(temporary) / 'verified', worker.current)
    print(json.dumps({'archive': str(output), 'bytes': output.stat().st_size, 'sha256': sha(output)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
