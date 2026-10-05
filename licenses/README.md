# Third-party license archive

See [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md) for distribution boundaries and important bundled/native terms.

- [PACKAGE-NOTICES.md](./PACKAGE-NOTICES.md): complete versioned package index with original text links.
- [inventory.json](./inventory.json): machine inventory, direct/transitive scopes, evidence provenance, archived text SHA-256 hashes and unresolved gaps.
- [sbom.cdx.json](./sbom.cdx.json): generated CycloneDX 1.6 source dependency BOM; not an attestation of a shipped binary/container.
- `texts/`: unchanged original package/upstream license, notice and attribution bytes, deduplicated by SHA-256. Copyright holders remain those identified in each original.
- `registry-cache.json`: exact-version official npm/PyPI evidence for packages absent from the generating environment.
- `external-components.json`: reviewed deployment/model/native asset references and their pinned upstream evidence.

Rebuild and offline checks: [dependency-and-sbom.md](../docs/compliance/dependency-and-sbom.md), [generator](../scripts/compliance/generate_notices.py).

The existing generic license texts and MeloTTS original are retained for historical references. This directory does not grant a license to the project's own code.
