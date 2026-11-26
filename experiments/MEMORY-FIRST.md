# Verified memory-first retrieval (opt-in)

## Cache audit at 9499541

- `LocalEncoder` already persists exact-input vectors in SQLite keyed by
  model file digests, dependency versions and exact UTF-8 text digest.
  Document changes invalidate only changed sentence inputs; mirrors with
  identical sentence text share vectors. Persist the same local cache file
  across runs rather than creating separate per-debate databases.
- `SentenceVectorIndex` keeps weighted prefix vectors in a 32-document LRU
  keyed by model, extracted text and segmentation layout. The previous
  pooled benchmark measured 5,077 SQLite hits, zero new encodings and
  identical rankings in a warm run (110.103 s cold, 1.250 s warm).
  Persisting the prefix sums as well would duplicate substantial data;
  no evidence yet of an important additional benefit. Not implemented.
- HTTP replay is reproducible but not current documentary verification.
  Create a fresh audit cache for each new verification run.
- The effective review memory currently covers 100 historical cases,
  of which 38 have accepted semantic decisions. Later experiment ledgers
  have not yet been normalized into that memory. The historical lookup was
  not called by the ordinary retrieval path.

## Adapter

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from evidence_audit import Auditor
from pilot_full import PilotClient
from review_memory import load_effective_memory
from review_reuse import MemoryFirst
from revision_receipt import RevisionRefresher

# retrieval is an existing configured Retrieval instance.
# case is a pinned inventory record including metadata and citation_ref.
with TemporaryDirectory() as fresh:
    client = PilotClient(Path(fresh), budget=200)
    engine = MemoryFirst(retrieval, load_effective_memory(),
                         Auditor(client), RevisionRefresher(client))
    result = engine.resolve(case)
```

Reuse requires a unique exact quote AND full-template fingerprint and
matching MIA links, a previously accepted latest decision, a freshly
authenticated pinned French wiki citation, and exact target document,
revision, candidate and fragment hashes. The existing independently
verified same-revision Wikirouge refresh path may be used. Changed MIA
HTML remains conservative: fallback rather than auto-accept.
The result retains separate fragments, original coordinates and metadata.
The decision is explicitly historical, not a new semantic judgment.
French archive-span revalidation, edition/translator, formatting and
rights remain separate. No automatic publication or new translation.

All other cases, including historical negative results, fall through to
the unchanged retrieval engine. A miss is not a claim that a translation
does not exist. The caller must not persist raw review text in Git or
public CI artifacts. The wrapper is a step toward a versioned batch
contract, not yet that contract or an integration in the production kit.

## Measurement

```sh
python -m unittest discover -s tests -p 'test_review_reuse.py' -v
python benchmark_memory_reuse.py
```

The benchmark uses the existing 100-case memory rather than searching for
new citations. It measures actual fresh source requests and revalidated
hits, then a second pass with the same in-run auditor. No retrieval on
misses in this isolated benchmark. Output is redacted and reports the
number of candidate words not requiring repeated semantic review, not
ChatGPT tokens or billing/quota. A fresh run still checks public evidence.
The benchmark cannot estimate hit frequency on a new debate.
