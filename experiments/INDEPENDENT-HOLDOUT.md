# Frozen-engine independent quotation holdout

No engine changes. Compare the original refined retrieval and the final
opt-in augmented TOC/semantic/hybrid engine on 50 citations excluding all
200 previously tested exact quotation and template fingerprints/IDs.
Selection is fixed before retrieval: balance authors, work labels, wiki
page titles and exact MIA URLs; prefer unused wiki pages on ties. No
new source URL remains in the frozen inventory, so this is a new-quote
holdout within familiar works, not a work-independent or corpus-random
sample. No manual matches, no alternate search after review. Compare
separate retrieval caches under the same budgets, independent documentary
authentication, and blinded first-candidate semantic decisions. Report
unavailable evidence and reuse rights separately. Measure source requests,
bytes, CPU/elapsed time, local embedding inputs and review words.
No hosted inference is performed by the script. Actual ChatGPT review
token/credit accounting is not exposed to this benchmark.

## Completed independent-quotation holdout

Original run 45840438647 completed successfully. Verified report SHA-256
55d890bc4e7bcc92aa8f3ce198c7f5963706f11175d1f9010b5e9603e3c2ce3c,
sample SHA-256 d21995e1bd6fd165ca69ece369ee10f129bbd436e996b4b78ca0eb36a2722f76.
50 new exact fingerprints, 5 author labels (Kautsky 12, Lenin 12,
Trotsky 13, Luxemburg 12, Serge 1), 13 work-label groups, 45 wiki page
titles (37 unseen), 17 known source URLs. Not work-disjoint or random.

| Metric | Baseline | Integrated |
| --- | ---: | ---: |
| Authenticated first candidates | 39 | 39 |
| Complete equivalent | 8 | 7 |
| Exact boundary adjustment | 6 | 13 |
| Explicit omission adjustment | 1 | 2 |
| Edition/transcription difference | 4 | 4 |
| Incomplete corresponding passage | 3 | 8 |
| Wrong passage | 17 | 5 |
| No candidate | 11 | 11 |
| Recoverable | 15 | 22 |
| First review English words | 5898 | 5966 |
| Retrieval requests | 272 | 273 |
| Decoded retrieval bytes | 11948026 | 12000206 |
| Retrieval seconds | 255.8809 | 571.5833 |

After the original ephemeral key was no longer accessible, the new
one-off run 45852479148 reread only frozen first candidates (69 requests,
1548613 decoded bytes, 46.447 s; no candidate search or neural inference).
All 39 pairs and all 117 text fingerprints matched the original report.
The compact redacted review records decisions, exact trim/omission
fragment hashes, the deterministic partially blinded arm order and report
provenance. 14 common successes, eight gains, one loss, 27 neither.
The loss is a complete lexical passage replaced by an incomplete semantic
passage. The integrated engine improves from 30% to 44% (+14 points),
but complete untrimmed recovery alone is 8 -> 7. No new chapter routes
on this holdout. The 11 no-candidate cases are identical (10 Luxemburg
catalog-source failures, 1 Serge bibliographic lead). Local model cost:
15315 new encoded texts, 15454 chunks; no hosted model calls in retrieval.
Actual hosted review tokens/credits are unknown. The review is ChatGPT,
not independent human gold. Reuse rights unreviewed; no auto publication.

Do not generalize the earlier difficult-cohort 2% -> 38% to the corpus,
and do not change default behavior from this experiment alone. This
fresh quotation comparison confirms a meaningful semantic ranking gain
with higher cold CPU time. Preserve lexical fallback and prioritize
reusable caches / routing evidence rather than manually mapping passages.
The one-off encrypted workflows and temporary certificate are removed.
