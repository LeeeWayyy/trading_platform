# ADR 0037: File-Based Universe Persistence

- Status: Accepted
- Date: 2026-03-01

## Context

The Universe Manager (P6T15/T15.1) needs to persist custom universe
definitions created by users.  These are small JSON documents (~1-5 KB)
that define filter criteria, symbol lists, and metadata.

Requirements:
- Durability: definitions must survive process restarts and crashes.
- Concurrency: multiple NiceGUI workers (or future API processes) may
  create, read, or delete universes concurrently.
- Simplicity: the expected scale (tens to low hundreds of universes) does
  not warrant a database migration or external store.
- Portability: definitions should be easy to back up, version-control,
  or migrate between environments.

Alternatives considered:
1. **Postgres table** - provides ACID and concurrent access, but requires
   a migration, schema maintenance, and couples universe storage to the
   trading database.  Overkill for the expected cardinality.
2. **Redis** - fast, but volatile by default.  Persistence modes (RDB/AOF)
   add operational complexity without clear benefit over the filesystem.
3. **SQLite** - single-writer lock model similar to file locking, but
   adds a dependency and does not improve on the JSON-file approach for
   this workload.

## Decision

Custom universe definitions are stored as individual JSON files in
`data/universes/<universe_id>.json`.

### Lock Model

- **Advisory file locking** via `fcntl.flock(LOCK_EX)` on a dedicated
  lock file (`data/universes/.lock`) protects all write operations
  (create, delete).
- Read operations (list, get) are lock-free; they tolerate brief
  inconsistency during concurrent writes because `os.rename` is atomic
  on POSIX and readers see either the old or new file, never a partial.

### Durability Guarantees

Writes follow the **temp-file, fsync, rename, fsync-parent** pattern:

1. Write to `<id>.json.tmp` with explicit `0o600` mode.
2. `os.fsync(fd)` the file descriptor to flush data to disk.
3. `os.rename(tmp, target)` - atomic on POSIX.
4. `os.fsync(dir_fd)` the parent directory to persist the rename.
5. On failure, the temp file is cleaned up; the original is untouched.

Deletes call `os.unlink` followed by best-effort `os.fsync` on the
parent directory.  If the directory fsync fails, the deletion still
succeeds and cache invalidation proceeds.

### Caching

An in-memory enrichment cache keyed by `(universe_id, as_of_date)` avoids
redundant Polars/CRSP enrichment.  A per-universe generation counter
(`_universe_generation`) is incremented on every mutation (save/delete).
In-flight enrichment tasks compare their captured generation against the
current value and discard stale results.  Cross-process changes are
detected via file mtime comparison.

### Validation and Safety

- Universe IDs are restricted to `[a-z0-9_]` (1-64 chars) to prevent
  path traversal and filesystem issues.
- All resolved paths are verified via `Path.resolve().is_relative_to()`
  against the universes directory root.
- Per-user creation limits (default 20) prevent storage abuse.

## Consequences

### Benefits
- Zero infrastructure dependencies beyond the filesystem.
- Easy backup (`cp -r data/universes/`) and environment migration.
- Atomic writes prevent corruption on crashes.
- Simple operational model: inspect/edit with standard tools.

### Risks and Mitigations
- **NFS/network filesystems**: `fcntl.flock` may not work correctly on
  NFS.  Mitigation: deployment target is local filesystem (Docker volume
  or host mount).  Document this constraint in operational runbook.
- **Windows portability**: `fcntl` is POSIX-only.  Mitigation: target
  platforms are Linux (production) and macOS (development).  If Windows
  support is needed, swap `fcntl.flock` for `msvcrt.locking`.
- **Scale ceiling**: scanning all JSON files for list/count operations
  becomes slow beyond ~1000 universes.  Mitigation: current limit is 20
  per user.  If scale grows, migrate to SQLite or Postgres (see trigger
  below).

### Migration Trigger

Migrate to a database-backed store when any of:
- Universe count exceeds 1000 total across all users.
- Multi-node deployment requires cross-process coordination beyond
  advisory file locks.
- Transactional operations spanning universes and other entities are
  needed (e.g., universe-linked backtests).
