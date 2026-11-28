# Exhaustive MIA citation inventory

The former benchmark inventory sampled distinct works and bounded quotation
lengths. It must not be used as a complete processing queue.

Run (from the experimental resolver checkout):
    python citation_inventory.py --inventory .cache/mia-inventory.json

This read-only command enumerates the same raw template scope as the domain
census, pins each occurrence and emits every supported literal MIA citation
as a case in the existing auto_retrieve --cases-file format. No popularity,
author, length or per-work filter is applied. The cases are ordered by
document URL to favour alignment reuse. Invalid citations and aliases that
the current resolver cannot identify are reported separately. The full
manifest is private operational state and contains no quote text.

On subsequent runs the unchanged page revisions are reused without downloading
their raw content. Modified pages and moved titles are parsed from explicitly
pinned revisions, new pages are added, and deleted occurrences disappear from
the current inventory. The distinct source-key changes distinguish new
content from mere movement or parameter-order changes. Existing registry
receipts are never deleted by the inventory. An incomplete scan retains the
old manifest; it cannot claim deletions. A changed parser or routing algorithm
invalidates the revision-reuse shortcut. The scan is non-atomic, as the wiki
can change during enumeration.

This is only the preparation queue, not a bulk retrieval or AI translation
campaign. First measure the real-debate kit integration before generalizing
retrieval. The numbers count raw occurrences, not guaranteed translations.
