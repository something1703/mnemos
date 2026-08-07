<!-- AGENTS.md: PRs are per sub-phase and link the phase they close. -->

**Phase / sub-phase:** <!-- e.g. Phase 03.4 — forget() -->

## What this changes

<!-- One paragraph. What a reviewer needs to know before reading the diff. -->

## Which invariant does this touch?

<!-- Required. Answer "none" only if you are certain. The five are in AGENTS.md.
     1. No LLM-driven process holds DELETE or governance privileges
     2. Every state-changing memory op appends a hash-chained audit row in the same txn
     3. No fact becomes recallable without provenance to at least one episode
     4. Memory rows never leave their home region
     5. Erasure is atomic across rows, vectors, and provenance — or it does not happen -->

## Evidence

<!-- Acceptance criteria in the phase files ARE the tests. Name the test that
     proves this works, not the manual check you did. -->

- [ ] Tests land with the code and name what they prove
- [ ] `make check` green locally
- [ ] If this touches destruction, residency, or the ledger: a test that tries
      to violate the relevant invariant and fails
- [ ] If this deviates from the phase file: an ADR in `docs/decisions.md`,
      approved before the deviation
- [ ] No secrets, connection strings, or cluster credentials in the diff
