# STEP 15-C: source-first evaluation pilot tooling

This directory preserves the offline pilot workflow source, not disclosure data
or a human-approved answer set.

[audit_pilot.py](audit_pilot.py) implements four explicit phases:

1. `prepare`: read the existing card archive in SQLite read-only mode, verify
   body hashes and export only the selected disclosure bodies and source rows.
2. `validate`: verify candidate values, evidence locations and numeric/date
   normalization; freeze the draft hash before opening saved rule results.
3. `compare`: compare the frozen candidate draft with already saved rule outputs,
   without running extractors or making network requests.
4. `preservation`: check pre-existing file hashes and the original worktree.

All generated files are local-only and excluded by this directory's
[ignore rules](.gitignore). The source is retained as the historical pilot
procedure: its preparation/preservation checks expect the original 18-file
worktree, so it is not a general clean-checkout bootstrap command.
Every output is created exclusively; existing files are not overwritten.

## Independent review limitations

The pilot covers one COMPLETE contract, one PARTIAL contract and one safety
disclosure. Candidate answers were drafted from archived HTML, frozen, then
compared with saved rule outputs. Earlier discussion had exposed some facts;
this was not fully blind annotation. Structural evidence checks do not establish
semantic correctness, completeness, realization or human approval.

The local pilot contained 48 candidate items, all awaiting human review.
No candidate has been promoted to certified ground truth by this commit.
The next review should separately judge numeric/wording matches, qualifications,
absence versus explicit negation, subsidiary attribution, event timing and
original/correction relationships.

## Deliberately not published

- Actual disclosure HTML or copied source-table text.
- Candidate answers, evidence packets and rule-comparison payloads.
- Detailed reports containing original excerpts.
- Operational or experiment databases.
- Preservation inventories, machine-specific paths and execution logs.

These files remain on the originating machine; selective publication does not
delete or rewrite them. The committed tooling alone cannot reproduce the local
pilot without separately authorized local inputs.
