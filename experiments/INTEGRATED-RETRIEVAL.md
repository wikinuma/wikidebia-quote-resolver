# Integrated experimental retrieval

Opt-in only. A verified indexed root pair and uniquely supported French
citation span permit a TOC hypothesis for an explicitly linked late
chapter beyond the ordinary page limit. Require mutual heading and
monotone support plus sampled bilingual document corroboration and
exact source/target digests. Reuse content probes within the run.
Speculative routing gets at most four extra network requests while
reserving eight for ordinary fallback. Existing catalog routes retain
priority. Failure or lack of a semantic candidate reverts to ordinary
retrieval. Hybrid first-review packet is opt-in; all other candidate
lists remain intact. No publication approval. A rejected hypothesis
can be retried with the experimental flags disabled.

Paired comparison on exactly the prior frozen 50 novel-URL cases.
Independent retrieval caches and identical request limits; shared
independent audit cache. Candidate authenticity, newly reached chapters,
review words and actual network/CPU work are measured. No inference
of equivalence or precision without independent review. All previously
studied source URLs remain represented, but this is a development
cohort, not an independent generalization test. Default behavior unchanged.

## Completed paired retrieval and qualitative review

Run 45831925252, artifact 15123201287, verified report SHA-256
92d8d79ee43ec2ed5b5604e5175bbc3b8d9deb96fa206ea0720aae5ac941c7ca.
Both arms 39/50 authenticated first candidates, 11 without candidate.
Aligned candidates: 2 -> 18. 16 authenticated early routes reached
documents absent from the baseline collection. First-pass words:
5526 -> 4886 (-11.58%); requests 135 -> 140; decoded bytes
6126561 -> 6508762; retrieval seconds 161.7458 -> 320.6342.
No hosted model calls. Cold neural inference is a real CPU cost.

A separate encrypted review packet from the frozen report reauthenticated
all 39 paired French citations and 78 first candidates: run 45833742340,
60 requests, 1390613 decoded bytes, 40.59 s. No replacement search.
Locally verified all text hashes and reviewed the X/Y texts without arm
labels, then restored the arms. Compact redacted decisions are in
integrated-paired-review.json. Counts over the complete 50-case cohort:

| Decision | Baseline | Integrated |
| --- | ---: | ---: |
| Complete equivalent | 1 | 9 |
| Complete after boundary trim | 0 | 4 |
| Edition/translation difference | 1 | 5 |
| Incomplete corresponding passage | 0 | 5 |
| Wrong passage | 37 | 16 |
| No candidate | 11 | 11 |
| Recoverable (first two rows) | 1 | 13 |

12 gains, zero losses, one common success. Ten gains are among the 16
early chapter routes; two others are ranking gains. A gain from 2% to
26% on this intentionally difficult novel-URL development cohort is
not an independent overall corpus recall estimate. Human gold, actual
AI review token costs, and reuse rights remain unestablished. Model-
assisted assessment excludes incomplete passages and significant
translation/edition differences. The new TOC structure parser (PR 23)
was separately validated and is not included in these integrated results.
