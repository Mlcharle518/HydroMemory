# ADR-0029: Keyless → encrypted first-time vault migration

Status: Accepted (extends [ADR-0027](0027-vault-key-rotation.md))

## Context

The vault runs keyless by default (`NullCipher`, plaintext at rest) for offline/dev
use ([ADR-0019](0019-vault-encrypt-which-fields.md)). When an operator later
configures `HYDRO_VAULT_KEY`, existing rows are still **plaintext payloads**, and
the new `FernetCipher` cannot decrypt them — `get` raises `InvalidToken`. Key
*rotation* ([ADR-0027](0027-vault-key-rotation.md)) re-encrypts *between Fernet
keys* and explicitly deferred this case. But "I started keyless and now want to
encrypt my existing data" is a real onboarding path, and without it that data is
stranded. So we need a one-time keyless → encrypted migration.

## Decision

Add `VaultRepository.encrypt_plaintext_rows()` (and an `encrypt_vault(config)`
convenience): walk every row and Fernet-encrypt the ones still stored as
plaintext, in place.

- **Detecting a plaintext row.** A `NullCipher` row stores the canonical payload
  verbatim, so its token **parses as a JSON object**; a Fernet token is base64 and
  never does. `_is_plaintext_payload(token)` = "`json.loads` yields a dict." This
  is more robust than try-decrypt-and-recover: it can't mistake a ciphertext token
  (under some other key) for plaintext, so already-encrypted rows are skipped
  rather than double-wrapped.
- **Encrypt in place.** For a plaintext row, the token bytes *are* the payload, so
  `cipher.encrypt(token)` produces the at-rest ciphertext; `content` and
  `meta["__vault__"]` are updated and the row re-upserted (routing columns,
  `app_id`, embeddings, links untouched).
- **Guards.** Owner-only (user-proxy; a non-owner attempt is audited as denied and
  raises). Requires a Fernet cipher — calling it on a keyless (NullCipher) vault
  raises `RuntimeError` ("set `HYDRO_VAULT_KEY` first"), since there is nothing to
  encrypt *to*. One `operation="encrypt_plaintext_rows"` audit entry records the count.

## Consequences

- **The keyless → encrypted path now works:** set `HYDRO_VAULT_KEY`, call
  `encrypt_vault(config)` once, and the previously-plaintext data is readable only
  with the key. Proven in `tests/test_vault_rotation.py`
  (`test_encrypt_plaintext_rows_*`): plaintext on disk before, ciphertext after,
  `get` decrypts under the key, and a pre-migration `get` raises `InvalidToken`.
- **Idempotent and mixed-safe.** Already-encrypted rows are skipped, so a re-run is
  a no-op and a vault that mixes plaintext (old) and ciphertext (new) rows migrates
  cleanly.
- **Distinct from rotation.** `encrypt_plaintext_rows` encrypts plaintext;
  `rotate_keys` ([ADR-0027](0027-vault-key-rotation.md)) rotates between Fernet
  keys. Keeping them separate keeps each detection path simple and each operation's
  intent explicit. Additive; v1 + v2 stay green.
