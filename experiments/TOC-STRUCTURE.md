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
