# ADR-0027: Vault key rotation via MultiFernet + an owner-gated re-encryption migration

Status: Accepted

## Context

The v2 vault ([ADR-0019](0019-vault-encrypt-which-fields.md)) encrypts each
droplet's secret fields into a single Fernet token under one key
(`HYDRO_VAULT_KEY`). v2 explicitly **deferred key rotation / re-encryption
migration** — but "encryption with no way to change the key" is the obvious
production gap in the vault, which is HydroMemory's core differentiator
(user-controlled, encrypted, audited memory). We need to rotate the vault's key
without downtime (reads must keep working mid-rotation) and without data loss,
and re-encrypt data at rest so a compromised old key can eventually be retired.

## Decision

**Hold a primary key plus retired keys as a `MultiFernet`, and add an
owner-gated, vault-wide `rotate_keys` migration that re-encrypts every row to the
primary.**

- **Cipher (`vault/cipher.py`).** `FernetCipher(key, *, previous_keys=())` builds
  `MultiFernet([primary, *retired])`: `encrypt` always uses the primary, `decrypt`
  tries every key, and a new `rotate(token)` re-encrypts one token to the primary
  (`MultiFernet.rotate`). `rotate` is added to the `Cipher` protocol;
  `NullCipher.rotate` is the identity (no key to rotate). Each key is still a raw
  Fernet key *or* a SHA-256-derived passphrase.
- **Config (`config.py`).** New `vault_prev_keys: list[str]` (env
  `HYDRO_VAULT_PREV_KEYS`, comma-separated). `build_cipher` threads it into
  `FernetCipher`. Default empty, so single-key behavior is unchanged.
- **Migration (`VaultRepository.rotate_keys`).** Walks **all** rows (rotation is a
  property of the user's vault, not an app scope — it ignores L1/L2 scope), rotates
  each row's single token via `cipher.rotate`, and writes it back. It touches only
  `content` + `meta["__vault__"]`; routing columns, `app_id`, embeddings (the
  plaintext `.vec` cache, [ADR-0020](0020-vector-index-decrypted-in-process-cache.md)),
  and links are preserved. A convenience `rotate_vault_keys(config)` opens the
  owner's cross-app vault and calls it.

## Consequences

- **No-downtime rotation.** Setting `HYDRO_VAULT_KEY=<new>` +
  `HYDRO_VAULT_PREV_KEYS=<old>` makes reads work immediately (old rows fall back to
  `<old>`) while new writes use `<new>`; `rotate_keys` then re-encrypts at rest, and
  the old key can be dropped. Proven in `tests/test_vault_rotation.py`: after
  rotation the new key alone reads every row and the old key alone cannot.
- **Owner-only + audited.** `rotate_keys` requires a user-proxy identity; a
  non-owner attempt is audited as denied and raises. A successful run appends one
  `operation="rotate_keys"` audit entry with the count, extending the hash chain
  ([ADR-0021](0021-append-only-hash-chained-audit-log.md)).
- **Idempotent, crash-safe, fail-loud.** Re-running is harmless; a partial run is
  still fully decryptable by the multi-key cipher (just re-run); and a token that
  no configured key can decrypt raises rather than silently losing data.
- **Separate migration:** encrypting a previously keyless (NullCipher/plaintext)
  vault is a distinct first-time migration, not done by `rotate_keys` (a no-op under
  NullCipher) — it is handled by
  [ADR-0029](0029-keyless-to-encrypted-vault-migration.md)
  (`encrypt_plaintext_rows` / `encrypt_vault`). Key generation/storage/KMS remains
  the operator's concern. v1 + v2 stay green; this is additive.
