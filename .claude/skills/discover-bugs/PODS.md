# Bug-scan pods

> **Generated from `pods.json` — do not edit by hand.**
> Edit `pods.json` and run `python3 prepare-scan.py --sync-doc` to regenerate.

Target repo: **brave-core** · branch: **master**.

Each pod below is one scan task: its own cron job and its own subagent. A pod sweeps its **entire tree** on master as full source (never diffs), bounded per run and rotating across runs until the whole tree is covered; then it idles for a cooldown and re-scans the whole codebase again. Findings are prioritized (P0/P1/P2) and filed as `ai-generated` issues for human confirmation.

**22 scan tasks:**

| # | Pod id | Area | Paths |
| - | ------ | ---- | ----- |
| 1 | `ai-chat` | AI Chat / Leo | `components/ai_chat/*`<br>`browser/ai_chat/*` |
| 2 | `wallet-ethereum` | Wallet — Ethereum/EVM | `components/brave_wallet/browser/eth_*`<br>`components/brave_wallet/browser/eip*`<br>`components/brave_wallet/browser/ethereum_*` |
| 3 | `wallet-solana` | Wallet — Solana | `components/brave_wallet/browser/solana*` |
| 4 | `wallet-bitcoin` | Wallet — Bitcoin | `components/brave_wallet/browser/bitcoin/*` |
| 5 | `wallet-zcash` | Wallet — Zcash | `components/brave_wallet/browser/zcash/*` |
| 6 | `wallet-other-chains` | Wallet — Cardano/Polkadot/Filecoin/internal | `components/brave_wallet/browser/cardano/*`<br>`components/brave_wallet/browser/polkadot/*`<br>`components/brave_wallet/browser/internal/*`<br>`components/brave_wallet/browser/filecoin_*`<br>`components/brave_wallet/browser/fil_*` |
| 7 | `wallet-keyring` | Wallet — Keyring / key management | `components/brave_wallet/browser/*keyring*` |
| 8 | `wallet-core` | Wallet — Core services & UI | `components/brave_wallet/*`<br>`components/brave_wallet_ui/*`<br>`browser/brave_wallet/*` |
| 9 | `shields` | Shields (content settings, blocking core) | `components/brave_shields/*`<br>`browser/brave_shields/*` |
| 10 | `adblock` | Adblock component | `components/brave_shields/core/browser/ad_block_*`<br>`components/brave_shields/core/browser/adblock*`<br>`components/brave_adblock_ui/*`<br>`components/brave_shields/content/*ad_block*` |
| 11 | `fingerprinting` | Fingerprinting protection (farbling) | `components/script_injector/*`<br>`components/brave_shields/*farbl*`<br>`components/brave_shields/*fingerprint*` |
| 12 | `sync` | Sync | `components/brave_sync/*`<br>`components/sync/*`<br>`components/sync_device_info/*`<br>`components/sync_preferences/*`<br>`browser/sync/*` |
| 13 | `vpn` | VPN | `components/brave_vpn/*`<br>`browser/brave_vpn/*` |
| 14 | `talk` | Talk | `components/brave_talk/*`<br>`components/brave_new_tab_ui/components/default/braveTalk/*`<br>`ios/browser/brave_talk/*` |
| 15 | `talk-premium` | Talk Premium / SKUs entitlement | `components/skus/*`<br>`browser/skus/*` |
| 16 | `news` | News | `components/brave_news/*`<br>`browser/brave_news/*` |
| 17 | `sidebar` | Sidebar | `components/sidebar/*`<br>`browser/ui/sidebar/*` |
| 18 | `speedreader` | Speedreader | `components/speedreader/*`<br>`browser/speedreader/*` |
| 19 | `tor` | Tor | `components/tor/*`<br>`browser/tor/*` |
| 20 | `wayback-machine` | Wayback Machine | `components/brave_wayback_machine/*` |
| 21 | `web-discovery` | Web Discovery | `components/web_discovery/*`<br>`browser/web_discovery/*` |
| 22 | `email-aliases` | Email Aliases | `components/email_aliases/*`<br>`browser/email_aliases/*` |

## Focus per pod

- **AI Chat / Leo** (`ai-chat`): Conversation state lifetime, streaming response handling, untrusted model/server output parsing, tab-content extraction, feature-flag gating.
- **Wallet — Ethereum/EVM** (`wallet-ethereum`): EVM tx construction/signing, ABI decode bounds, nonce/gas math integer over/underflow, RPC response parsing, allowance handling.
- **Wallet — Solana** (`wallet-solana`): Instruction/message compilation bounds, account-meta indexing, signature/serialization correctness, untrusted RPC parsing.
- **Wallet — Bitcoin** (`wallet-bitcoin`): UTXO selection/amount math over/underflow, PSBT/tx serialization bounds, key derivation, block-tracker lifetime.
- **Wallet — Zcash** (`wallet-zcash`): Shielded/transparent tx math, orchard/librustzcash FFI boundary validation, serialization bounds, amount over/underflow.
- **Wallet — Cardano/Polkadot/Filecoin/internal** (`wallet-other-chains`): Chain-specific tx/amount math, serialization bounds, FFI/internal boundary validation, key handling.
- **Wallet — Keyring / key management** (`wallet-keyring`): Seed/private-key lifetime and zeroing, mnemonic handling, keyring migration correctness, unlock/lock state, password handling.
- **Wallet — Core services & UI** (`wallet-core`): Service/factory object lifetime, mojo/IPC validation of renderer-supplied values, asset/price parsing, permission checks, WeakPtr use-after-invalidate.
  - excludes: `components/brave_wallet/browser/eth_*`, `components/brave_wallet/browser/eip*`, `components/brave_wallet/browser/ethereum_*`, `components/brave_wallet/browser/solana*`, `components/brave_wallet/browser/bitcoin/*`, `components/brave_wallet/browser/zcash/*`, `components/brave_wallet/browser/cardano/*`, `components/brave_wallet/browser/polkadot/*`, `components/brave_wallet/browser/internal/*`, `components/brave_wallet/browser/filecoin_*`, `components/brave_wallet/browser/fil_*`, `components/brave_wallet/browser/*keyring*`
- **Shields (content settings, blocking core)** (`shields`): Content-settings resolution correctness, per-site rule application, cross-thread access to shields state, null pref/host-content-settings reads.
  - excludes: `components/brave_shields/core/browser/ad_block_*`, `components/brave_shields/core/browser/adblock*`, `components/brave_shields/*farbl*`, `components/brave_shields/*fingerprint*`
- **Adblock component** (`adblock`): Filter-list provider lifetime, component/DAT loading and parsing bounds, engine update races, untrusted rule/resource parsing.
- **Fingerprinting protection (farbling)** (`fingerprinting`): Farbling seed derivation, script injection into untrusted frames, per-origin randomization consistency, renderer-side value validation.
- **Sync** (`sync`): Sync record encode/decode of untrusted server data, crypto/nudge handling, device-info lifetime, cross-sequence access, migration correctness.
- **VPN** (`vpn`): Connection state machine correctness, credential/subscription handling, region-list parsing of server data, connection-observer lifetime.
- **Talk** (`talk`): Talk integration/launch flow, message-handler input validation, native-JS bridge value validation.
- **Talk Premium / SKUs entitlement** (`talk-premium`): SKU/entitlement verification that gates Talk & VPN premium, credential/order-state parsing of server responses, expiry math, replay/forgery of entitlement state.
- **News** (`news`): Feed/publisher parsing of untrusted network data, image/URL handling, feed-controller lifetime, cross-thread feed updates.
- **Sidebar** (`sidebar`): Sidebar item model lifetime vs UI, tab/browser observer teardown, null browser/webcontents reads, item ordering correctness.
- **Speedreader** (`speedreader`): Distillation of untrusted page content, throttle/delegate lifetime, renderer boundary validation, null document reads.
- **Tor** (`tor`): Tor process/launcher lifetime, control-port parsing, proxy config correctness, profile teardown, leaking identifiers across sessions.
- **Wayback Machine** (`wayback-machine`): Wayback API response parsing, infobar/delegate lifetime vs webcontents, null response handling, URL validation.
- **Web Discovery** (`web-discovery`): Untrusted page/content extraction and reporting, anonymization/hashing correctness, scheduler/observer lifetime, PII leakage in payloads.
- **Email Aliases** (`email-aliases`): Alias service state, server-response parsing, auth/session handling, mojo/IPC validation, alias-list lifetime.
