"""Render a deposition as a self-contained HTML file that verifies its own
hashes offline, in the browser — PHASE_06 6.7's "hand this to an auditor"
artifact.

No CDN, no build step, no network required to verify. The only external
network call this page can make is optional: fetching the S3-anchored root
via a presigned URL, offered as a clearly-labeled extra step because it is
the one part of `docs/ledger.md` §5's three-part verification (5.3, checking
the live chain against something outside the database) that an offline file
cannot do on its own.

The JavaScript here is a byte-for-byte port of `packages/engine/src/
mnemos_engine/canonical.py` — same canonical JSON encoding, same
`entry_hash = SHA256(payload_hash || prev_hash)` construction, same Merkle
folding with odd-node promotion instead of duplication. `docs/ledger.md` is
the contract; this is a second, independent implementation of it, on purpose
— a verifier that could only ever agree with our own Python would prove
nothing.
"""

from __future__ import annotations

import html
import json
from typing import Any

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f8; --panel: #ffffff; --ink: #1a1a1a; --muted: #666;
  --border: #e2e2e5; --accent: #2b5fd9; --ok: #1a7f37; --bad: #c0342a;
  --warn: #9a6700; --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #15161a; --panel: #1e1f24; --ink: #e8e8ea; --muted: #9a9aa2;
    --border: #303138; --accent: #7ea1ff; --ok: #3fb950; --bad: #f0685f; --warn: #d9a441; }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem 4rem; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.wrap { max-width: 880px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 .75rem; padding-bottom: .4rem; border-bottom: 1px solid var(--border); }
.sub { color: var(--muted); margin: 0 0 1.5rem; font-size: .9rem; }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem 1.5rem; margin-bottom: 1rem; }
.badge { display: inline-block; padding: .15rem .55rem; border-radius: 999px; font-size: .78rem; font-weight: 600; }
.badge.ok { background: color-mix(in srgb, var(--ok) 18%, transparent); color: var(--ok); }
.badge.bad { background: color-mix(in srgb, var(--bad) 18%, transparent); color: var(--bad); }
.badge.warn { background: color-mix(in srgb, var(--warn) 18%, transparent); color: var(--warn); }
.badge.muted { background: color-mix(in srgb, var(--muted) 18%, transparent); color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: .88rem; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing: .02em; }
tr:last-child td { border-bottom: none; }
.mono { font-family: var(--mono); font-size: .82em; word-break: break-all; }
.flags span { margin-right: .4rem; }
.contaminated { border-color: var(--bad); background: color-mix(in srgb, var(--bad) 6%, var(--panel)); }
button {
  font: inherit; font-weight: 600; padding: .55rem 1.1rem; border-radius: 8px;
  border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer;
}
button.secondary { background: transparent; color: var(--accent); }
button:disabled { opacity: .5; cursor: default; }
#verify-log { margin-top: 1rem; font-family: var(--mono); font-size: .82rem; white-space: pre-wrap; }
#verify-log .line { padding: .15rem 0; }
#verify-log .ok { color: var(--ok); }
#verify-log .bad { color: var(--bad); }
#verify-log .info { color: var(--muted); }
.footer { color: var(--muted); font-size: .8rem; margin-top: 3rem; text-align: center; }
"""

_JS = r"""
const GENESIS = new Uint8Array(32);

function toHex(bytes) {
  return Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");
}
function fromHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) out[i / 2] = parseInt(hex.substr(i, 2), 16);
  return out;
}
function concatBytes(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0); out.set(b, a.length);
  return out;
}
async function sha256(bytes) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

// A second, independent implementation of packages/engine/src/mnemos_engine/
// canonical.py's canonicalize() — object keys sorted, no insignificant
// whitespace, no float support. Every key our own schema ever emits is
// plain ASCII, so UTF-16 code-unit sort order (JS's default) agrees with
// UTF-8 byte-order sort exactly; that equivalence is what would need
// revisiting before this could be reused for arbitrary non-ASCII keys.
function canonicalize(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw new Error("non-integer numbers are not canonical");
    return String(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return "{" + keys.map(k => JSON.stringify(k) + ":" + canonicalize(value[k])).join(",") + "}";
  }
  throw new Error("cannot canonicalize " + typeof value);
}

async function payloadHash(payload) {
  return sha256(new TextEncoder().encode(canonicalize(payload)));
}
async function entryHash(payloadDigest, prev) {
  return sha256(concatBytes(payloadDigest, prev));
}

// docs/ledger.md §4: leaves sorted, SHA256(left||right) per pair, an odd
// trailing node promoted unchanged (never duplicated — that is what makes
// this collision-resistant against CVE-2012-2459-style attacks).
async function merkleRoot(leafHexes) {
  if (leafHexes.length === 0) return toHex(GENESIS);
  let level = [...leafHexes].sort().map(fromHex);
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i + 1 < level.length; i += 2) {
      next.push(await sha256(concatBytes(level[i], level[i + 1])));
    }
    if (level.length % 2 === 1) next.push(level[level.length - 1]);
    level = next;
  }
  return toHex(level[0]);
}

function log(container, cls, text) {
  const div = document.createElement("div");
  div.className = "line " + cls;
  div.textContent = text;
  container.appendChild(div);
}

async function verifyShard(shardId, entries, out) {
  let expectedPrev = GENESIS;
  let expectedSeq = 1;
  for (const row of entries) {
    if (row.seq !== expectedSeq) {
      log(out, "bad", `shard ${shardId}: sequence gap — expected seq ${expectedSeq}, found ${row.seq}. A row was removed.`);
      return false;
    }
    const ph = await payloadHash(row.payload);
    if (toHex(ph) !== row.payload_hash) {
      log(out, "bad", `shard ${shardId} seq ${row.seq}: payload does not match its recorded hash — the row was edited.`);
      return false;
    }
    if (toHex(expectedPrev) !== row.prev_hash) {
      log(out, "bad", `shard ${shardId} seq ${row.seq}: prev_hash does not match the previous entry — the chain was spliced.`);
      return false;
    }
    const eh = await entryHash(ph, expectedPrev);
    if (toHex(eh) !== row.entry_hash) {
      log(out, "bad", `shard ${shardId} seq ${row.seq}: entry_hash does not recompute.`);
      return false;
    }
    expectedPrev = eh;
    expectedSeq = row.seq + 1;
  }
  log(out, "ok", `shard ${shardId}: ${entries.length} entr${entries.length === 1 ? "y" : "ies"} verified, genesis to head — every payload hash, every prev_hash link, every entry_hash recomputes.`);
  return true;
}

async function verifyCheckpoint(checkpoint, out) {
  const leaves = Object.values(checkpoint.shard_heads).map(h => h.hash);
  const recomputed = await merkleRoot(leaves);
  if (recomputed !== checkpoint.merkle_root) {
    log(out, "bad", `checkpoint ${checkpoint.checkpoint_seq}: recomputed Merkle root does not match the stored root.`);
    log(out, "info", `  stored:     ${checkpoint.merkle_root}`);
    log(out, "info", `  recomputed: ${recomputed}`);
    return false;
  }
  log(out, "ok", `checkpoint ${checkpoint.checkpoint_seq}: Merkle root recomputes correctly from its own ${Object.keys(checkpoint.shard_heads).length} shard heads.`);
  return true;
}

async function verifyAnchor(checkpoint, anchorUrl, out) {
  log(out, "info", "fetching the anchor from S3 (the one step in this page that uses the network)...");
  let anchor;
  try {
    const response = await fetch(anchorUrl);
    if (!response.ok) throw new Error("HTTP " + response.status);
    anchor = await response.json();
  } catch (err) {
    log(out, "bad", "could not fetch the anchor: " + err.message);
    return false;
  }
  if (anchor.merkle_root !== checkpoint.merkle_root) {
    log(out, "bad", "the anchored root does NOT match this export's checkpoint. Either this export was doctored, or the database was rewritten after anchoring.");
    log(out, "info", `  anchored:  ${anchor.merkle_root}`);
    log(out, "info", `  exported:  ${checkpoint.merkle_root}`);
    return false;
  }
  log(out, "ok", "the S3-anchored root (written to an Object Lock bucket nobody, including the AWS account root, can alter before its retention expires) matches this export's checkpoint exactly.");
  log(out, "info", "note: this compares the export against the anchor, not the anchor against the database right now — for that, run `mnemos-attest verify` against a live connection.");
  return true;
}

async function runVerification(bundle, anchorUrl) {
  const button = document.getElementById("verify-btn");
  const anchorButton = document.getElementById("verify-anchor-btn");
  const out = document.getElementById("verify-log");
  out.innerHTML = "";
  button.disabled = true;
  button.textContent = "Verifying…";

  let allOk = true;
  const shardIds = Object.keys(bundle.chain_entries);
  if (shardIds.length === 0) {
    log(out, "info", "this deposition has no subject-scoped audit trail to verify (the action recorded no subject_key).");
  }
  for (const shardId of shardIds) {
    const ok = await verifyShard(shardId, bundle.chain_entries[shardId], out);
    allOk = allOk && ok;
  }
  if (bundle.checkpoint) {
    const ok = await verifyCheckpoint(bundle.checkpoint, out);
    allOk = allOk && ok;
  } else {
    log(out, "warn", "no covering checkpoint yet — this action's chain segment is unanchored.");
  }

  const badge = document.getElementById("verify-result");
  if (allOk) {
    badge.textContent = "VERIFIED";
    badge.className = "badge ok";
  } else {
    badge.textContent = "FAILED";
    badge.className = "badge bad";
  }
  button.disabled = false;
  button.textContent = "Re-verify";
  if (anchorButton) anchorButton.disabled = !bundle.checkpoint;
}

window.__mnemosVerify = function (bundle, anchorUrl) {
  document.getElementById("verify-btn").addEventListener("click", () => runVerification(bundle, anchorUrl));
  if (anchorUrl) {
    document.getElementById("verify-anchor-btn").addEventListener("click", () => {
      const out = document.getElementById("verify-log");
      verifyAnchor(bundle.checkpoint, anchorUrl, out);
    });
  }
};
"""


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _fact_flags(fact: dict[str, Any]) -> str:
    flags = []
    if fact.get("revoked_since"):
        flags.append('<span class="badge bad">revoked since</span>')
    if fact.get("superseded_since"):
        flags.append('<span class="badge warn">superseded since</span>')
    elif fact.get("changed_since"):
        flags.append('<span class="badge warn">changed since</span>')
    return " ".join(flags)


def _render_facts(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return '<p class="sub">No recalled facts are attached to this action.</p>'
    rows = []
    for f in facts:
        provenance = "".join(
            f'<div class="mono">{_esc(e["event_type"])} · {_esc(e["source_trust"])} · '
            f"{_esc(e['home_region'])} · sha256:{_esc(e['content_hash'])[:16]}…</div>"
            for e in f.get("provenance", [])
        )
        score = f.get("score_at_recall")
        score_str = f"{score:.4f}" if isinstance(score, int | float) else "—"
        rows.append(
            "<tr>"
            f'<td><div class="mono">{_esc(f["subject_key"])}</div>'
            f'<div class="mono" style="color:var(--muted)">{_esc(f["fact_id"])}</div></td>'
            f"<td>{_esc(f['trust_at_recall'])} → {_esc(f.get('trust_now') or 'deleted')}"
            f' <div class="flags">{_fact_flags(f)}</div></td>'
            f"<td>{score_str}</td>"
            f"<td>{provenance or '—'}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Fact</th><th>Trust (at recall → now)</th>"
        f"<th>Score</th><th>Provenance episodes</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def render_deposition_html(
    bundle: dict[str, Any],
    *,
    anchor_presigned_url: str | None,
) -> str:
    """Build the complete, self-contained HTML page for one deposition
    bundle (as returned by `mnemos_engine.accountability.build_verifiable_export`,
    already JSON-serialized — see `services/api/src/mnemos_api/tools.py`'s
    `export_deposition` handler for how the bundle is prepared)."""
    deposition = bundle["deposition"]
    checkpoint = bundle["checkpoint"]

    contaminated_banner = ""
    if deposition["contaminated"]:
        contaminated_banner = (
            '<div class="panel contaminated">'
            '<span class="badge bad">CONTAMINATED</span> '
            f"{_esc(deposition['contamination_note'])}</div>"
        )

    anchor_html = '<p class="sub">Not yet anchored to S3 Object Lock — this export\'s proof rests on the checkpoint alone.</p>'
    if checkpoint and checkpoint.get("anchor_uri"):
        anchor_html = (
            f'<p><span class="badge ok">anchored</span> {_esc(checkpoint["anchor_uri"])} '
            f"at {_esc(checkpoint.get('anchored_at'))}</p>"
        )
        if anchor_presigned_url:
            anchor_html += (
                '<p class="sub">A time-limited link to fetch that anchor is wired to the '
                '"Check against S3 anchor" button below — the one verification step here '
                "that needs network access.</p>"
            )

    checkpoint_html = '<p class="sub">No covering checkpoint yet.</p>'
    if checkpoint:
        checkpoint_html = (
            "<table><tbody>"
            f"<tr><th>Checkpoint</th><td>{checkpoint['checkpoint_seq']}</td></tr>"
            f'<tr><th>Merkle root</th><td class="mono">{_esc(checkpoint["merkle_root"])}</td></tr>'
            f"<tr><th>Entries covered</th><td>{checkpoint['entry_count']}</td></tr>"
            "</tbody></table>"
            f"{anchor_html}"
        )

    shard_summary = (
        ", ".join(
            f"shard {sid} ({len(rows)} entries)" for sid, rows in bundle["chain_entries"].items()
        )
        or "none — this action recorded no subject_key"
    )

    bundle_json = json.dumps(bundle, separators=(",", ":")).replace("</script", "<\\/script")
    anchor_url_json = json.dumps(anchor_presigned_url)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deposition — {_esc(deposition["action_type"])} — {_esc(deposition["action_id"])[:8]}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>{_esc(deposition["action_type"])}</h1>
  <p class="sub">{_esc(deposition["description"])} · declared {_esc(deposition["declared_at"])} ·
    action <span class="mono">{_esc(deposition["action_id"])}</span></p>

  {contaminated_banner}

  <h2>Facts this action relied on</h2>
  <div class="panel">{_render_facts(deposition["facts"])}</div>

  <h2>Covering ledger checkpoint</h2>
  <div class="panel">{checkpoint_html}</div>

  <h2>Self-verification</h2>
  <div class="panel">
    <p class="sub">This page embeds the raw audit-chain data for
      {_esc(shard_summary)} and reimplements <code>docs/ledger.md</code>'s hash
      construction independently in JavaScript below — it does not trust this
      export process's own arithmetic. Verification runs entirely in your
      browser using the Web Crypto API; nothing is sent anywhere unless you
      click the S3 anchor check.</p>
    <p>
      <button id="verify-btn">Verify offline</button>
      <button id="verify-anchor-btn" class="secondary" disabled>Check against S3 anchor</button>
      <span id="verify-result" class="badge muted">not yet run</span>
    </p>
    <div id="verify-log"></div>
  </div>

  <p class="footer">Generated by Mnemos. Verify this document with your own
    eyes, not ours — see docs/ledger.md for the specification this page
    implements independently.</p>
</div>
<script>{_JS}</script>
<script>
window.__mnemosVerify({bundle_json}, {anchor_url_json});
</script>
</body>
</html>
"""


__all__ = ["render_deposition_html"]
