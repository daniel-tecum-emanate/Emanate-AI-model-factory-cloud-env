# Lane 07 — always-on Linux box — **BLOCKED on a spend decision**

**Blocked, awaiting Daniel.** Filed as **V-095** in `validation/QUEUE.md`. Not provisioned:
recurring spend is a human decision (invariants I5/I9), and an untested provisioning script is
exactly the false green this repo keeps writing lessons about.

**This is the only lane that closes the actual gap** — see [`PROBLEM.md`](../PROBLEM.md).

---

## Why it is still needed after lanes 02, 03 and 04

The unmet requirement, in Daniel's terms: a **long, interactive** session he attaches to, steers,
and detaches from repeatedly over days, from anywhere, including from a phone with the laptop shut
and in a bag.

| Lane | Why it does not close this |
|---|---|
| 02 lid-close | Needs the laptop powered and on the network |
| 03 Cursor cloud | Fire-and-steer; no attach/detach loop |
| 04 Claude cloud | **Might** close it via `--teleport` + the mobile app — but teleport is one-way and untested. If lane 04 proves out, this lane's priority drops sharply. |

**Do not decide V-095 before testing lane 04.** Lane 04 is free and may make a paid box redundant.

## What it would be

A small Linux box running `tmux` plus the `claude` CLI plus the Cursor CLI, reachable over
SSH/Tailscale from a laptop or phone. Planned deliverables were `provision_remote.sh` and
`ssh_remote.sh` as a `longrun` driver, both dry-run verifiable before touching a real host. A
partial artifact exists at `.longrun/provision.rendered.sh` (untested, from the subagent that
died — treat as a sketch, not a script).

## Options and cost

| Option | Cost | Notes |
|---|---|---|
| **Hetzner CPX31** 4 vCPU / 8GB | **~€16/mo** | Cheapest capable box; EU-hosted |
| **DigitalOcean** 4GB / 2 vCPU | **~$24/mo** | Simplest UX, best docs |
| GCE e2-standard-2 | ~$50/mo | **Currently unusable — see below** |
| Self-hosted Actions runner on the above | +$0 | Raises the Actions per-job ceiling 6h → **5 days**, and sidesteps the hosted-runner policy problem in [lane 06](06-github-actions-DROPPED.md) |

**Sizing constraint:** migration work needs local Supabase via Docker. Any of these can run it,
but the 4GB tier is tight — size up if the box is meant to run migrations.

## GCP: corrected, and the conclusion got stronger

**V-095's original evidence line was false.** It said "gcloud auth list returned no accounts" —
that reading was taken while the SDK was still initialising.

Corrected: gcloud **is** authenticated as `dtecum001@gmail.com` with **11 active projects**
(`brokerforceai`, `companybrief-493403`, `gen-lang-client-*`, `iap-sundai-485119`,
`residencias-462416`, `satelite-images-mexico`, `trading-bots-463800`, and two "My First Project"
entries).

But GCP is **not merely "not a shortcut" — it is currently unusable**:

```
$ gcloud billing accounts list
ACCOUNT_ID            NAME                OPEN   MASTER_ACCOUNT_ID
01B457-96799F-D161FB  My Billing Account  False
```

`OPEN: False`. The only billing account is **closed**, so no billable instance can be created at
all until Daniel re-opens billing. Hetzner and DigitalOcean are unaffected.

Correction filed as **V-101** (the queue is append-only, so V-095's text still contains the wrong
line — this is the superseding record).

## The decision Daniel owes

1. **Test lane 04 first** — it is free and may remove the need entirely.
2. If a box is still wanted: **Hetzner ~€16/mo** or **DigitalOcean ~$24/mo**, or re-open GCP
   billing to use the existing 11-project setup.
3. Or accept the gap: lane 02 at the desk, lane 03/04 when away, no multi-day interactive lane.

## Extra safety note if this is built

Per the research findings, `launchd` is a stronger supervisor than a one-shot detached process for
the *local* lane, because `disablesleep` has no crash recovery. If a remote box is provisioned,
the analogous point is that the remote tmux/agent supervisor needs a restart policy — a bare
`tmux` session with no supervision recreates the fragility this lane is meant to remove.
