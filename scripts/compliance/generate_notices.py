#!/usr/bin/env python3
"""Build a source dependency inventory, original license archive and SBOM.

Run from any directory after a frozen pnpm install. Python package metadata
is read without importing application modules. --fetch obtains missing exact
version metadata/artifact license files from npm/PyPI; it never installs code.
--check is offline and checks pinned inputs, archived bytes and generated files.
Requires the already constrained PyYAML and packaging packages.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import importlib.metadata as metadata
import io
import json
import re
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict, deque
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "licenses"
CACHE_PATH = OUT / "registry-cache.json"
INVENTORY_PATH = OUT / "inventory.json"
TEXT_DIR = OUT / "texts"
LICENSE_NAME = re.compile(r"^(?:licen[sc]e|copying|notice|copyright|authors|ofl)(?:[._-].*)?$", re.I)
MANIFESTS = [ROOT / "package.json", *sorted((ROOT / "apps").glob("*/package.json")),
             *sorted((ROOT / "packages").glob("*/package.json"))]
REQUIREMENTS = [*sorted((ROOT / "services/api").glob("requirements*.txt")),
                ROOT / "services/voice/requirements.txt"]
EXTERNAL_PATH = OUT / "external-components.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def source_inputs():
    return [*MANIFESTS, *REQUIREMENTS, ROOT / "services/api/constraints.txt",
            ROOT / "pnpm-lock.yaml", ROOT / "pnpm-workspace.yaml",
            ROOT / "deploy/local/docker-compose.yml",
            ROOT / "deploy/self-hosted/install_voice.sh", EXTERNAL_PATH]


def original(data: bytes, origin: str):
    if not data or b"\0" in data:
        return None
    digest = sha256(data)
    target = TEXT_DIR / f"{digest}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return {"path": target.relative_to(ROOT).as_posix(), "sha256": digest,
            "origin": origin, "bytes": len(data)}


def license_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(LICENSE_NAME.match(parts[-1])) or any(
        p.lower() in {"licenses", "licences"} for p in parts[:-1])


def license_from_metadata(info):
    expression = info.get("license_expression") or info.get("License-Expression")
    raw = info.get("license") or info.get("License")
    if isinstance(raw, dict):
        raw = raw.get("type")
    if isinstance(raw, list):
        raw = " OR ".join(x.get("type", "") for x in raw if isinstance(x, dict))
    if raw and (len(str(raw)) > 180 or "\n" in str(raw)):
        raw = None  # Entire license texts are archived separately, not an SPDX expression.
    if not expression and raw and not str(raw).startswith("SEE LICENSE"):
        expression = str(raw)
    if not expression:
        classifiers = info.get("classifiers", [])
        mapping = {"MIT License": "MIT", "Apache Software License": "Apache-2.0",
                   "ISC License (ISCL)": "ISC", "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
                   "BSD License": "BSD-3-Clause", "PSF License": "PSF-2.0"}
        values = {mapping[x.split(" :: ")[-1]] for x in classifiers
                  if x.startswith("License ::") and x.split(" :: ")[-1] in mapping}
        if len(values) == 1:
            expression = values.pop()
    return {"license_declared": raw, "license_expression": expression or None}


def package_identity(key: str):
    return key.split("(", 1)[0]


def npm_purl(name: str, version: str):
    return f"pkg:npm/{urllib.parse.quote(name, safe='/')}@{urllib.parse.quote(version, safe='')}"


def installed_npm():
    result = {}
    store = ROOT / "node_modules/.pnpm"
    if not store.is_dir():
        raise SystemExit("Frozen pnpm install required before inventory generation.")
    for item in sorted(store.iterdir()):
        directory = item / "node_modules"
        paths = [*directory.glob("*/package.json"), *directory.glob("@*/*/package.json")]
        for path in paths:
            if path.parent.is_symlink():
                continue
            try:
                info = json.loads(path.read_text())
                result[f"{info['name']}@{info['version']}"] = (path.parent, info)
            except (ValueError, KeyError, OSError):
                continue
    return result


def collect_npm(lock, cache):
    installed = installed_npm()
    scopes = defaultdict(set)
    direct = defaultdict(set)
    edges = defaultdict(set)
    snapshots = lock.get("snapshots", {})
    for importer, config in lock["importers"].items():
        for kind in ("dependencies", "devDependencies", "optionalDependencies"):
            for name, ref in config.get(kind, {}).items():
                version = ref["version"]
                if version.startswith(("link:", "workspace:")):
                    continue
                key = f"{name}@{version}"
                direct[package_identity(key)].add(f"{importer}:{kind}")
                pending = deque([key])
                visited = set()
                while pending:
                    current = pending.popleft()
                    if current in visited:
                        continue
                    visited.add(current)
                    base = package_identity(current)
                    scopes[base].add(f"{importer}:{kind}")
                    snapshot = snapshots.get(current, {})
                    for field in ("dependencies", "optionalDependencies"):
                        for child, resolved in snapshot.get(field, {}).items():
                            if resolved.startswith("link:"):
                                continue
                            child_key = f"{child}@{resolved}"
                            edges[base].add(package_identity(child_key))
                            pending.append(child_key)
    components = []
    for key, detail in sorted(lock["packages"].items()):
        name, _, version = key.rpartition("@")
        record = {"ecosystem": "npm", "name": name, "version": version,
                  "purl": npm_purl(name, version),
                  "scopes": sorted(scopes[key]), "direct_in": sorted(direct[key]),
                  "dependencies": sorted(edges[key]),
                  "optional": bool(detail.get("optional")),
                  "integrity": detail.get("resolution", {}).get("integrity"),
                  "license_files": [], "evidence": "lock-only",
                  "license_expression": None, "license_declared": None}
        if key in installed:
            directory, info = installed[key]
            record.update(license_from_metadata(info))
            record["evidence"] = "installed-package"
            record["homepage"] = info.get("homepage")
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.is_symlink() and license_path(
                        path.relative_to(directory).as_posix()):
                    if "node_modules" in path.relative_to(directory).parts:
                        continue
                    copied = original(path.read_bytes(),
                                      f"npm:{key}/{path.relative_to(directory).as_posix()}")
                    if copied:
                        record["license_files"].append(copied)
        cached = cache.get(f"npm:{key}")
        if cached:
            for field in ("homepage", "license_expression", "license_declared"):
                if not record.get(field):
                    record[field] = cached.get(field)
            record["license_files"].extend(cached.get("license_files", []))
            record["registry_source"] = cached["registry_source"]
            if record["evidence"] == "lock-only":
                record["evidence"] = "registry-artifact;not-installed"
        record["license_files"] = list({x["origin"]: x for x in record["license_files"]}.values())
        components.append(record)
    return components


def requirement_lines(path):
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            yield Requirement(line)


def python_purl(name, version):
    return f"pkg:pypi/{canonicalize_name(name)}@{urllib.parse.quote(version, safe='')}"


def collect_python(cache):
    constraints = {canonicalize_name(r.name): str(next(iter(r.specifier)).version)
                   for r in requirement_lines(ROOT / "services/api/constraints.txt")}
    observed = {canonicalize_name(d.metadata["Name"]): d for d in metadata.distributions()
                if d.metadata.get("Name")}
    wanted = defaultdict(set)
    direct = defaultdict(set)
    root_extras = {}
    for path in REQUIREMENTS:
        profile = path.relative_to(ROOT).as_posix()
        for req in requirement_lines(path):
            name = canonicalize_name(req.name)
            exact = [s.version for s in req.specifier if s.operator == "=="]
            version = exact[0] if exact else constraints.get(name)
            if not version:
                raise SystemExit(f"Unpinned Python requirement: {req} ({profile})")
            wanted[(name, version)].add(profile)
            direct[(name, version)].add(profile)
            root_extras[(name, version, profile)] = frozenset(req.extras)
    for name, version in constraints.items():
        wanted[(name, version)].add("services/api/constraints.txt:constraint-only")
    # Actual installed transitive closure, evaluated for this interpreter.
    # Voice and other uninstalled profiles remain declarations, not fake resolutions.
    queue = deque((key, scope, root_extras.get((*key, scope), frozenset()))
                  for key, scopes in direct.items() for scope in scopes)
    seen = set()
    dependency_edges = defaultdict(set)
    while queue:
        (name, version), scope, extras = queue.popleft()
        marker = (name, version, scope, extras)
        if marker in seen:
            continue
        seen.add(marker)
        dist = observed.get(name)
        if dist is None or dist.version != version:
            continue
        parent = (name, version)
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker and not any(req.marker.evaluate({"extra": extra})
                                      for extra in {"", *extras}):
                continue
            child = observed.get(canonicalize_name(req.name))
            if child is None or child.version not in req.specifier:
                continue
            key = (canonicalize_name(req.name), child.version)
            wanted[key].add(scope + ":observed-transitive")
            dependency_edges[parent].add(python_purl(*key))
            queue.append((key, scope, frozenset(req.extras)))
    records = []
    for (name, version), scopes in sorted(wanted.items()):
        record = {"ecosystem": "pypi", "name": name, "version": version,
                  "purl": python_purl(name, version), "scopes": sorted(scopes),
                  "direct_in": sorted(direct[(name, version)]),
                  "dependencies": sorted(dependency_edges[(name, version)]),
                  "license_files": [], "evidence": "declared;not-installed",
                  "license_expression": None, "license_declared": None}
        dist = observed.get(name)
        if dist and dist.version == version:
            info = dict(dist.metadata.items())
            info["classifiers"] = dist.metadata.get_all("Classifier") or []
            record.update(license_from_metadata(info))
            record["evidence"] = "installed-distribution"
            record["homepage"] = dist.metadata.get("Home-page")
            for path in dist.files or []:
                if license_path(str(path)):
                    real = dist.locate_file(path)
                    if real.is_file():
                        copied = original(real.read_bytes(), f"pypi:{name}@{version}/{path}")
                        if copied:
                            record["license_files"].append(copied)
        cached = cache.get(f"pypi:{name}@{version}")
        if cached:
            for field in ("homepage", "license_expression", "license_declared"):
                if not record.get(field):
                    record[field] = cached.get(field)
            record["license_files"].extend(cached.get("license_files", []))
            record["registry_source"] = cached["registry_source"]
            if record["evidence"] == "declared;not-installed":
                record["evidence"] = "registry-artifact;not-installed"
        record["license_files"] = list({x["origin"]: x for x in record["license_files"]}.values())
        records.append(record)
    return records


def download(url, max_bytes=65_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": "Next-Tutor-License-Inventory/1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if int(response.headers.get("Content-Length", "0")) > max_bytes:
            raise ValueError("artifact exceeds extraction limit; inspect release artifact separately")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("artifact exceeds extraction limit")
        return data


def extract_licenses(data, url, digest):
    if digest and sha256(data) != digest:
        raise ValueError("registry artifact sha256 mismatch")
    records = []
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in sorted(archive.namelist()):
                if not name.endswith("/") and license_path(name):
                    copied = original(archive.read(name), f"{url}#{name}")
                    if copied:
                        records.append(copied)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in sorted(archive.getmembers(), key=lambda x: x.name):
                if member.isfile() and member.size < 5_000_000 and license_path(member.name):
                    file = archive.extractfile(member)
                    if file is not None:
                        copied = original(file.read(), f"{url}#{member.name}")
                        if copied:
                            records.append(copied)
    return records


def registry_record(record):
    name, version, ecosystem = record["name"], record["version"], record["ecosystem"]
    if ecosystem == "npm":
        url = f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='')}/{version}"
        info = json.loads(download(url))
        result = license_from_metadata(info)
        artifact_url = info["dist"]["tarball"]
        data = download(artifact_url)
        integrity = info["dist"].get("integrity")
        if integrity:
            algorithm, value = integrity.split("-", 1)
            if base64.b64encode(hashlib.new(algorithm, data).digest()).decode() != value:
                raise ValueError("npm tarball integrity mismatch")
        result["license_files"] = extract_licenses(data, artifact_url, None)
        result["homepage"] = info.get("homepage")
    else:
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        response = json.loads(download(url))
        info = response["info"]
        result = license_from_metadata(info)
        result["homepage"] = info.get("project_urls", {}).get("Homepage") if info.get("project_urls") else info.get("home_page")
        artifacts = sorted(response["urls"], key=lambda x: (
            0 if x["filename"].endswith("-py3-none-any.whl") else
            1 if x["packagetype"] == "sdist" else 2, x["size"]))
        result["license_files"] = []
        for artifact in artifacts[:3]:
            if artifact["size"] > 65_000_000:
                continue
            try:
                result["license_files"] = extract_licenses(
                    download(artifact["url"]), artifact["url"], artifact["digests"]["sha256"])
            except (ValueError, OSError, urllib.error.URLError):
                continue
            if result["license_files"]:
                break
    result["registry_source"] = url
    return f"{ecosystem}:{name}@{version}", result


def refresh_missing(records, cache):
    missing = [r for r in records if
               (not r["license_files"] or not r["license_expression"]) and
               f"{r['ecosystem']}:{r['name']}@{r['version']}" not in cache]
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(registry_record, r): r for r in missing}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            record = futures[future]
            try:
                key, value = future.result()
                cache[key] = value
            except Exception as exc:
                errors.append({"package": record["purl"], "error": type(exc).__name__})
            if index % 25 == 0 or index == len(missing):
                CACHE_PATH.write_bytes(json_bytes(cache))
                print(f"Registry license extraction: {index}/{len(missing)}", flush=True)
    CACHE_PATH.write_bytes(json_bytes(cache))
    return errors


def bom(inventory):
    components = []
    refs = {r["name"] + "@" + r["version"]: r["purl"]
            for r in inventory["components"] if r["ecosystem"] == "npm"}
    deps = []
    for record in inventory["components"]:
        item = {"type": "library", "name": record["name"], "version": record["version"],
                "bom-ref": record["purl"], "purl": record["purl"],
                "properties": [{"name": "next-tutor:evidence", "value": record["evidence"]},
                               {"name": "next-tutor:scopes", "value": ",".join(record["scopes"])},
                               {"name": "next-tutor:license-files", "value": ",".join(
                                   sorted({x["path"] for x in record["license_files"]}))}]}
        if record["license_expression"]:
            # Preserve package declarations without pretending every publisher
            # used a valid SPDX expression; CycloneDX license.name allows this.
            item["licenses"] = [{"license": {"name": record["license_expression"]}}]
        if record.get("homepage"):
            item["externalReferences"] = [{"type": "website", "url": record["homepage"]}]
        components.append(item)
        children = [refs.get(d, d) for d in record.get("dependencies", [])]
        deps.append({"ref": record["purl"], "dependsOn": sorted(set(children))})
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {"component": {"type": "application", "name": "Next-Tutor-Agent source dependency inventory"},
                         "properties": [{"name": "next-tutor:boundary", "value": inventory["boundary"]}]},
            "components": components, "dependencies": deps}


def render_table(inventory):
    lines = ["# Resolved dependency license index", "", "Generated by `scripts/compliance/generate_notices.py`.",
             "This source inventory includes development and optional declarations; evidence labels identify actual installed packages.",
             "It is not a binary or container attestation. Original bytes are retained under `texts/`.", "",
             "| Package | Version | Declared license | Evidence | Original texts |", "|---|---|---|---|---|"]
    for record in inventory["components"]:
        links = sorted({f"[{x['sha256'][:10]}]({Path(x['path']).relative_to('licenses').as_posix()})" for x in record["license_files"]})
        label = record["license_expression"] or "Unresolved declaration; inspect original text"
        label = str(label).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{record['ecosystem']}:{record['name']}` | {record['version']} | {label} | {record['evidence']} | {' · '.join(links) or 'Pending artifact evidence'} |")
    return ("\n".join(lines) + "\n").encode()


def products(inventory):
    return {OUT / "sbom.cdx.json": json_bytes(bom(inventory)),
            OUT / "PACKAGE-NOTICES.md": render_table(inventory)}


def check():
    inventory = load_json(INVENTORY_PATH, None)
    if inventory is None:
        raise SystemExit("Missing licenses/inventory.json")
    problems = []
    for path, digest in inventory["inputs"].items():
        real = ROOT / path
        if not real.exists() or sha256(real.read_bytes()) != digest:
            problems.append(f"Changed inventory input: {path}")
    for record in inventory["components"]:
        for file in record["license_files"]:
            real = ROOT / file["path"]
            if not real.exists() or sha256(real.read_bytes()) != file["sha256"]:
                problems.append(f"Missing/changed original license: {file['path']}")
    for path, data in products(inventory).items():
        if not path.exists() or path.read_bytes() != data:
            problems.append(f"Generated output drift: {path.relative_to(ROOT)}")
    if problems:
        raise SystemExit("\n".join(sorted(set(problems))))
    print(f"License inventory verified: {len(inventory['components'])} components")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="fetch missing original license artifacts")
    parser.add_argument("--check", action="store_true", help="offline input/text/generation drift guard")
    args = parser.parse_args()
    if args.check:
        check()
        return
    OUT.mkdir(exist_ok=True)
    cache = load_json(CACHE_PATH, {})
    lock = yaml.safe_load((ROOT / "pnpm-lock.yaml").read_text())
    records = collect_npm(lock, cache) + collect_python(cache)
    errors = refresh_missing(records, cache) if args.fetch else []
    if args.fetch:
        records = collect_npm(lock, cache) + collect_python(cache)
    records += load_json(EXTERNAL_PATH, [])
    for record in records:
        record.setdefault("dependencies", [])
        record.setdefault("direct_in", [])
        record.setdefault("license_declared", record.get("license_expression"))
    inventory = {"schema_version": 1,
                 "boundary": "Source lock/declarations plus installed metadata; not a release binary SBOM. Python transitive closure is observed for the generating interpreter; uninstalled optional voice/platform dependencies are not asserted installed.",
                 "inputs": {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes())
                            for p in source_inputs() if p.exists()},
                 "components": sorted(records, key=lambda x: x["purl"]),
                 "registry_errors": errors,
                 "gaps": [{"purl": r["purl"], "license_expression_missing": not bool(r["license_expression"]),
                           "original_text_missing": not bool(r["license_files"])} for r in records
                          if not r["license_files"] or not r["license_expression"]]}
    INVENTORY_PATH.write_bytes(json_bytes(inventory))
    for path, data in products(inventory).items():
        path.write_bytes(data)
    print(json.dumps({"components": len(records), "npm": sum(r["ecosystem"] == "npm" for r in records),
                      "python": sum(r["ecosystem"] == "pypi" for r in records),
                      "gaps": len(inventory["gaps"]), "registry_errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
