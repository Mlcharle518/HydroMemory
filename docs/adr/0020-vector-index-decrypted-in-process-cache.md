# ADR-0020: The vector index is a decrypted-in-process cache (plaintext embeddings)

Status: Accepted

## Context

Semantic recall (§5.6) depends on the file-backed brute-force cosine vector index
(ADR-0012), which is a rebuildable cache persisted next to the database as
`{db_path}.vec.npz` and reloaded from the embeddings stored on each row. Under the
encrypted vault (ADR-0019), the question is what happens to embeddings: encrypting
them would make cosine ranking impossible without decrypting the entire index on
every search, and would break `rebuild_index`, which reconstructs the index by
reading embeddings back off the stored rows.

## Decision

Store **embeddings as plaintext** and treat the vector index as a
**decrypted-in-process cache**. The vault's `_encrypt_for_disk` deliberately
leaves `droplet.embedding` unchanged and excludes the backing repo's reserved
embedding key from the encrypted payload, so the `SqliteDropletRepository`
persists the embedding in `meta_json["__embedding__"]` and the `.vec.npz` exactly
as in v1 (`hydromemory.vault.vault`, `hydromemory.storage.sqlite_repository`).
`VaultRepository.search_similar` wraps the caller's `candidate_filter` so it sees
a *decrypted* droplet and always enforces app-scope + `Operation.READ` access on
each hit, while the ranking itself runs over the plaintext index;
`VaultRepository.rebuild_index` simply delegates to the backing rebuild, which
reloads plaintext embeddings from the rows — no decrypt needed.

## Consequences

- `search_similar` and `rebuild_index` keep working under encryption, with exact,
  deterministic cosine ranking (the ADR-0012 guarantee) preserved, and per-hit
  access still enforced via the wrapped filter.
- This is a **documented leakage**: the embedding vectors sit in plaintext in
  `.vec.npz` (and in `meta_json["__embedding__"]`), so an attacker with raw file
  access can run similarity queries / attempt embedding-inversion even though the
  content token is encrypted. The vault module docstring labels this an explicit
  in-process leak — the price of keeping similarity search functional at rest.
- A deployment that cannot accept plaintext embeddings would need a different
  index strategy (encrypted-vector search or an enclave), which is a replacement
  of the index layer, not a change to the engine.
