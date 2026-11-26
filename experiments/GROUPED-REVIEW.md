# Private grouped citation review

Experimental, read-only retrieval handoff. Reuses the existing candidate queue,
Auditor and CitationRegistry. The input must come from a hash-pinned MIA cohort
report. The source template is independently re-read at its pinned wiki revision,
and routing is checked against its literal link (not report tags).

Prepare locally, with a private path outside Git or under the ignored .cache:

    python grouped_review.py prepare --report REPORT.json SHA256 --out .cache/review-batch

Keep manifest.json private and unchanged; send only review.json to the reviewer.
The latter groups exact authors/work metadata and chapter URL/article; identical
source identities across pages and identical candidate spans are deduplicated.
English document URLs and passages appear once. Review candidates are limited to
four distinct verified spans per source, with existing ranking preferred.
No candidate or unavailable evidence is not proof that a translation does not
exist. The tool never searches non-MIA-linked citations. It may use a supported
English archive already reached through a MIA-triggered route.

The return contains schema, packet_sha256, reviewer and decisions. Each decision
contains id, decision, candidate and ranges only. Use half-open Unicode character
offsets relative to the exact supplied candidate, not to the whole document.
Do not recopy passages. For uncertain/no_equivalent set candidate=null and
ranges=[]; these decisions do not create positive receipts.

    python grouped_review.py apply --packet .cache/review-batch/manifest.json --packet-sha SHA256_FROM_PREPARE --reply PRIVATE_REPLY.json --db PRIVATE_WORK_LIBRARY.sqlite3

Import requires the independently retained preparation digest, exactly one
decision per offered source, candidate membership and fresh verification of the
pinned citation and exact target bytes/coordinates before recording. Stale
evidence is rejected. Registry writes remain idempotent. No text in public
reports; the local review packet contains private quotation text and must never
be added to Git. No rights approval, automatic publication or Codex integration.
The packet size cap rejects rather than truncates; split large cohorts before
preparation. The CLI currently accepts existing refined reports, not a live
854-quotation scan. Historical decisions are not automatically imported.
