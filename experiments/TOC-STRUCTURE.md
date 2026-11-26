# Second TOC parser experiment

The first probe showed an unexpectedly low count of parsed chapters on
some actual MIA tables. Separate file links from intra-file fragments:
multiple links to different internal sections no longer invalidate the
unique full-file chapter heading. Recognize isolated legacy table
markers, allow actual same-directory file links, never synthesize URLs.
Retain the original collector's position separately from explicit file
order. Multiple different full-file labels still cause abstention.

An additional opt-in hypothesis uses equal contiguous gaps of at most
four intervening entries between consecutive strong anchors. Require a
weak heading similarity floor and sampled document corroboration;
otherwise abstain. It is not allowed to extrapolate beyond anchors.
No integration in retrieval or acceptance until the results warrant it.

Run both algorithms against the same historical labels and new URLs
with a shared HTTP cache. Expected English child URLs are evaluation
labels only. CPU and network metrics of the second run are warm-cache
metrics and not a fair cold-cache speed comparison.

## Completed same-response comparison

Run 45832355291, artifact 15122698994. Original report SHA-256:
cbca2c457e033bf3af0a0cdeaa8a7b9dcbc3e84d5326c5e2b35489b040085e8b.
New report SHA-256:
68b2e31ec93a1771dfd155fa3d3310d68f04d058c506f5a22be8b0846ea1f7ea.
Both downloaded and verified. Historical labels: 4/14 correct originally,
5/14 after change, zero wrong in both, 3 vs 4 additionally corroborated.
Novel URLs: 5/14 vs 8/14 content-supported candidates, all outside the
ordinary collection budget. Relative increase 60%; abstentions retained.
The original five cover 20 of the 50 novel-URL quotations; the eight
cover 30/50, but four of those 30 still lacked French source localization.
These are document opportunities, not verified quotation equivalences.
Original run 36 requests, 1,423,005 decoded bytes, 30.193 s. Second run
12 additional requests, 786,285 bytes, 13.893 s with warm shared caches,
113 newly encoded texts, zero hosted calls. Do not compare these timings
as independent cold runs. Optional parser retained pending end-to-end
evaluation; it is not yet the default matcher in the integrated engine.
