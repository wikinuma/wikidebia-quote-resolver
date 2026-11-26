# Historical favourable citation decisions

This targeted audit reuses the two frozen cohorts, the original revalidated
first registry, and the fresh second-pass report. The original artifact
hashes and cross-links must agree. Exactly 29 + 8 + 1 favourable historical
decisions (37 distinct cases) are considered. No new searches or model calls.
A valid literal MIA link and pinned raw citation are required for each.

Historical candidates and all reviewer-selected fragments are re-read and
verified. On a positive Wikirouge revision only, byte-identical candidate and
fragment text at the same coordinates may acquire a new exact proof if the
HTML wrapper has changed: the audit records both body hashes and marks the
new proof separately. Changed revision, missing document, changed passage,
or changed MIA bytes without a revision all require a new review. A narrow
documented Lenin edition mirror is supported, not arbitrary colon titles.
The general historical proof and strict span verification remain unchanged.

Run locally with the three original Actions artifact archives extracted
and the private WorkLibrary path:

    python historical_citations.py --development frozen/reports-live/refined-index.json --holdout frozen/reports-live/holdout-index.json --first-registry registry/review-registry.json --second-report second/second-pass.json --db .cache/works.sqlite3 --out .cache/historical-audit.json

The run records old editorial decisions, **not fresh independent semantic
reviews**. Existing CitationRegistry rights and conflict safeguards apply.
CI uses a temporary private DB and uploads only a text-free audit report;
to seed a persistent local DB run the command against that DB.
The output differentiates decisions, distinct cases, source keys and
nonconflicting lookups. It never declares publication or reuse authorized.
