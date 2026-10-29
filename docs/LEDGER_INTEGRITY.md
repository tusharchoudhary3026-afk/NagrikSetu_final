# Ledger integrity chain

The append-only decision ledger now has a SHA-256 integrity sidecar in `ledger_integrity`. A database trigger computes a hash for every new ledger row in the same transaction, chaining it to the previous hash. The payload covers the ledger ID, complaint ID, event type, actor, suggested/final departments, reason/reason code, details and UTC creation timestamp. The first event starts from the literal genesis value `NYAYAFLOW_LEDGER_GENESIS_V1`.

When an older database is initialized, `core.db.init_db` appends a backfill for existing ledger rows in ID order. It does not modify the ledger rows. Backfill runs in the schema migration transaction and is idempotent. If a non-empty sidecar is incomplete, initialization refuses to invent replacement hashes.

Auditors and other audit-authorized staff can run **Verify ledger integrity** on Case replay. The server reloads the user's active role and checks each event, each previous-hash link, and ledger/sidecar row counts. The result reports the number verified and the first failing ledger ID. The database triggers also prevent ordinary UPDATE/DELETE on the sidecar.

This is a tamper-evidence aid, not a cryptographic signature, external timestamp, trusted remote checkpoint or guarantee against a database administrator who can replace the database and recompute the chain. Historical hashes are computed from the database contents present at migration time; they cannot prove those older values were authentic before migration. Verification does not replace backups or access controls.

## Backup and rollback

Before deploying this migration to a database with real records, stop the app and make a SQLite-consistent backup (use SQLite's `.backup` command or the application's approved backup process; copying only the main file while WAL is active is not sufficient). Verify the backup can be opened. Apply the migration to a copy first. The migration is additive: it creates the sidecar table and triggers without changing complaint or ledger columns or deleting prior rows.

For rollback, stop the app and restore the pre-migration backup. If retaining the migrated database while reverting to code that does not register `nyaya_ledger_hash`, first back up that database, then remove the hash trigger and sidecar objects:

```sql
DROP TRIGGER IF EXISTS ledger_integrity_after_insert;
DROP TRIGGER IF EXISTS ledger_integrity_no_update;
DROP TRIGGER IF EXISTS ledger_integrity_no_delete;
DROP TABLE IF EXISTS ledger_integrity;
```

Existing ledger rows remain intact in either rollback path. Restoring the pre-migration backup is the recommended rollback.
