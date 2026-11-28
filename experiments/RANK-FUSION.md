# Candidate ranking fusion

A local hash-checked retrospective analysis of run 45819797551 used
the same 34 revalidated known-document cases, with no new queries or
inference. Interleave semantic and lexical ranks, semantic first,
deduplicating only identical document/span evidence and retaining the
first five. Never use expected spans for candidate ordering.

Top5 full-cover: lexical 32, semantic 28, interleaved 32.
Top5 IoU >= .8: lexical 22, semantic 24, interleaved 28.
Top5 exact single fragment: lexical 13, semantic 14, interleaved 18.
Top5 review words: lexical 32122, semantic 24900, interleaved 28417.
Top1 remains semantic. The union of both full lists covers 33, but
requires more review; do not interpret either as automatic publication.
The heuristic is exploratory on a historical corpus, not an independent
holdout result. The helper canonicalizes exact source spans before
combining raw candidates and leaves the original lists available.
