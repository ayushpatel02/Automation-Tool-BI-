# Cached PBIR JSON schemas

PBIR validation is network-free at runtime: it validates each generated file against a
locally cached copy of Microsoft's published JSON schema, matched by the file's `$schema`
URL.

Schema URL pattern:

```
https://developer.microsoft.com/json-schemas/fabric/item/report/definition/{fileType}/{version}/schema.json
```

Cached files are named `{fileType}-{version}.json`, e.g. `visualContainer-2.0.0.json`.

## Refreshing the cache

Run the fetch script (requires network access) to (re)download the current schemas:

```bash
python -m app.validation.fetch_schemas
```

If a schema is not present, validation for that file type is skipped (structural and
cross-reference checks still run). **Verify the current version strings against Microsoft
documentation** before relying on schema validation — versions may have incremented past
those defaulted in `fetch_schemas.py`.
