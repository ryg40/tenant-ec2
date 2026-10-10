# Agent instructions

Rules for an agent in this repository. Read [README.md](README.md) first. A snapshot holds runtime guidance only; request private development context before work that needs owner decisions.

1. The tracker is Gitea, through [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md). No GitHub issue, no local ticket file.
2. The development branch is `local-dev`. One change is one `topic/*` branch, merged with `--no-ff`. Nothing goes to GitHub except through `scripts/publish_portable.py --push` after the owner approves.
3. No secret and no host value in a tracked file, a commit message or a report. `.env`, `.local/`, `data/`, `secrets/` and `state/` are ignored. The scan in the hooks refuses a finding.
4. GPT-6 Luna runs at reasoning effort `xhigh` only. Do not lower it and do not set `drop_params` to make a request pass.
5. A credential from another deployment does not go to the host. Each key of the host is new and has the smallest scope.
6. The kits are consumed at the tags of `kits.lock`. Never build from a working checkout of a kit; clone the tag.
7. Write in simple English: one idea per sentence, present tense, "Not verified:" for a fact that no check proved.
8. A security review gives suggestions. A suggestion blocks no commit, no merge and no deploy. A proven functional defect is not a suggestion. Its ticket corrects it. The explicit gates stay: the hooks with the secret scan and the check script, and the label `needs-approval`. A push or a deploy needs the approval of the owner.
