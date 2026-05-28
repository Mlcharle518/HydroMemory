# Hydro Query Language (HQL)

HQL is a small, conjunction-only DSL over the droplet store (PRD §13). It is
implemented in `hydromemory/hql/` as a hand-written lexer
(`lexer.py`), a recursive-descent parser (`parser.py`), an AST (`ast.py`), and an
executor (`executor.py`). `parse(text)` builds a `Query`; `execute(query, repo,
...)` runs it.

```python
from hydromemory.hql import parse, execute, compile_precipitate
query = parse('GET memories WHERE reservoir="groundwater" AND purity>0.8')
```

---

## 1. Grammar (EBNF)

The grammar below matches the real parser. HQL is whitespace-insensitive between
tokens (newlines included) and is **conjunction-only** — the only connective is
`AND`.

```ebnf
query       = verb , target , [ where ] , [ group_by ] , [ output ] ;

verb        = "GET" | "PRECIPITATE" | "FILTER" | "DISTILL" ;
target      = IDENT ;                         (* e.g. "memories" | "cloud" *)

where       = "WHERE" , predicate , { "AND" , predicate } ;

predicate   = field_path , ( op , value
                           | "(" , value , ")" ) ;   (* call form *)
field_path  = IDENT , { "." , IDENT } ;
op          = "=" | ">" | "<" | ">=" | "<=" | "!=" ;
value       = STRING | NUMBER | IDENT ;       (* IDENT "true"/"false" -> boolean *)

group_by    = "GROUP" , "BY" , IDENT ;
output      = "OUTPUT" , IDENT ;
```

Lexical tokens (`lexer.py`): `IDENT` (bare words; keywords are just identifiers
the parser recognises by position), `STRING` (double-quoted, supports backslash
escapes), `NUMBER` (int or float), `OP` (`=`, `>`, `<`, `>=`, `<=`, `!=`; `==`
is accepted and normalised to `=`), `DOT` (`.`), `LPAREN`/`RPAREN`, and `EOF`.

Keywords (case-insensitive, uppercased internally): the four verbs plus `WHERE`,
`AND`, `GROUP`, `BY`, `OUTPUT`. A keyword may not be used as a `target` noun. Any
trailing token after a complete query is a syntax error. An empty query, an
unknown verb, a missing operator, an unterminated string, or an unexpected
character all raise `HQLSyntaxError`.

### Predicate forms

A `predicate` is one of:

- **`field op value`** — the common form, e.g. `purity > 0.8`,
  `reservoir = "groundwater"`, `phase = "polluted"`. Parsed as
  `Predicate(field, op, value)`.
- **`field.path("arg")`** — the function-call form, used for
  `permission.allows("agent")`. Parsed as
  `Predicate(field="permission.allows", op="call", value="agent")`.

Recognised predicate fields (from the executor):

| Field | Where it is handled | Notes |
|-------|---------------------|-------|
| `reservoir` | repository filter (op `=`) | Coerced via `normalize_reservoir`. |
| `type` / `memory_type` | repository filter (op `=`) | Both map to the repo's `memory_type` kwarg. |
| `phase` | repository filter (op `=`) | Coerced to a `Phase`. |
| `purity` | repository filter (`>`/`>=`) + post-fetch | `>=` sets the repo `min_purity` floor; strict `>` also re-checks post-fetch. |
| `topic` | query-level (op `=`) | Matched against `semantic_tags`, `meta["context"].topic`, and content. |
| `permission.allows` | post-fetch (op `call`) | Public droplets pass; otherwise the agent must be in `allowed_agents` (or the list is empty). |
| `related_to` | post-fetch | Matches a semantic tag, a linked id, the context topic, or a substring of content. |
| `minimum_purity` | post-fetch | `state.purity >= value`. |
| `maximum_privacy_risk` | PRECIPITATE JSON op | Carried into the compiled op (see §3). |
| `theme`, `trigger` | PRECIPITATE JSON op | Carried into the compiled op; tolerated (always pass) as post-fetch filters. |
| generic state float | post-fetch | Any `State` attribute (e.g. `confidence`, `gravity`) is compared numerically. |

Comparison operators on numeric fields evaluate as expected: `=` (equality),
`>`, `<`, `>=`, `<=`, `!=`. An unsupported field raises `HQLSyntaxError` at
execution.

---

## 2. Statement verbs and how the executor maps them

`execute(query, repo, *, recall=None, verbs=None)` dispatches on the verb.

### `GET`

Runs `repo.query(**repo_kwargs)` using the indexed dimensions
(`reservoir` / `memory_type` / `phase` / `min_purity` / `topic`), then applies
the remaining predicates as **post-fetch filters** (`permission.allows`,
`related_to`, `minimum_purity`, strict `purity >`, generic state-float
comparisons, and a topic match). Returns the matching `list[Droplet]`.

`compile_filters(query)` performs the partition into repository kwargs versus
post-fetch predicates; `_execute_get` runs the query and filters.

### `PRECIPITATE`

Compiles the query to the §13 JSON op via `compile_precipitate(query)`. If a
`recall` callable is supplied, the op is handed to it and its result is returned;
otherwise the compiled op dict itself is returned. (At the engine level, the
recall callable runs the `Verbs.precipitate` recall path described in the verb
reference.)

### `FILTER`

Requires a `Verbs` instance. Runs the same `GET` selection to find matching
droplets, then calls `verbs.filter(d)` on each (delegating to the contamination
module) and returns the list of filtered droplets. Raises `HQLSyntaxError` if no
`Verbs` instance was supplied.

### `DISTILL`

Requires a `Verbs` instance. Runs the same `GET` selection to gather the cluster,
then calls `verbs.distill(matches)` to extract a single principle droplet (see
the verb reference). Returns `None` if nothing matched. Raises `HQLSyntaxError`
if no `Verbs` instance was supplied. Note: `GROUP BY` and `OUTPUT` parse and are
preserved on the `Query`, but the executor distills the full match set into one
principle (the grouping/output spec is descriptive metadata, not a separate
execution path).

---

## 3. The `PRECIPITATE` JSON op

`compile_precipitate(query)` lowers a `PRECIPITATE` query to the §13 JSON
operation:

```json
{
  "operation": "PRECIPITATE",
  "query": {
    "theme": "...",
    "trigger": "...",
    "reservoirs": ["..."],
    "minimum_purity": 0.0,
    "maximum_privacy_risk": 0.0
  },
  "output": {
    "mode": "behavioral_guidance",
    "include_explanation": false
  }
}
```

Mapping rules:

- `theme` and `trigger` predicates copy their value into `query.theme` /
  `query.trigger`.
- A single `reservoir = "..."` predicate becomes `query.reservoirs` as a
  one-element list; a `reservoirs` predicate is used as-is (list or single value).
- `minimum_purity` copies directly; otherwise a `purity > / >=` predicate seeds
  `minimum_purity`.
- `maximum_privacy_risk` copies directly.
- `output` is fixed to `{"mode": "behavioral_guidance", "include_explanation":
  false}`.

Only the keys present in the query appear under `query` (it starts empty and is
populated as predicates are found).

---

## 4. Example queries (PRD §13)

The four canonical §13 queries. Each parses against the real grammar (verified
with `hydromemory.hql.parse`).

**1. `GET` — typed, high-purity, agent-permitted groundwater memories**

```sql
GET memories
WHERE reservoir = "groundwater"
AND type = "communication_preference"
AND purity > 0.8
AND permission.allows("assistant")
```

Fetches persistent communication-preference memories with purity above 0.8 that
the `assistant` agent is allowed to access. `reservoir`/`type` are repository
filters; `purity > 0.8` floors `min_purity` and is re-checked post-fetch;
`permission.allows("assistant")` is the call-form post-fetch gate.

**2. `PRECIPITATE` — recall a cloud pattern by theme and trigger**

```sql
PRECIPITATE cloud
WHERE theme = "user cognitive style"
AND trigger = "system architecture request"
```

Compiles to a `PRECIPITATE` JSON op with `query.theme = "user cognitive style"`
and `query.trigger = "system architecture request"`, then runs the recall path
to surface the relevant abstracted pattern.

**3. `FILTER` — clean polluted memories tied to user identity**

```sql
FILTER memories
WHERE phase = "polluted"
AND related_to = "user identity"
```

Selects `polluted`-phase droplets related to "user identity" (`phase` is a repo
filter; `related_to` is a post-fetch filter), then runs `verbs.filter` on each to
clean / verify / reconcile them.

**4. `DISTILL` — extract a principle from a topic cluster**

```sql
DISTILL memories
WHERE topic = "AI memory"
GROUP BY pattern
OUTPUT principle
```

Gathers memories on the "AI memory" topic and distills them into a single
high-purity principle droplet (landing in the `sacred` reservoir). The `GROUP BY
pattern` and `OUTPUT principle` clauses parse and are preserved on the `Query`.

### Verifying the examples

Each example can be re-verified from a shell:

```bash
./.venv/Scripts/python.exe -c "from hydromemory.hql import parse; print(parse('GET memories WHERE reservoir=\"groundwater\" AND purity>0.8'))"
```

This prints a `Query(verb='GET', target='memories', predicates=[...])` value
rather than raising `HQLSyntaxError`.
