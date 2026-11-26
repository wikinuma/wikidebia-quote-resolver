# MIA review memory — 2026-11-26

The 100-case compact registry was reconstructed from four SHA-256-pinned
redacted reports and the existing committed second-pass ledger by run
45796684678. That run completed without new source queries or model calls.
The first and second review records are preserved, including rejected
candidate receipts where available. No excerpt text is stored. The
original runner digest is retained separately and checked by CI.
37 historical accepted cases, 63 unresolved; rights approved: zero.
A stored decision is not a current source check and cannot authorize
reuse. Only matching quote AND template fingerprints and explicit MIA
URLs may recover a historical decision. A new revision with changed
content requires a new decision. The historical construction workflow
is preserved under experiments/history/workflows; the active check no
longer depends on expiring artifacts. The full reports remain in the
owner's verified handoff archive.

## Bibliographic diagnosis: Plekhanov

The eight unresolved citations sharing the French source
https://www.marxists.org/francais/plekhanov/works/1904/00/plekhanov_19040000.htm
are from 1904 Geneva workers' lectures, published in Nouvelle Revue
Socialiste April–August 1926, reprinted in Pages Socialistes (1946).
The seemingly corresponding English page
https://www.marxists.org/archive/plekhanov/1897/history/part1.htm
is the 1897 review of Antonio Labriola, not those lectures (the heading
also gives the inconsistent year 1891). Do not treat it as a translation.
The National Library of Russia bibliography
https://nlr.ru/domplekhanova/40/chitalnyiy-zal separately lists the Paris
1946 French booklet and the Moscow 1946 English Fineberg translation of
the Russian work. This does not establish an English translation of
the 1904 lectures. No matching English edition verified yet; no claim
that one does not exist. Record negative work-level evidence once instead
of repeating the same title-only search eight times.

## Restored cases

Run 45797074815 verified all six exact passages through independent
pinned Wikirouge revision parses, with 6 network requests and 439623
decoded body bytes. Run 45797398853 prepared a short-lived encrypted
review, 9 requests and 445502 bytes. Its decrypted packet and all
3 French/6 English text digests were independently checked locally.
The new ledger accepts one boundary-adjusted equivalent and rejects
two wrong passages; the latter have documented correct-work leads.
The immutable 37/100 historical snapshot remains unchanged.
load_effective_memory() applies the later redacted review to yield
38/100, 62 unresolved, zero reuse approvals. All semantic decisions
remain model-assisted, not independent human labels. No plaintext or
private key is retained in Git. Temporary workflow/certificate removed.

## Offline research queue

`python work_queue.py --out reports-live/mia-work-queue.json` excludes the
38 historically accepted cases, retains every unresolved case and groups by
exact author, work, article and MIA URL set (not inferred title equivalence).
`work-research.json` records reusable bibliographic leads and exclusions,
without suppressing any unresolved case or approving any quote. The Victor
Serge essay has several printed English-edition leads, but its exact text and
reuse rights remain unverified. The queue is planning only and makes no
network or model calls.
