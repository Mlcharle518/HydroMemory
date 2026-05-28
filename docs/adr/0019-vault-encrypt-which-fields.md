# ADR-0019: Vault encrypts content/state/tags/cycle/meta; routing columns stay plaintext

Status: Accepted

## Context

The §9 User-Controlled Memory Vault must encrypt memory at rest. But the storage
layer (ADR-0012) promotes the queryable routing/governance dimensions to plain
SQLite columns so that `query` filters are plain SQL and `check_access` can read
phase/reservoir/permissions directly. If the vault encrypted *everything*, those
columns would be ciphertext and both the indexed query and the access gate would
break. So the vault must decide precisely which fields can be encrypted without
disabling routing and governance.

## Decision

The `VaultRepository` (`hydromemory.vault.vault`) wraps the plain
`SqliteDropletRepository` and encrypts a **specific subset** of each droplet,
leaving the routing/governance columns plaintext:

- **Plaintext (so `query` + `check_access` keep working):** `phase`,
  `reservoir`, `memory_type`, `owner`, `visibility`, `retention`,
  `external_sharing`, `purity`, and the L1 `app_id` column.
- **Encrypted into one token:** `content`, `semantic_tags`, the full `state`
  vector, `cycle`, and `meta` are packed into a single canonical JSON payload,
  encrypted to **one** ciphertext token, and stashed in `meta["__vault__"]` of the
  on-disk droplet (`_encrypt_for_disk`). The on-disk `content` column becomes the
  token; `semantic_tags`/`cycle` are emptied; `state` is reduced to only
  `purity`. On read, `_decrypt_from_disk` pulls the token, decrypts, and restores
  the secret fields, returning a fully-decrypted droplet.
- **`purity` is duplicated** into its plaintext column (kept on the reduced
  on-disk `State(purity=...)`) so `min_purity` queries and governance still work,
  while the authoritative full state lives encrypted in the payload.
- Encryption is a **pluggable `Cipher`** (`hydromemory.vault.cipher`):
  `FernetCipher` (real symmetric encryption, lazily importing `cryptography` only
  when constructed) when `HYDRO_VAULT_KEY` is set, else `NullCipher` (the keyless
  dev default, an identity transform). `build_cipher` selects between them and
  logs a warning when falling back to plaintext.

## Consequences

- The indexed `query` and `check_access` operate unchanged under encryption,
  because everything they read is plaintext; the vault adds only an app-scope
  filter and per-row access enforcement on top of the backing query.
- The plaintext routing columns are a **deliberate metadata leak**: an attacker
  with raw DB access learns each droplet's phase, reservoir, type, owner,
  visibility, retention, external-sharing flag, purity, and owning app — but not
  its content, tags, full state, cycle, or meta. This is the documented cost of
  keeping routing/governance queryable at rest.
- `NullCipher` is the default when no key is configured, so **dev/offline runs
  store content as plaintext** (loudly warned in `build_cipher`); real protection
  requires setting `HYDRO_VAULT_KEY`. `_decrypt_from_disk` tolerates an
  un-encrypted row (no `__vault__` key), so a NullCipher store round-trips
  cleanly.
- Cycle metadata is encrypted, so `touch_cycle` cannot use the backing repo's
  plaintext `cycle_json` update path; the vault read-decrypts, mutates, and
  re-encrypts via `upsert` instead.
