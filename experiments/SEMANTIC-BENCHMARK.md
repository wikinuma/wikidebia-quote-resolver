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
