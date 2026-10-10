# Publishing portable snapshots

A portable snapshot contains only the reviewed publish set. It contains no development history, private inventory or owner request record.

| Setting | Value |
| --- | --- |
| Development branch | `local-dev`; `HEAD` must be on it. |
| Publish set | `PUBLISH` and `PUBLISH_DIRS` in `scripts/publish_check.py`. |
| Snapshot branch | Local `portable`, pushed to remote `main`. |
| Tag | `portable/<yyyymmdd>-<source sha>`, annotated, UTC date. |
| Snapshot identity | `tenant-ec2 portable <portable@tenant-ec2.invalid>`, unsigned. |
| Private exclusions | `scripts/dev-only-files.txt`. |
| Pending public review | None. Each published file receives the public reader check. The check has no list of exempt files. |

The optional private exclusion list keeps owner decisions, credential records, security assessments and development regressions out of the snapshot. These files stay tracked in development.

The public [credential reference](credentials.md) describes names, creation and scope. It contains no home inventory.

## Public reader rules

The check rejects private tracker references, machine paths, host records and links to unpublished tracked files.

The local deny list adds owner domains, accounts, addresses and other identifiers. Exact public kit URLs in the allow list remain valid.

A pass without a local deny list does not prove that owner values are absent. The tracked deny list stays empty.

Do not hide a new finding with an accepted-line hash. Correct the text or remove the private file from publication.

New pages remain outside the publish set until the release candidate reviews them. Their presence in development does not approve publication.

The directory rule publishes each tracked file below `scripts/`, but not a file of the private exclusion list. A new script needs no entry in `PUBLISH`. A configuration file needs one.

The development tests are development checkout only. A snapshot does not hold them, and the check script reports a skip for them there.

## Accepted wording at first publication

Date: `2026-10-10`.
The owner accepted both classes below at the second gate of the first publication.

- Seven lines in five files hold the model identifier required for GPT-6 Luna. The files are `.env.example`, `litellm/config.yaml`, `litellm/copilot.yaml`, `scripts/aws-host.cfn.yaml` and `scripts/check.sh`. The gateway routes, the host template and the check script need the identifier.
- Two scripts name the development test directory in one line each: `scripts/check.sh` and `scripts/publish_portable.py`. They need the name to skip those tests in a snapshot.

A new line of either class in another file is a new finding. It needs a new decision.

## Checks before publication

1. Run `python3 scripts/publish_check.py`. Check that the publish set is valid.
2. Run `python3 scripts/publish_check.py --public`. Check that it passes with the local deny list.
3. Run `sh scripts/scan.sh --level fail tree`. Resolve every finding in the publish set.
4. Obtain the release approval of the owner. No check supplies that approval.

The fail-level tree scan also reads development-only records. Those findings need review, but the records must never enter the snapshot.

## Snapshot commands

The following commands create refs. Do not run them while preparing source changes without release approval.

```sh
python3 scripts/publish_portable.py
python3 scripts/publish_portable.py --push
```

The first command checks, creates a snapshot and tag, and prints the push plan. It contacts no remote.

The second command also pushes. It requires the local deny list, installed hooks and the expected remote visibility.

The tool refuses a dirty tree, a shallow repository, a configured `core.hooksPath` and a source branch other than `local-dev`.

The snapshot chain shares no commit with development. The snapshot identity is not a cryptographic signature.

Warning: The tool exposes `--allow-public` and `--visibility-checked`. Neither option grants publication approval or replaces content review.

Not verified: publication of the revised publish set. A rehearsal creates snapshot refs only in a throwaway clone.
