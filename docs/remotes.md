# Remotes

| Remote | Points at | Receives | Push |
| --- | --- | --- | --- |
| `origin` | The private development repository on the installer's Gitea server (configure its URL locally) | `local-dev`, topic branches and their tags | Permitted for development work. |
| `github` | The snapshot repository on GitHub, private by default (configure its URL locally) | Only the local branch `portable` as the remote branch `main`, and the tags `portable/*` | Only through `scripts/publish_portable.py --push`, with the approval of the owner. |

The pre-push hook refuses each other ref toward every remote except `origin`, also with `--all`, `--tags`, `--follow-tags` or `--mirror`.

Add the remotes in a new clone by hand. Write each URL without a user name or a token; Git takes the credential from its store.

```sh
git remote add origin https://<gitea server>/<owner>/tenant-ec2.git
git remote add github https://github.com/<account>/tenant-ec2.git
git config remote.github.tagOpt --no-tags
scripts/install-hooks.sh
```

Keep the GitHub repository private. The publish command asks `gh` for the visibility and refuses a repository that is not private.
Only `--allow-public` permits a public repository. That option records a decision of the owner, not a content review.
