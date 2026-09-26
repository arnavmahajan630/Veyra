# 04 — DEMO SCRIPT (3:00)

Owner from S2 onward: the presenter. Scenario implementation: B7 (`demo/scenarios/sih_main.yaml`).

## 1. The story in one breath

A government operator runs VEYRA in an air-gapped network:
1. A power company onboards its messy auth server in seconds.
2. A log storm hits: clean logs, vendor logs and ugly logs all land in Wazuh immediately.
3. The ugly ones are "unknown", so VEYRA learns their shape. An AI *drafts* a parser, humans approve it, and history is replayed. Wazuh now catches a brute-force attack it had missed.
4. Every field is traceable to its bytes, and even an insider with root and the keys can't rewrite evidence undetected.

## 2. Pre-seeded world (created by `make demo-reset`)

| Tenant | Source | Transport | Zone | Contract | State at start |
|---|---|---|---|---|---|
| NTRO Core Ops (`t_ntro_core`) | `src_fw_dmz_01`: Acme NGFW (CEF) | syslog TCP 5515 | dmz | `acme_ngfw_cef@1` (library) | active |
| NTRO Core Ops | `src_lnx_core_07`: Linux sshd | syslog UDP 5524 | core | `linux_sshd@1` (library) | active |
| NTRO Core Ops | legacy OT historian (not registered) | syslog UDP 5524 | core | none | **unregistered** |
| Maha Power Corp (`t_maha_power`) | (none yet) | — | — | — | onboarded live in Beat 2 |

Users:
- `admin@veyra` (admin)
- `author@maha` (pack_author, tenant `t_maha_power`)
- `approver@veyra` (pack_approver, platform)

The console's **demo mode** shows a user switcher in the header, so four-eyes approval takes one click.

Background traffic: `DEMO_EPS_BASELINE` events/sec from the NTRO sources, so the Overview is alive before the demo starts.

### 2.1 Messy auth server lines (Maha Power)

Sent by the demo engine through **HTTP push with the key issued in Beat 2**:

```
T1 (in onboarding samples):   <134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=r.patil OK login from 10.4.1.20 via 10.2.3.4"} | trace=
T2 (in onboarding samples):   <134>Sep 26 14:05:10 fw01 app[233]: {"evt":"session","msg":"session 7781 closed for r.patil after 312s"}
T3 (NOT in samples → drift):  <134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=
                                at com.x.Auth.login(Auth.java:88)
```

T3 arrives 8 times in 20 s from `103.21.4.77`, with the stack-trace continuation line and multi-line framing. It is the brute-force attempt Wazuh can't see yet.

## 3. Screen setup

- One 1920×1080 display.
- Browser window 1 (80% of the time): the VEYRA console at `http://localhost:8080`.
- Browser window 2: the Wazuh dashboard, pre-opened on Discover filtered to `rule.groups: veyra`, time range "last 15 minutes", auto-refresh 5 s. Switch with Alt+Tab.
- The demo panel is a hidden route `/demo`. Hotkeys work from any console page:

| Hotkey | Action |
|---|---|
| `Shift+1..6` | Trigger stage |
| `Shift+T` | Insider tamper on the currently open event |
| `Shift+R` | Reset (confirmation required) |

- Browser zoom 110%. Dark theme off unless the projector is known to be good.

## 4. Beats

### Beat 1: Hook (0:00–0:15)
- **Screen:** Console Overview, live. The flow diagram shows NTRO sources pulsing and the tier bar is mostly green.
- **Say:** *"Every organisation's logs look different, and SIEMs choke on the messy ones. VEYRA sits in front of your SIEM, air-gapped, and makes any log usable and provable. This is live, on this laptop, with no internet."*

### Beat 2: Onboard an org source (0:15–0:40)
- **Do** (as `author@maha`):
  1. Sources → **Onboard source** → name "Auth Server", transport **HTTP push**.
  2. Click **Paste samples**. Demo mode pre-fills T1 and T2.
  3. Click **Analyze**. Two templates are detected; no library match. The draft is ready: LLM live, with cache fallback. Mappings show green provenance ticks.
  4. **Create contract**, then the header user switcher → `approver@veyra` → **Approve & activate**.
  5. The **API key** card appears, showing the key once plus a curl example.
- **Say:** *"The org pastes three sample lines. VEYRA detects two message shapes and drafts the mapping. Every field it proposes is tied to real bytes in the sample. A second person approves; four-eyes is built in. They get a key and they're live."*
- **Fallback:** if the draft stalls for more than 5 s, the UI automatically shows the cached draft with a small "cached" badge. Don't mention it.

### Beat 3: Log storm (0:40–1:25)
- **Do:** `Shift+3`. The demo engine starts:
  - Maha Power traffic through HTTP push with the new key: T1, T2 and T3 ×8;
  - an sshd brute-force burst from `45.12.3.9`;
  - CEF deny events;
  - unregistered OT historian lines.
- **Screen (console):**
  - Overview tier bar: tier 1 grows; a tier 3 slice appears (Maha T3 and the OT historian).
  - The "Sources" strip shows `Auth Server` at "62% match, 38% unknown template".
- **Do:** Alt+Tab to Wazuh.
  - Show a T3 event: `veyra.tier: 3`, `observables` containing `103.21.4.77` and `a.sharma`, and `raw_data` intact.
  - Show the **brute-force alert (100111)** for `45.12.3.9` from the clean sshd source.
- **Say:** *"Everything reaches Wazuh immediately. Clean logs are fully normalized to OCSF, and Wazuh fires a brute-force alert on the Linux box. The messy failed-login lines are a shape VEYRA has never seen. It still delivers them, with the IP and user extracted, and labels them 'unknown template'. But Wazuh can't tell the attacker's IP from the server's, so that brute force is invisible. Nothing is dropped, nothing is blocked."*

### Beat 4: Drift → draft → approve → replay (1:25–2:10)
- **Do:** Alt+Tab to the console. A drift card has appeared: "Auth Server: new message shape, 8 events".
  1. Open it. The **draft view** shows:
     - the raw line with colored highlights (user, source IP, destination IP);
     - the mapping table with ✓ provenance for each row;
     - Class: Authentication / Logon / Failure.
  2. The **backtest** panel reads "8 events: tier 3 → tier 1, 0 regressions".
  3. **Submit** → switch user → **Approve** → **Promote**. Contract `authsrv@2` is live.
  4. **Replay 8 events.** The progress bar fills.
  5. Alt+Tab to Wazuh: the same 8 events arrive as `revision 2`, tier 1, Authentication failure. The **brute-force alert (100111) fires for `103.21.4.77`**.
- **Say:** *"VEYRA noticed a new shape and drafted a parser. The AI can only *point at* tokens in the real log; it can't invent values. We tested it on the 8 real events: all upgrade, nothing breaks. Approve, promote, and replay history. Wazuh now sees the attack it missed, retroactively. The AI never touches live traffic; parsing stays deterministic."*
- **Fallback:** if the drift card hasn't appeared within 10 s, press `Shift+4`. That forces the drift worker to flush, and the demo engine also resends T3.

### Beat 5: Traceability and tamper-evidence (2:10–2:50)
- **Do:** Click one replayed event (from the drift view "View events", or Lineage search `a.sharma`).
  1. **Lineage view:** raw on the left, normalized OCSF on the right, and the revision timeline "rev 1 tier 3 → rev 2 tier 1 (authsrv@2)".
  2. Hover `src_endpoint.ip`. The exact bytes `103.21.4.77` highlight in the raw.
  3. **Verify.** Eight steps animate green, from hash through chain walk, segment digest, Merkle inclusion and signature to immudb.
  4. `Shift+T` (insider rewrite: the attacker has root and the data keys, and changes the IP in the stored raw).
  5. **Verify** again. It turns red at "Matches ingest-time hash" and "Merkle inclusion", and the panel reads "Segment seg_… altered after sealing; root w_… signature valid; evidence chain broken here".
- **Say:** *"Every normalized field points to its exact source bytes. Verification walks the hash chain to a signed root. Now an insider with root *and* the encryption keys rewrites the attacker's IP in storage. They can re-encrypt, but they can't re-sign history. VEYRA shows exactly where it broke."*
- **Fallback:** if verify is slow (over 3 s), the steps show a spinner. Keep talking; verify must finish in under 2 s on the laptop (B4 acceptance).

### Beat 6: Close (2:50–3:00)
- **Screen:** Architecture slide, or the console's "About" overlay showing v1's 8 layers with the built ones highlighted.
- **Say:** *"Everything you saw is the v1 architecture running: edge stamping, Kafka, evidence vault, deterministic normalization, SIEM routing, a governed control plane. It scales by configuration from this laptop to a 60,000 events-per-second site."*

## 5. Pre-demo checklist (T-10 min)

- [ ] `make demo-preflight` passes. It checks:
  - all containers healthy;
  - memory headroom ≥ 3 GB;
  - Ollama model loaded (`make llm-warm`);
  - the LLM cache has the T3 draft;
  - Wazuh rules loaded;
  - clock sync.
- [ ] `make demo-reset` completed; the Overview shows baseline traffic.
- [ ] Wazuh tab open, filtered, auto-refresh on.
- [ ] Laptop on power; notifications off; screen sleep off; Wi-Fi **off** (air-gap claim).
- [ ] Backup video on desktop, as `veyra_demo_backup.mp4`.

## 6. Recovery during the demo

| Problem | Action | What to say |
|---|---|---|
| Console frozen | Reload the page (state is server-side) | — |
| A stage didn't fire | Press its hotkey again (stages are idempotent) | — |
| Wazuh slow to show | Keep narrating; refresh Discover | "Wazuh polls every few seconds" |
| Anything catastrophic | Switch to the backup video at the same beat | "Let me show you the recorded run to save time" |

## 7. Q&A prep (likely judge questions)

| Question | Answer |
|---|---|
| "What if the LLM is wrong?" | Provenance check, then backtest, then four-eyes approval, then canary shadow. The LLM can only reference tokens, never invent values. |
| "Throughput?" | Laptop numbers from `make bench-throughput`, plus v1's design target: 60k EPS with 48 partitions and horizontal normalizers. |
| "Is this court-admissible?" | We produce the technical evidence package with a pre-filled BSA 2023 §63 technical section. Admissibility is a legal determination. |
| "Why not just use Logstash/Cribl?" | They don't seal evidence before parsing, don't give byte-level lineage, and aren't air-gap-first with governed parser changes. |
| "What's real vs mocked?" | Everything shown is real. The declared deviations are in the master file §7. |
