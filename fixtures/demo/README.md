# Synthetic demo fixtures

Everything under `fixtures/demo/` is **project-authored synthetic content**:
no textbook text, OCR output, chunks, real user data, real file ids, page
numbers, content hashes or source paths are stored here. Textbook titles are
fictional (`合成/示例/示范` markers), concept definitions/examples are written
for this demo, and every identifier is an `fx_*` synthetic key.

`scripts/demo/export_pages_demo.py` seeds an isolated runtime from these
fixtures and captures read-only API responses for the GitHub Pages demo. The
knowledge-graph and fictional-textbook-library showcase is intentionally
self-contained: it demonstrates the product UI, never a specific real textbook.
