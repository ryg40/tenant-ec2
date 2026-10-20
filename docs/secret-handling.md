# Secret handling

`scripts/scan.sh` stops a secret or a host value before it goes to a remote or into an image. It needs no network. The hooks of `scripts/install-hooks.sh` run it before a commit (`staged`) and before a push (the new range). `scripts/publish_check.py` applies the same deny lists to the publish set.

| Finding | Source | Result |
| --- | --- | --- |
| Secret | The rules in `.gitleaks.toml` (gitleaks v8.28.0 default rules, path allowlists removed) | Always fails. |
| Host value | `scripts/host-values.deny` (tracked, empty), `.local/host-values.deny` (untracked, the values of one host), `scripts/host-values.regex` (private address ranges) | Fails on `portable`, on a `portable/*` tag and in the mode `image`; a warning elsewhere. |
| Policy | A tracked `.gitleaksignore`, a tracked file under `.local/`, a tracked archive or database dump | Always fails. |

Rules:

- A value lives in `.env` only. In `.env.example` each secret name has an empty value; a setting may hold a portable default that is not a host value.
- A clean scan `without a local deny list` is not a host-value pass. Maintain `.local/host-values.deny` with mode 0600 before publication.
- Add domains, private server names, Git accounts, account IDs, administrator addresses, public key text, e-mail addresses and Entra identifiers to that list.
- Add new values when they become known. Report only the number of added patterns. Never print a pattern.
- Linked worktrees read the local deny list from the main checkout when they have no list of their own.
- Keep the tracked deny list empty. Store every deployment value in the untracked list only.
- Store rendered edge output below an ignored directory such as `rendered/`. A custom output name can defeat a filename ignore rule.
- Public key files ending in `.pub`, Gerbil key files and certificate stores are ignored. An ignore rule does not remove an already tracked file.
- Owner requests and credential records stay in the private development checkout. The public reader check has no list of exempt files.
- `scripts/host-values.allow` holds the public kit URLs that may stay in tracked files.
- Run `sh scripts/scan.sh selftest` after each change of a rule file. The hooks do this when such a file is staged.
- The scan for an image (`scripts/scan.sh image <reference>`) runs before an image leaves the host.
