# Unified Persistence Foundation Design

## Goal

Replace JSON-backed runtime state for feature flags, the group whitelist, and
MyCard bindings/subscriptions with reliable SQLite-backed repositories. Preserve
all existing data through an automatic, one-time migration. Leave tournament
state and all tournament functionality unchanged.

## Scope

- Add a small shared persistence foundation responsible for SQLite connection
  configuration, transactions, schema version tracking, and database startup.
- Add SQLite repositories for feature flags, the group whitelist, MyCard user
  bindings, and MyCard subscriptions.
- Store this low-volume bot state in `data/hikari.db`.
- On first startup, migrate data from `feature_flags.json`, `whitelist.json`,
  `mycard_user.json`, and `subscribe.json` in one transaction.
- Rename each successfully imported legacy file to the same name with a
  `.bak` suffix. Never overwrite an existing backup.
- Keep existing public service functions where practical so plugins and
  monitors require only narrow call-site changes.
- Add tests for schema initialization, migration, idempotency, rollback, and
  repository behaviour.

## Non-goals

- Do not migrate or modify `match_state.json`, tournament commands, or
  tournament deck artifacts.
- Do not merge Cardrush price history into `hikari.db`.
- Do not migrate the third-party card database (`card.cdb`), image caches, PDF
  outputs, or YDK files into SQLite.
- Do not introduce a general-purpose ORM.

## Storage Boundaries

```text
data/
  hikari.db             Low-volume bot state: flags, whitelist, MyCard state
  cardrush_prices.db    Cardrush market-history domain; remains independent
  cache/                Rebuildable third-party card data and image caches
  artifacts/            Deck files and generated binary outputs
```

The persistence foundation is shared infrastructure, not a requirement that
every domain share one physical database. Cardrush retains its repository and
its independent database because price history grows quickly and can be reset
or backed up separately without risking user state.

## Components

### Database foundation

`hikari_bot/persistence/database.py` will expose a narrow connection and
transaction API. Each connection will enable foreign keys, WAL mode, and a
busy timeout so short concurrent writes from commands, web routes, and
monitors do not immediately fail with `database is locked`.

### Migration runner

`hikari_bot/persistence/migrations.py` will create a `schema_migrations` table
and apply ordered migrations once at startup. It will also run the legacy JSON
import exactly once. The import will only run when the state tables are empty
and no legacy-import migration marker exists.

### State repositories

- `FeatureFlagsRepository`: read and set named Boolean flags.
- `WhitelistRepository`: add, list, test, and clear group IDs.
- `MyCardRepository`: bind QQ IDs to MyCard usernames and manage unique
  `(username, target_type, target_id)` subscriptions.

Repositories own SQL; plugins and services will not open files or SQLite
connections directly.

## Schema

```sql
CREATE TABLE feature_flags (
  name TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
);

CREATE TABLE whitelist_groups (
  group_id TEXT PRIMARY KEY
);

CREATE TABLE mycard_bindings (
  qq_id TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE mycard_subscriptions (
  username TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('group', 'private')),
  target_id TEXT NOT NULL,
  PRIMARY KEY (username, target_type, target_id)
);
```

The default values remain compatible with current behaviour: missing
`mycard_notify` and `mensa_monitor` flags are treated as enabled.

## Legacy Migration Flow

```text
Application startup
  -> create/upgrade hikari.db schema
  -> if legacy import has not run and state tables are empty:
       validate all present JSON inputs
       import all valid data in one transaction
       record legacy-import migration marker
       rename imported files to *.bak
  -> start plugins, monitors, and web routes
```

If JSON parsing, validation, insertion, or backup renaming fails, the database
transaction is rolled back and the original files are left untouched. A clear
error is logged; no partial database migration is considered successful.

If the database already contains state, it is authoritative: legacy JSON is
not imported and cannot overwrite database values.

## Runtime Data Flow

```text
Plugin / monitor / web route
  -> existing service-level function
  -> feature-specific repository
  -> shared database foundation
  -> hikari.db
```

The initial migration may preserve synchronous repository APIs and run them in
worker threads where they are reached from async handlers. This minimizes
plugin churn. The shared foundation keeps transactions short and does not
perform network, rendering, or file-download work while a transaction is open.

## Error Handling

- Repository methods raise a small persistence-specific error that includes
  operation context without exposing raw SQL to users.
- Existing plugins keep their user-facing messages and log the detailed error.
- Missing legacy files are valid and import as empty state.
- Invalid legacy JSON stops the import rather than silently dropping records.
- Duplicate subscriptions are ignored through the primary key and report the
  same result as the current idempotent subscription operation.

## Verification

- Unit tests create temporary databases and exercise each repository.
- Migration tests cover successful import, missing files, invalid JSON,
  idempotent restart, existing database precedence, and rollback.
- Compatibility tests verify the MyCard monitor and whitelist commands retain
  their existing data shapes and outcomes.
- The existing Cardrush repository remains covered independently and is not
  coupled to `hikari.db`.

## Acceptance Criteria

1. A first startup imports existing flag, whitelist, MyCard binding, and
   subscription data without manual steps.
2. Successful imports retain recoverable `.bak` copies and never re-import on
   later startups.
3. A failed import leaves both the database state and original legacy files
   unchanged.
4. Runtime reads and writes for the four migrated concerns no longer depend on
   JSON files.
5. Tournament state remains JSON-backed and unaffected.
6. Cardrush keeps `data/cardrush_prices.db` and continues to function through
   its current repository boundary.
