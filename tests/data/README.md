# Test data

- `sarif-schema-2.1.0.json`: the OASIS SARIF 2.1.0 JSON schema (errata 01), byte for byte as
  `https://raw.githubusercontent.com/oasis-tcs/sarif-spec/adbb670c018335b0f384e6dd8819f4ea055d7ee1/sarif-2.1/schema/sarif-schema-2.1.0.json`
  serves it (fetched 2026-10-05; sha256
  `c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e`). `tests/test_standards.py`
  validates every SARIF log the standards write against it, offline.
