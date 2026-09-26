# contracts-repo — the Log Contract registry (IF-CONTRACT-YAML)

Runtime data, not application code. `control-api` (C1/C2) commits every contract version
here, so a contract's history *is* git history, and `<id>@<version>` maps to a commit.

Layout: `contracts-repo/<tenant_id>/<contract_id>.yaml`.

S0 seeds two **placeholder** library contracts so the normalizer's control follower and
the engine's golden tests have something to load. C2 finalizes them against the real
compiler, and C3 imports them into the library pack set:

| Contract | Source | Corpus file |
|---|---|---|
| `linux_sshd` | `src_lnx_core_07` (syslog UDP, core zone) | `demo/corpus/linux_sshd.log` |
| `acme_ngfw_cef` | `src_fw_dmz_01` (syslog TCP, dmz zone) | `demo/corpus/acme_ngfw_cef.log` |

`Maha Power`'s `authsrv` contract is intentionally absent: it is created live during
demo Beat 2, and its T3 template arrives later as drift (Beat 4).

The seed YAML files are tracked in the **outer** repo, so a fresh clone has them. The
registry's own git history is created at runtime — `make contracts-repo-init`, or by
control-api on first boot (C1), or by `make demo-reset` (B7) — and `contracts-repo/.git/`
is ignored by the outer repo. That keeps one seed under review while contract *versions*
live in the registry's history, where `<id>@<version>` maps to a commit.
