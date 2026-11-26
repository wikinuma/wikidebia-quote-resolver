# Novel MIA source URLs (2026-11-26)

Reuse the hash-pinned complete inventory of 601 Wikidebats pages, not a new
crawl. The first three frozen cohorts contain 150 distinct quote hashes
and cover 50 of 64 exact source URLs. After excluding all their quote,
template and case fingerprints and exact source URLs, 152 distinct eligible
quotes remain on 14 previously unseen URLs. Select 50 by deterministic
round robin across source URLs before retrieval, rather than by outcomes.
These are new documents/links, not necessarily new works or authors.

An opt-in semantic sidecar searches the same already-collected target
documents as the baseline. It does not change routing, baseline packets,
request budgets, or default behavior. No new manual catalog mappings.
If the full collected scope is too large, report the restricted candidate
document scope rather than silently truncating it. The independent audit
checks exact source bytes/revision/span, not semantic equivalence. Report
candidate agreement, words, route coverage, runtime, network costs and
local encoder work; no hosted model calls. Do not infer false-positive
rates, exact translation success or reuse rights from authentication.

## Completed comparison

Run 45828708877, artifact 15121037396. Sample SHA-256
19020f67ee6fb3b31ec292082a6ec9143d287a828c5c3215e929bee000c30f1b;
report SHA-256 15f6cfdf923dcd380b595fb65f9890d545e4fa194abc069d14ee37f38bcfd97c.
50 fresh quote fingerprints, 14 novel URLs, but only two author labels.
Both arms: 39 authenticated/ready, 11 without candidate. Review words
5526 baseline vs 4998 semantic (-9.55%). Agreement: 29 different document
or revision, 7 disjoint spans in same document, 2 overlapping spans,
1 same exact text, 11 neither ready. None of these establish equivalence.
135 retrieval requests, 6,126,561 decoded bytes; 70 audit requests,
2,282,340 decoded bytes. Semantic sidecar 140.445408 seconds, 7195 new
encoded texts, 7241 chunks, 23 document builds and 480 hits. 36 cases
searched all collected docs, 3 restricted, 11 not attempted. Overall
407.916 seconds. No hosted model calls, equivalence and false positives
remain unmeasured. All four workflows passed. Retain opt-in sidecar;
next compare early chapter routing against this frozen sample.
