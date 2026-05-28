# The User-Controlled Memory Vault (§9)

The vault is the v2 §9 storage layer that makes a user's memory **encrypted at
rest, audited, access-enforced, and app-scoped**. It is a drop-in
`DropletRepository` (`VaultRepository`) that wraps the plain
`SqliteDropletRepository` and adds, on every method: per-app scope filtering,
`check_access` enforcement, an append-only audit entry, and encryption of the
secret fields.

It lives in [`hydromemory/vault/`](../hydromemory/vault): `cipher.py` (pluggable
encryption), `audit.py` (the hash-chained `AuditLog`), `scope.py`
(`AppIdentity` / `AppScope`), and `vault.py` (`VaultRepository` + the
`open_vault_store` / `build_vault_engine` factories). The integration levels that
sit on top (L1 app scope, L2 owner vault) are in
[integration-levels.md](integration-levels.md); the base governance model is in
[governance-policy.md](governance-policy.md).

> The vault is **off by default** (`HydroConfig.vault_enabled=False`, no
> `vault_key`), so v1 behavior is unchanged until it is explicitly wired in.

---

## The Cipher abstraction

`cipher.py` defines a small `Cipher` protocol (`label`, `encrypt(bytes) ->
bytes`, `decrypt(bytes) -> bytes`, `rotate(bytes) -> bytes`) with two
implementations:

| Cipher         | `label`    | Behavior                                                             |
| -------------- | ---------- | ------------------------------------------------------------------- |
| `NullCipher`   | `null-dev` | Identity transform — stores **plaintext**. Offline/dev default. NOT secure. `rotate` is a no-op. |
| `FernetCipher` | `fernet`   | Real symmetric encryption via `cryptography.fernet.MultiFernet` (a primary key + optional retired keys). |

`build_cipher(config, *, conn=None)` selects between them:

- If `config.vault_key` is set -> `FernetCipher(key, previous_keys=config.vault_prev_keys)`,
  passing the per-vault scrypt salt when the key is a passphrase (see **Key
  derivation** below). The optional `conn` is the vault's SQLite connection used
  to load/persist that salt in `vault_meta`; when omitted, a short-lived
  connection on `config.db_path` is used (the store has already created the file).
- Otherwise -> `NullCipher`, and a **warning is logged** ("content stored as PLAINTEXT").

**`HYDRO_VAULT_KEY` (+ retired keys).** The primary key comes from
`config.vault_key` (env `HYDRO_VAULT_KEY`); retired keys come from
`config.vault_prev_keys` (env `HYDRO_VAULT_PREV_KEYS`, comma-separated).
`FernetCipher` builds a `MultiFernet([primary, *retired])`: **encrypt** always
uses the primary, **decrypt** tries every key (so rows written under a retired
key still read during a rotation), and **`rotate(token)`** re-encrypts a single
token to the primary.

**Key derivation (raw key vs. passphrase).** Each key may be a raw
urlsafe-base64 Fernet key *or* an arbitrary passphrase:

- **Raw Fernet key — the fast path (unchanged).** If the value is already a valid
  32-byte urlsafe-base64 Fernet key it is used **verbatim**, with no KDF and no
  DB access. This is the recommended production form (generate one with
  `cryptography.fernet.Fernet.generate_key()`).
- **Passphrase — salted scrypt.** A non-Fernet string is stretched with
  **`scrypt`** (`n=2**14, r=8, p=1`, 32-byte output) over a **random 16-byte,
  per-vault salt**. The salt is generated on first use and persisted in the
  vault's `vault_meta` table (a tiny `name TEXT PRIMARY KEY, value BLOB`
  key/value table created idempotently), so the *same* vault re-derives the
  *same* key on reopen (scrypt is deterministic given salt + passphrase) while
  two *different* vault DBs with the *same* passphrase derive *different* keys.
  This replaces the old unsalted single-`sha256(passphrase)` derivation, which
  was offline brute-forceable and gave identical passphrases identical keys.

**Legacy compatibility + migration.** For each passphrase the cipher *also* keeps
the old `base64.urlsafe_b64encode(sha256(passphrase))` key as a **decrypt-only
fallback** inside the `MultiFernet`, so rows written under the pre-scrypt scheme
still read. New writes use the scrypt primary; running `rotate_keys()` (see **Key
rotation**) re-encrypts every legacy row to the scrypt primary, after which the
old `sha256` key is no longer needed.

**Dev vs. production.** For real deployments prefer a **raw Fernet key** (maximum
entropy, no KDF cost) or a high-entropy passphrase; the scrypt parameters here
are moderate (a few ms) to keep the test suite fast, not maximal. A short,
human-memorable passphrase is still only as strong as scrypt over that
passphrase — choose accordingly. Offline/dev runs with no key use `NullCipher`
(plaintext, loudly warned) as before.

**`cryptography` is an optional extra.** It is imported **lazily inside**
`FernetCipher.__init__`, never at module load. Install it via the `vault` extra
(see `pyproject.toml`). `NullCipher` needs no third-party dependency, so offline
tests and dev runs work with no secrets installed.

---

## Which fields are encrypted

This is the crux of the design. The backing repo persists a droplet by reading
`droplet.to_dict()` and writing **promoted columns** plus `*_json` sidecars. To
keep `query` and `check_access` working, the routing/governance columns must stay
plaintext; everything secret is packed into a single encrypted token.

### Plaintext at rest (on the on-disk droplet)

The vault preserves these so the backing `query` and governance gate still work:

- **Routing / governance columns:** `phase`, `reservoir`, `memory_type`, and the
  full `permissions` block (`owner`, `visibility`, `retention`,
  `external_sharing`, ...).
- **`purity`:** the only `state` float kept in cleartext. The on-disk `state` is
  rebuilt as `State(purity=droplet.state.purity)` so the queryable `purity`
  column survives; `state_json` therefore leaks only `purity` plus zeros.
- **`app_id`:** the L1 scope column (written by a small direct `UPDATE` on the
  backing connection, since the backing upsert never sets it).
- **Also plaintext as a side effect of being promoted columns:** `id`, `source`,
  and `created_at` (the backing repo promotes these to their own columns; the
  vault does not encrypt them).

### Encrypted into one token

`content`, `semantic_tags`, the **full** `state` vector, `cycle`, and `meta` are
serialized into ONE canonical JSON payload, encrypted to a single token, and
stored in `meta["__vault__"]` of the on-disk droplet. The on-disk droplet
therefore carries:

- `content` = the ciphertext token (so the `content` column is ciphertext);
- `semantic_tags = []` and `cycle = Cycle()` (their `*_json` sidecars leak nothing);
- `state` = a `State` with only `purity` preserved;
- `meta = {"__vault__": <token>}`.

On read, the vault pulls `meta["__vault__"]`, decrypts it, and restores the
secret fields, returning a fully-decrypted `Droplet`. The `__vault__` key is
stripped on read, so a round-tripped `meta` never leaks it. (A row with no
`__vault__` key — e.g. a plain store under a NullCipher path — is returned
unchanged.)

### Documented leakage

Two things are **intentionally** left in cleartext and are documented leaks:

1. **The routing/governance columns** (and `id`/`source`/`created_at`) listed
   above. They are needed for query + access decisions without decrypting every
   row. An attacker with raw DB access learns a droplet's reservoir, phase,
   owner, visibility, purity, source, and timestamps — but not its content,
   tags, full state, cycle, or meta.
2. **The plaintext vector index.** The backing repo stores each embedding as
   plaintext in the on-disk droplet's `meta["__embedding__"]` (and in the
   file-backed index at `f"{db_path}.vec.npz"`). The vault keeps the original
   `embedding` on the on-disk droplet on purpose, so the vector index and
   `rebuild_index` keep working under encryption. The raw embedding vectors are
   therefore readable at rest.

---

## Search and rebuild under encryption

Because the index holds **decrypted-in-process** vectors (the plaintext-at-rest
embeddings loaded into memory), cosine ranking is **exact** even when content is
encrypted:

- **`search_similar`** delegates to the backing vector search but wraps the
  caller's `candidate_filter` so it sees a *decrypted* droplet, and it always
  enforces app-scope + a READ `check_access` on every hit before returning it.
- **`rebuild_index`** delegates straight to the backing rebuild — embeddings are
  plaintext, so the backing repo reloads them from rows directly with no decrypt
  step.
- **`touch_cycle`** cannot use the backing repo's plaintext `cycle_json` update
  (cycle is encrypted), so it read-decrypts, mutates the cycle in memory, then
  re-encrypts via an upsert (preserving the embedding and routing columns).

---

## The AuditLog

`AuditLog` (`audit.py`) is an **append-only, hash-chained** trail over the
`audit` table (created idempotently via `AUDIT_DDL`). Every access decision is
recorded as an `AuditEntry`:

```
seq, ts, actor, app_id, operation, droplet_id, allowed,
obligations_json, detail, prev_hash, entry_hash
```

- **Hash chain.** `entry_hash = sha256(prev_hash || canonical(entry))`, where
  the first entry chains off a genesis hash of 64 zeros and `canonical` is
  deterministic JSON (sorted keys, no whitespace). The chain makes **insertion,
  edits, and reordering** detectable — they all break a link.
- **Head watermark (defeats tail truncation).** The chain *alone* cannot detect
  **tail truncation**: deleting the most recent rows leaves a shorter but
  internally-consistent chain that would otherwise still verify. So on every
  `append` the log persists a **head watermark** — the max `seq` and the last
  `entry_hash` — in the `vault_meta` table (the same key/value sidecar the cipher
  uses for its KDF salt). `verify_chain()` rejects a log whose actual last row is
  *behind* that watermark (a lower max `seq`, or a head `entry_hash` that no
  longer matches). The log is therefore tamper-evident **given an intact
  watermark**; a log with no watermark recorded (e.g. one written before this
  change, or a genuinely empty one) is not failed on that basis alone.
- **`verify_chain()`** recomputes the chain from row 0, returns `False` on the
  first inconsistency (a mismatched `prev_hash` link or a recomputed `entry_hash`
  that differs from the stored one), and finally returns `False` if the head
  watermark is ahead of the real last row (tail truncated).
- **`query(**filters)`** filters by `actor` / `app_id` / `droplet_id` /
  `operation` (equality), `allowed` (bool), `since` (inclusive lower bound on
  `ts`), and `limit` (most-recent N).

### Out-of-scope cross-app attempts are audited (ADR-0028)

`VaultRepository` checks `_in_scope(droplet_id)` **first** on its single-id
methods (`get` / `delete` / `touch_cycle` / `add_link` / `remove_link`). A
cross-app access is still fully isolated (returns `None` / no-ops), but the
**attempt is now audited** as a denied entry (`allowed=False`,
`detail="out of app scope"`, with the natural operation) via
`_audit_out_of_scope` — so cross-scope probes are visible in the owner's
tamper-evident log instead of being a silent miss. This closes the gap originally
noted in [ADR-0021](adr/0021-append-only-hash-chained-audit-log.md); see
[ADR-0028](adr/0028-audit-out-of-scope-access-attempts.md). Asserted by
`tests/test_l1_app_scoping.py::test_l1_out_of_scope_attempt_is_audited`.

Bulk `query` / `search_similar` scope-filtering stays a **silent per-row filter**
(not a targeted id probe). The other audited denial path is the **in-scope** one:
a read that clears the scope filter but is then refused by `check_access` (e.g. a
weak, non-user-proxy identity reading a higher-trust reservoir within its own
scope) is recorded with the denial reason in `detail`
(`test_l1_in_scope_denied_read_is_audited`).

---

## Access enforcement on every method

`VaultRepository` calls `check_access` (the governance entry point) on every
data path, audits the decision, and only proceeds on allow:

| Method            | Scope check first | `check_access` operation | Audited |
| ----------------- | ----------------- | ------------------------ | ------- |
| `upsert`          | tags `app_id` after write | `MUTATE`            | yes     |
| `get`             | `_in_scope` (else `None`) | `READ`  | yes — in-scope + out-of-scope attempt |
| `delete`          | `_in_scope`       | `OVERWRITE`              | yes (incl. out-of-scope attempt) |
| `query`           | `_scoped_ids` filter | `READ` per row        | yes per row (scope skips silent) |
| `search_similar`  | `_scoped_ids` filter | `READ` per hit        | gate only (scope skips silent) |
| `touch_cycle`     | `_in_scope`       | (re-encrypt via upsert)  | via upsert; out-of-scope attempt audited |
| `add_link` / `remove_link` | `_in_scope` on `src_id` | (delegates) | out-of-scope attempt only |

Scope filtering is driven by `AppScope`: a scoped view
(`AppScope(app_id="...")`) sees only rows whose `app_id` column matches; a
cross-app view (`AppScope(cross_app=True)`) sees everything (`_scoped_ids()`
returns `None`, meaning "no filter").

---

## Factories and configuration

### open_vault_store

```python
open_vault_store(config, *, identity=None, scope=None) -> VaultRepository
```

Opens a `VaultRepository` over the configured store: a fresh
`SqliteDropletRepository` backing, the configured cipher (`build_cipher`), and an
`AuditLog` on the **same** SQLite connection (so audit rows live alongside the
droplets). Defaults:

- `identity` -> a **user-proxy** identity (`AgentIdentity(name="user",
  trust_level=HIGH_TRUST, is_user_proxy=True)`) — the owner acting directly on
  their own vault.
- `scope` -> an **L1 app scope** when `config.app_id` is set, else the **L2
  owner** cross-app vault.

### build_vault_engine

```python
build_vault_engine(config, *, app_id=None, identity=None) -> Engine
```

Builds a full `Engine` whose `repo` is a scoped `VaultRepository`. It reuses the
v1 engine wiring (intelligence + the full `Verbs` bundle) but injects the vault
as the repo, so **every** engine/verb operation is encrypted, audited,
access-enforced, and app-scoped (L1 when `app_id` is given, else the owner's L2
cross-app vault).

### Configuration

`HydroConfig` (`hydromemory/config.py`) exposes the vault knobs (all default off
so v1 is unchanged):

| Field           | Env var               | Default | Meaning                                          |
| --------------- | --------------------- | ------- | ------------------------------------------------ |
| `vault_enabled` | `HYDRO_VAULT_ENABLED` | `False` | Master toggle for the vault layer.               |
| `vault_key`     | `HYDRO_VAULT_KEY`     | `None`  | Primary encryption key/passphrase; absent -> `NullCipher`. |
| `vault_prev_keys` | `HYDRO_VAULT_PREV_KEYS` | `[]` | Retired keys (comma-separated) kept for decryption during a rotation. |
| `app_id`        | `HYDRO_APP_ID`        | `None`  | Default L1 app scope; absent -> L2 owner vault.   |

`HYDRO_VAULT_ENABLED` is parsed truthy from `1` / `true` / `yes`.

### rotate_vault_keys

```python
rotate_vault_keys(config, *, identity=None) -> int   # returns rows re-encrypted
```

Re-encrypts the **whole** vault to `config.vault_key` (the new primary). Opens the
owner's cross-app vault and calls `VaultRepository.rotate_keys()`. Also runnable as
`hydromem vault-rotate` (keys from the environment). See **Key rotation** below.

### encrypt_vault

```python
encrypt_vault(config, *, identity=None) -> int   # returns rows encrypted
```

One-time **keyless → encrypted** migration: with `config.vault_key` set, opens the
owner's cross-app vault and calls `VaultRepository.encrypt_plaintext_rows()` to
Fernet-encrypt every row still stored as plaintext. Also runnable as `hydromem
vault-encrypt`. See **Encrypting a previously-keyless vault** below.

---

## Key rotation

The vault supports rotating its encryption key without downtime or data loss —
the v2-deferred "key rotation / re-encryption migration", now built.

The flow (Fernet → Fernet):

1. **Set the new primary, keep the old as retired.** `HYDRO_VAULT_KEY=<new>` and
   `HYDRO_VAULT_PREV_KEYS=<old>`. From this moment new writes use `<new>` and
   reads transparently fall back to `<old>` for not-yet-rotated rows (no error,
   no migration required to keep running).
2. **Re-encrypt at rest.** Call `rotate_vault_keys(config)` (or
   `VaultRepository.rotate_keys()`). It walks **every** row, re-encrypts its single
   ciphertext token to the primary via `cipher.rotate`, and writes it back.
   Routing columns, `app_id`, embeddings, and links are untouched; the vector
   index keeps working. One audit row (`operation="rotate_keys"`) records the count.
3. **Drop the retired key.** Once rotation completes, remove `HYDRO_VAULT_PREV_KEYS`;
   every row now decrypts under `<new>` alone.

Properties:

- **Owner-only.** `rotate_keys` requires a user-proxy (owner) identity; a non-owner
  attempt is **audited as denied** and raises `PermissionError`.
- **Idempotent + crash-safe.** Re-running is harmless (already-rotated rows
  re-rotate to the same primary), and a partial run leaves a mix the multi-key
  cipher still decrypts — so you can simply re-run after fixing a problem.
- **Fail-loud on a missing key.** If a row's token can't be decrypted with any
  configured key (you forgot to list the old key in `HYDRO_VAULT_PREV_KEYS`),
  rotation raises rather than silently dropping data.
- **NullCipher.** With no key configured, `rotate_keys` is a no-op returning `0`.

## Encrypting a previously-keyless vault

Rotation moves between Fernet keys. Encrypting a vault that ran **keyless**
(`NullCipher`, plaintext at rest) for the first time is a distinct migration,
done by `VaultRepository.encrypt_plaintext_rows()` (or `encrypt_vault(config)`):

1. **Set a key.** `HYDRO_VAULT_KEY=<key>`. New writes are now encrypted, but old
   rows are still plaintext — and `get` on them raises `InvalidToken` (a Fernet
   cipher can't decrypt plaintext), so migrate before reading them.
2. **Encrypt at rest.** Call `encrypt_vault(config)`. It detects each still-plaintext
   row (its token parses as a JSON object; a Fernet token never does) and
   Fernet-encrypts it in place; rows already ciphertext are skipped. One audit row
   (`operation="encrypt_plaintext_rows"`) records the count.

Properties: **owner-only** (non-owner is audited-denied + raises); **idempotent and
mixed-safe** (re-running is a no-op; a plaintext/ciphertext mix migrates cleanly);
and it **requires a key** — calling it on a keyless vault raises `RuntimeError`
(nothing to encrypt *to*). Tested in `tests/test_vault_rotation.py`
(`test_encrypt_plaintext_rows_*`); see
[ADR-0029](adr/0029-keyless-to-encrypted-vault-migration.md).
