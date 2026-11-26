# Incremental Wikirouge index

The original verified snapshot is immutable. Its recorded export start is
2026-11-25T18:11:44.460583+00:00; the first window starts at the beginning
of that second. Run 45803291710 (artifact 15108721415) produced
`deltas/0001.json`, verified byte-for-byte against the reported SHA-256.
The public recentchanges feeds contained no events in that interval;
4 HTTP requests, 513 decoded bytes, 1.952 seconds of collection. No model
calls. The first delta is a checkpoint, not a re-export.

To read the base plus verified deltas:

    from wikirouge_incremental import open_current
    with open_current() as index:
        result, route = index.route(metadata)

To collect the next delta (no writes to either wiki):

    python wikirouge_incremental.py --history data/wikirouge/deltas/manifest.json --out reports-live/next-delta.json

After inspecting its results and verifying its SHA-256, append the immutable
delta file and digest to the history manifest in a reviewed commit. A failed
or partial collection must not advance checkpoints. Each window is inclusive
at the previous checkpoint, and touched pages are re-read by identity.
A missing page, move, or exit from text categories is represented explicitly.
Bilingual pairs and degrees are recalculated locally using the original
builder's semantics; ambiguous links are retained. The original archive
remains available for reproducible benchmarks.

Limitations: direct public recentchanges are not a complete guarantee of
indirect template/category changes, asynchronous MediaWiki link updates, or
hidden revisions. Template/module edits, retention gaps, unaccounted IDs,
excessive windows, and inconsistent reads block advancement. Metadata for
changed pages is extracted from recognized templates and the original
field-name vocabulary; this is a candidate index, never semantic evidence.
No full-text corpus or credentials are stored. No SSH is required.
The first live window is empty; edits/deletes/moves have regression coverage,
and a separate four-page read probe checks the real metadata API.
