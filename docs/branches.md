# Branches and releases

Topology: first-party repository. No upstream remote. The kits are consumed at pinned tags (`kits.lock`), not as forks or submodules.

| Item | Role | Rule |
| --- | --- | --- |
| `origin` | the private Gitea repository | writable, private, the issue tracker |
| `github` | the private GitHub repository | receives only the snapshot branch and the `portable/*` tags |
| `local-dev` | development and tests | host values stay in ignored files; never pushed to `github` |
| `topic/*` | short-lived work | merge into `local-dev` with `--no-ff`, then delete |
| `portable` | local snapshot chain | written by `scripts/publish_portable.py` only; never checked out |
| `portable/<yyyymmdd>-<sha>` | immutable snapshot | annotated tag on a snapshot commit, neutral identity |

Promotion mode: **allowlist snapshot**. `scripts/publish_portable.py` builds a temporary index with exactly the files of `PUBLISH` and `PUBLISH_DIRS` in `scripts/publish_check.py`, commits it onto the previous snapshot, tags it, and pushes the two refs by explicit refspec. The snapshot chain shares no commit with `local-dev`. See [publishing.md](publishing.md).

The tag scheme uses `portable/<yyyymmdd>-<sha>`. The remote branch is `main` on `github`.

The private development checkout records earlier requests. Use the [install guide](install.md) for a portable snapshot.

Each publication needs the approval of the owner. A development commit is not an accepted portable release.
