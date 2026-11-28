# Consolidation and focused follow-up (2026-11-26)

All 17 stacked commits through 6153073 are present on experiment/pilot-v04
(non-forced fast-forward from 55eba8c). PR #3 merged; #4–#8 closed as
ancestors, with explanatory comments. Baseline run 45710555772 and
closure run 45772638946 both passed unit/navigation/inventory tests;
the historical live pilot was incomplete in both, with a matching
zero-network offline replay. No test gate was relaxed.

The supplied wikidebia-reprise-mia-20261126.zip (SHA-256
fecf5a232c197ec4480e7519c04d0e37981866ba601aead730ad412d6f38c7bc)
was read locally: all 11 listed files match sizes and SHA-256.
The reported result remains 37/100 (29 first + 8 second review).
Unresolved latest decisions: 30 no primary candidate, 24 edition
variants, 4 no new candidate, 3 not reproduced, 1 wrong passage,
1 partial. These are model-assisted editorial judgments, not gold labels.
No reuse approved. The three nonreproduced cases refer to six earlier
candidate receipts in three English Wikirouge documents; the small
fixture was extracted from the verified original second-pass report.
The diagnostic reruns just these documents, not the 100 quotations.

The public recipient certificate and the two encrypted review workflows
were still present at 6153073, contrary to the handoff. Archive the
workflow definitions verbatim under experiments/history/workflows; remove
the obsolete public certificate from the active tree. A new encrypted
review requires a new recipient certificate supplied by the reviewer;
no plaintext review packet or private key belongs in GitHub. Old
experiment workflows reference expiring artifacts; their historic
results are preserved by the user's handoff archive. Do not assume
those artifact URLs remain available. Legacy pilot remains strict and
can be run separately; metadata-only changes no longer trigger it.
No production or wiki writes. No new benchmark or equivalence claim yet.
