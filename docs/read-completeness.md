# Read completeness

## Purpose

Reading a database is not sufficient to establish that the returned records are
complete. A row or relationship may be malformed, use an unsupported schema
shape, or fail validation. Treating the remaining parsed records as a complete
result can make an absent record indistinguishable from an unreadable one.

The read-completeness API makes that distinction explicit. It reports what was
observed, what was parsed, and identity-only diagnostics for source rows that
could not be interpreted. It does not expose the rejected row's payload.

## Public contract

`MoneywizApi` provides three related operations:

| Operation | Result |
| --- | --- |
| `load(managers=...)` | Reloads the requested managers and returns their aggregate completeness evidence. |
| `completeness(managers=...)` | Returns completeness evidence for managers that have already loaded. |
| `snapshot(managers=...)` | Returns JSON-safe records, completeness evidence, and the selected schema profile. |

The supported manager names are `accounts`, `payees`, `categories`,
`transactions`, `investment_holdings`, and `tags`.

```python
from pathlib import Path

from moneywiz_api import MoneywizApi

with MoneywizApi(
    Path("/absolute/path/to/moneywiz.sqlite"),
    managers=("accounts", "transactions"),
) as api:
    completeness = api.completeness().as_dict()
    snapshot = api.snapshot().as_dict()
```

The returned records remain regular library data. The completeness report is
separate evidence about how those records were obtained; it does not transform
model `as_dict()` compatibility output.

## Reading the result

Each manager has a `ManagerLoadReport` with the following states:

| Status | Meaning |
| --- | --- |
| `complete` | Every observed source record and required relationship was parsed. |
| `partial` | At least one observed record or relationship could not be fully interpreted. |
| `error` | Observation occurred, but no usable result could be published. |
| `unloaded` | The manager has not completed a load; this is different from a valid empty result. |

For each manager, the report includes source and parsed IDs, counts, skipped
record diagnostics, and relationship reports. A skipped-record diagnostic
contains only the source identity, entity name, bounded error kind, and exception
type. It intentionally omits the source values that failed parsing.

Transaction relationship reports cover category assignments, refunds, and tags.
Their storage state is `present`, `absent`, or `unknown`. An unknown or malformed
relationship layout prevents the containing manager from being complete.

## Using partial results safely

Partial data can still be useful for inspection, reporting, or diagnosing an
unsupported database layout. It must not be silently presented as a complete
answer.

```python
report = api.completeness()
if not report.complete:
    raise RuntimeError(f"read is {report.status}; inspect diagnostics first")

snapshot = api.snapshot().as_dict()
```

Callers choose their own policy for partial results. The library reports the
evidence and preserves parsed records; it does not infer the caller's intent or
discard usable data solely because another row failed.

## Consistent observation

Database access is read-only. A multi-manager load runs inside one SQLite read
transaction, so records and completeness evidence describe the same observed
database generation. A later scoped load refreshes the union of the newly
requested and previously loaded managers before publishing it.

If a reload fails, the API retains the previous published records and
completeness evidence. A direct manager load follows the same boundary: it
publishes records, relationships, and its report together only after successful
observation.

Schema metadata and physical table definitions are checked before cache-dependent
reads. If they change, callers must close and reopen the API instead of combining
current rows with stale schema metadata.

## Snapshot and ownership guarantees

`snapshot()` converts supported values into JSON-safe data and returns detached,
mutable dictionaries and lists. It rejects values that cannot be represented
safely, including binary values, arbitrary objects, non-finite floats,
unsupported mapping keys, and normalized-key collisions.

Published completeness reports and snapshots own immutable nested evidence.
Mutating a manager or an ordinary caller-owned dictionary after publication cannot
rewrite previously returned completeness information.

## Compatibility notes

Existing model `as_dict()` methods retain their raw compatibility behavior.
Read-completeness reporting is an additional public contract for callers that
need to distinguish a complete observation from a partial one.
