# Automatic bilingual TOC hypotheses

A new source URL in a late chapter may be outside the current 16-document
retrieval budget. Numbering is not stable across editions. Instead use
explicitly linked chapter headings in independently identified work roots,
local multilingual embeddings, mutual nearest-neighbour margins and
monotone corroborating pairs. No manually added work or chapter mapping.
The probe evaluates 14 historical chapter labels after matching, and
all 14 novel exact source URLs using only automatic index metadata for
their roots. Independently sample actual source/target document text at
fixed positions for corroboration; this remains a hypothesis, not semantic
equivalence, publication or rights approval. Only real HTML link targets,
never synthesized URLs. This standalone experiment does not change the
retrieval engine; integrate only if it produces useful accurate routes.

## Completed probe

Run 45829168427, artifact 15121366747, report SHA-256
929ce314b6448a5d8439a08eb467c02bada00ea55658bce11a62cc2deb678697.
14 historical labels: 4 correct candidate routes, 10 abstentions, 0 wrong
routes; 3 correct additionally passed independent sampled-content probe.
14 new exact source URLs: 5 candidate routes, all 5 sampled-content
supported and beyond the first 15 TOC links; 8 no supported chapter,
1 no unique indexed root pair. 36 requests, 1,423,005 decoded bytes,
29.076 seconds, 269 unique texts encoded, 375 chunks, 0 hosted calls.
This is a useful optional route for otherwise out-of-budget chapters,
not evidence of general high recall or recovered quotations. The
novel routes were not manually supplied to the matcher. Default
retrieval unchanged; next step is guarded early route + fallback and
same-sample comparison, preserving all historical mappings.
