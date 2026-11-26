# MIA-only experiment handoff — 2026-11-26

## Current result

Frozen cohort: 100 French Citation templates whose own lien points to marxists.org, two 50-case cohorts, the second with new quote fingerprints but familiar work groups. 29 complete or adjusted equivalents in the first review, recorded and reread in run 45719477640; second-pass run 45720424112 produced 44 authenticated candidates for 25 of 29 selected cases. An earlier chat reported 11 further equivalents provisionally, without a saved per-case registry.

Fresh second-pass run 45772180576 (187 tests in the same source tree, workflow succeeded) produced 38 candidates for 22 of the 29 targeted cases. Three formerly ready cases (two Engels Trades Unions, one Condition of the Working Class) failed strict target span verification. The exact reason for the document mismatch has not been diagnosed; do not silently relax the hash check. The fresh packet was decrypted in the current session using an ephemeral locally generated key; all 38 candidate text hashes, all 22 available input text hashes, all 29 case identities and the public report were checked locally. The full plaintext packet and private key are not committed. The temporary certificate/workflow change is removed from the current branch tree.

The second review ledger records 6 complete, 1 boundary adjustment, 1 explicit omission adjustment, 12 edition variants, 1 wrong passage, 1 partial, 4 no replacement and 3 not reproduced. Thus 8 additional complete/adjusted equivalents, 9 exact text fragments. Combined result: 37/100, not the provisional 40/100. No rights approval. All decisions are from one model-assisted editorial review, not independent human gold labels.

## Evidence references

- Paired MIA benchmark: run 45713244614 / artifact 15069630067; paired-index.json SHA-256 c800e9e37586d760a10c14bbb037e6e4a493fd5c130694b9723914678812ecea.
- Frozen development and familiar-work holdout: run 45715249856 / artifact 15070262046; refined-index.json SHA-256 368a2ad617ab504838634dbf328ef4776a25bda9b45ae13d9c7061d6c24bedfd; holdout-index.json SHA-256 0a1d137fa16b1ff15e897e8d75fe6d37aa89a02adad32f9b5c8f24799f5ed18b.
- Evidence audit: run 45716912994; 164 authentic English candidate excerpts, not semantic verification.
- First chapter audit: run 45718562215 / artifact 15070548783.
- Extended chapter audit: run 45719728379 / artifact 15071956069; chapter-quality.json SHA-256 ec70f3aba700147d8da2848195ff3e55379d35c74bcc18e85cb62e5aca8e2de8.
- First review registry: run 45719477640 / artifact 15072030637; review-registry.json SHA-256 262a6b5e6c941266fd91a4b5eae78c6c3011ca97aef8a0fbf7ba808aedff8bc2.
- Original second pass: run 45720424112 / artifact 15072590018.
- Fresh second pass: run 45772180576 / artifact 15094442617; second-pass.json SHA-256 5dea50b0407103fa08338a1ffa4756c821a92463d04906b7e6f4ca01655937be.
- second-pass-review-ledger.json binds decisions to that fresh report using case IDs, candidate hashes and Unicode-codepoint relative fragments. Full local registry SHA-256 d056b92dfac446f5d84323bcfd989e33073d8b5021b281298d1e0f1e4883eaf6. A local handoff archive preserves reports beyond artifact expiration.

## Next priorities

1. Verify and reconcile the stacked experimental PRs #3–#8 before merging into experiment/pilot-v04 (never production or wiki writes). The legacy public pilot previously failed for documentary incompleteness; distinguish old failures and real regressions.
2. Avoid rerunning the entire cohort. Reuse the decision registries, diagnose the three nonreproduced cases, investigate unresolved cases by work/edition, and reserve AI for compact uncertain review packets. A failed HTML hash is not permission to accept changed text; explicitly verify stable document revision and exact span or mark changed/unavailable. Review license/edition separately.
3. Compare passage coverage, verified-equivalence rates, false positives, review words, actual measured AI tokens if available, network requests/decoded bytes and duration on a genuinely work-disjoint MIA holdout if possible. Do not equate review words with quota.
4. Persist bibliographic/document caches and implement bounded incremental Wikirouge index updates using public MediaWiki revisions, no SSH or mass re-export. Keep original snapshot provenance.
5. Propose production architecture only; no integration into wikidebates/wikidebia-project, no wiki edits, no generated translations presented as published.
