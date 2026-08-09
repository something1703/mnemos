"""`render_deposition_html` in isolation from any database — a hand-built
bundle exercises the anchored/unanchored, contaminated, and no-facts render
paths, plus the one thing that would be a real vulnerability if it silently
broke: a `</script>` sequence inside embedded data must never be able to
terminate the script tag it lives inside of.
"""

from __future__ import annotations

import json
import uuid

from mnemos_api.deposition_html import render_deposition_html


def _bare_deposition(**overrides: object) -> dict:
    base = {
        "action_id": str(uuid.uuid4()),
        "action_type": "verify",
        "description": "Address verified.",
        "declared_at": "2026-08-09T00:00:00.000000Z",
        "contaminated": False,
        "contamination_note": None,
        "facts": [],
    }
    base.update(overrides)
    return base


def _bundle(**overrides: object) -> dict:
    base = {
        "deposition": _bare_deposition(),
        "chain_entries": {},
        "checkpoint": None,
    }
    base.update(overrides)
    return base


def test_renders_unanchored_checkpoint_honestly() -> None:
    html_out = render_deposition_html(_bundle(), anchor_presigned_url=None)
    assert "No covering checkpoint yet" in html_out


def test_renders_anchored_checkpoint_with_presigned_url() -> None:
    bundle = _bundle(
        checkpoint={
            "checkpoint_seq": 3,
            "merkle_root": "ab" * 32,
            "shard_heads": {"0": {"seq": 1, "hash": "cd" * 32}},
            "entry_count": 1,
            "anchor_uri": "s3://mnemos-ledger-anchor-test/checkpoints/x/3.json",
            "anchored_at": "2026-08-09T00:00:00.000000Z",
        }
    )
    html_out = render_deposition_html(
        bundle, anchor_presigned_url="https://example.com/signed-anchor"
    )
    assert "anchored" in html_out
    assert "https://example.com/signed-anchor" in html_out
    assert "verify-anchor-btn" in html_out


def test_renders_contamination_banner() -> None:
    bundle = _bundle(
        deposition=_bare_deposition(
            contaminated=True,
            contamination_note="Revocation abc123 withdrew the evidence it rested on.",
        )
    )
    html_out = render_deposition_html(bundle, anchor_presigned_url=None)
    assert "CONTAMINATED" in html_out
    assert "Revocation abc123" in html_out


def test_renders_no_facts_message() -> None:
    html_out = render_deposition_html(_bundle(), anchor_presigned_url=None)
    assert "No recalled facts" in html_out


def test_a_script_close_tag_inside_embedded_data_cannot_escape_its_script_tag() -> None:
    """The adversarial case: a `reason` or `payload` value containing the
    literal text `</script>` must not be able to terminate the JSON blob's
    <script> tag early — which would let attacker-controlled ledger content
    (a subject_key or an audit reason an operator wrote) inject arbitrary
    markup into an exported deposition."""
    bundle = _bundle(
        chain_entries={
            "0": [
                {
                    "seq": 1,
                    "payload": {
                        "op": "remember",
                        "actor": "agent",
                        "subject_key": "x",
                        "reason": "</script><script>alert(1)</script>",
                        "seq": 1,
                        "shard_id": 0,
                        "tenant_id": str(uuid.uuid4()),
                        "data": {},
                    },
                    "payload_hash": "0" * 64,
                    "prev_hash": "0" * 64,
                    "entry_hash": "0" * 64,
                }
            ]
        }
    )
    html_out = render_deposition_html(bundle, anchor_presigned_url=None)

    # The one substring an HTML parser actually treats as script-terminating
    # is a raw `</script` — regardless of what precedes it inside a JS string
    # literal. It must never appear unescaped; only the backslash-escaped
    # `<\/script` form (still valid inside a JSON string, inert to the HTML
    # parser) may.
    assert "</script>alert(1)</script>" not in html_out
    assert "alert(1)<\\/script>" in html_out

    # And the data survives, decodable, with the dangerous substring intact
    # as DATA rather than markup — round-tripping through the same escape
    # this function applies proves it was an encoding change, not data loss.
    blob = html_out.split("<script>", 2)[2].split("</script>", 1)[0]
    unescaped = blob.replace("<\\/script", "</script")
    parsed = json.loads(unescaped.split("window.__mnemosVerify(", 1)[1].rsplit(", null);", 1)[0])
    assert (
        parsed["chain_entries"]["0"][0]["payload"]["reason"] == "</script><script>alert(1)</script>"
    )
