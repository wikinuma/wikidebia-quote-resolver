# Experimental pooled multilingual passage retrieval

Compare three algorithms on exactly the same known English document, using
38 historical accepted MIA-linked quotations as frozen evaluation labels.
The accepted English offsets and fragment text are never ranking inputs.
Revalidate the pinned French text and English candidate/fragment evidence;
report unavailable labels, never silently reduce the denominator.

This deliberately isolates passage search from the unsolved task of
bibliographic routing. It cannot measure end-to-end recall or human-verified
precision. The labels are historical model-assisted decisions. Compare
lexical retrieval, a sentence-vector index and the original direct window
embeddings on a cost-limited subset chosen by stable ID and window count.
Measure coverage, character IoU, exact boundaries, review words, local
encoder work, cold/warm time, source requests and bytes. Omitted ranges
penalize precision. No generated text, hosted inference, or rights approval.

The optional model is a pinned, public Apache-2.0 multilingual MiniLM
snapshot (~471 MB weights); only safetensors and tokenizer/config files are
downloaded. The model is executed locally on CPU, with an offline encoder.
Initial model download and CPU inference are real costs, separate from the
OpenAI quota. The existing exact-text SQLite vector cache enables reuse.
Sentence pooling approximates the whole-window embedding and must earn
its use through paired results. Do not enable in the default engine yet.

## First completed result (2026-11-26)

Run 45819797551, artifact 15117123248, report SHA-256
7d61af8ef16cab83a0e97cc589ca7822f117784d2240b76fd3604a6c83896927
(downloaded and hash-checked). 34/38 labels revalidated; four failed strict
label revalidation. Model fingerprint a451afeac6083a6697860afb2997245ae988e62870744ddfc518e6061ca013f2.

| Metric (34 revalidated, known document) | Lexical | Pooled |
| --- | ---: | ---: |
| Top1 full coverage | 24 | 27 |
| Top1 character IoU >= .8 | 18 | 24 |
| Top1 exact single fragment | 10 | 14 |
| Mean top1 IoU | .632900 | .855354 |
| Top1 review words | 5866 | 4969 |
| Cold search seconds | 7.923 | 110.103 |
| Warm search seconds | not measured | 1.250 |

Across 34 eligible cases, original direct-window encoding would need 21,732
distinct window texts vs 5,025 distinct sentence texts (76.88% fewer);
this is input-count arithmetic, not measured inference time at equal scope.
Actual direct-window comparison was limited to four cases: top1 IoU>=.8
3/4 in both methods. The pooled warm replay encoded zero texts, with 5,077
cache hits; all rankings identical. Source retrieval: 60 requests, 1,486,771
decoded bytes. No hosted inference. Local CPU/model download are real costs.

The pooled method is worth retaining as an optional experimental ranker.
Lexical top5 full-cover recall was higher (32 vs 28); preserve fallback.
No default activation, no independent evaluation of work discovery, false
positives, equivalence on fresh cases or reuse rights.
