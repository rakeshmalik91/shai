# Pagination tool-schema fix

## What changed
- Kilo/Kilo Free now receive the full canonical `read_file` tool description instead of a shortened generic description.
- `read_file` schema requires `path`, `offset`, and `limit`.
- The `offset` and `limit` schema properties now include explicit descriptions so the model is told how to paginate.
- Added a regression test ensuring the Kilo tool payload preserves the pagination instructions and required schema.

## Why
The inspected source already had a paginated `read_file` implementation and required pagination fields in the canonical schema. However, `build_kilo_tools()` replaced the detailed `read_file` description with a generic sentence. This removed the strongest pagination instructions from requests sent to Kilo providers.

## Verification
- 73 unittest tests passed.
- 29 Python files compiled successfully.
