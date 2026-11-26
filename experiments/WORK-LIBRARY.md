# Persistent work library (experimental)

The active citation-level memory and its reuse shortcut have been removed.
Historical cohort identities and evaluation labels remain frozen under
`data/mia/benchmarks/20261126/` only. They are not imported by retrieval.

## One shared library for all debates

```sh
python auto_retrieve.py --cases-file pinned-cases.json --refined \
  --work-library private-cache/works.sqlite3 --cache private-cache/http \
  --budget 400
```

The input has a `cases` array with pinned wiki title, ID and citation_ref
records. The original Citation template is fetched for each quote. The
existing incremental Wikirouge index and the manual work/chapter catalogs
are used by default in library mode. The library records work title and
author, attested French/English work URLs, edition/date/translator fields
when present, revision and documentary provenance. A route learned from
the index is tied to its fingerprint; live routes require reciprocal
links and exact MIA source evidence. Conflicting, ambiguous, stale or
unsupported records fall back to ordinary discovery. Unseen source URLs
are not inferred from a similar-looking URL. Revalidation is still
needed before publication.

For the optional experimental local model add
`--model-dir PATH --vector-cache private-cache/vectors.sqlite3`.
A successfully corroborated chapter hypothesis is retained by root and
chapter URLs, headings, exact TOC and chapter document hashes, and
matcher identity. It may skip TOC ranking and the content probe only
when those bodies still match. The new French passage must be found and
the new English candidate ranked on every invocation. There is no
semantic decision cache for individual quotations.

The same local database paths can be reused across debates and process
restarts. HTTP and vector caches remain separate and local; actual
documentary verification uses fresh source reads. No automatic Git
commit, publication, rights approval or new translation. Strict ordered
HTTP replay cannot be combined with a mutating work library.

Export the metadata-only view, including the existing 14 researched
bibliographic notices, manual chapter catalog and work-level research notes:

```sh
python work_library.py --database private-cache/works.sqlite3 --export private-cache/works.json
```

The SQLite file is operational state, not a new quotation database.
Metadata export is for review/backup; no unchecked external import.
Unknown publication information is not invented; rights remain unreviewed.

## Test plan

The targeted unit tests cover persistence across process boundaries, exact
work identity, stale and conflicting mappings, snapshot changes, metadata
projection, fetching the new citation, and chapter reuse only with matching
document evidence. The live benchmark selects two different frozen French
citations per documented work before querying, runs both arms with fresh
HTTP sessions, and compares work routes. It separates indexed and live
navigation. This does not establish end-to-end passage accuracy or quota
savings; a separate benchmark will be needed for those.
