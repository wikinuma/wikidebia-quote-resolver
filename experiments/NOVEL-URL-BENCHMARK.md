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
