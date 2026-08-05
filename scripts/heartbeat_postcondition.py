#!/usr/bin/env python3
"""Capture and verify heartbeat message IDs in the steward-federation hub.

Usage:
    python scripts/heartbeat_postcondition.py capture \
      --outbox data/federation/nadi_outbox.json \
      --peer-json data/federation/peer.json \
      --output heartbeat-proof.json

    python scripts/heartbeat_postcondition.py verify \
      --proof heartbeat-proof.json

Exit codes:
    0 — all captured heartbeat IDs confirmed in hub
    1 — postcondition not met
    2 — usage or I/O error
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── capture ─────────────────────────────────────────────────────────────────


def cmd_capture(outbox_path: str, peer_json_path: str, output_path: str) -> int:
    """Read outbox and peer.json, save proof of pending heartbeat IDs.

    The proof records two separate identities:
    - *hub_agent_id*: from peer.json identity.city_id (used by nadi-kit
      for hub mailbox file names)
    - *message_source*: cryptographic source from the signed messages
      (used to validate message origin inside mailbox files)
    """
    opath = Path(outbox_path)
    if not opath.exists():
        print(f"error: outbox not found: {opath}", file=sys.stderr)
        return 2

    try:
        raw = json.loads(opath.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: outbox is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(raw, list) or not raw:
        print("error: outbox is empty", file=sys.stderr)
        return 1

    # Structural validation: every entry must be a dict with id/source/operation
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            print(f"error: outbox entry {i} is not a JSON object",
                  file=sys.stderr)
            return 2
        if not isinstance(entry.get("id"), str) or not entry["id"].strip():
            print(f"error: outbox entry {i} missing valid 'id'",
                  file=sys.stderr)
            return 2
        if not isinstance(entry.get("source"), str) or not entry["source"].strip():
            print(f"error: outbox entry {i} missing valid 'source'",
                  file=sys.stderr)
            return 2
        if not isinstance(entry.get("operation"), str) or not entry["operation"].strip():
            print(f"error: outbox entry {i} missing valid 'operation'",
                  file=sys.stderr)
            return 2

    # Cryptographic source from first message (already validated)
    message_source = raw[0]["source"]

    # All messages must share the same source
    sources = {m["source"] for m in raw}
    if len(sources) != 1:
        print(f"error: outbox contains messages from {len(sources)} "
              f"different sources", file=sys.stderr)
        return 2

    # Hub agent identity from peer.json (used by nadi-kit for file names)
    ppath = Path(peer_json_path)
    if not ppath.exists():
        print(f"error: peer.json not found: {ppath}", file=sys.stderr)
        return 2
    try:
        peer = json.loads(ppath.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: peer.json invalid: {exc}", file=sys.stderr)
        return 2
    if not isinstance(peer, dict):
        print("error: peer.json is not a JSON object", file=sys.stderr)
        return 2

    identity = peer.get("identity")
    if not isinstance(identity, dict):
        print("error: peer.json identity is not a JSON object", file=sys.stderr)
        return 2

    hub_agent_id = identity.get("city_id")
    if not isinstance(hub_agent_id, str) or not hub_agent_id.strip():
        print("error: peer.json missing valid identity.city_id",
              file=sys.stderr)
        return 2

    # Filter heartbeat messages (all entries already validated)
    heartbeat_msgs = [
        m for m in raw if m["operation"] == "heartbeat"
    ]
    if not heartbeat_msgs:
        print("error: no heartbeat message found in outbox", file=sys.stderr)
        return 1

    heartbeat_ids = [m["id"] for m in heartbeat_msgs]
    other_ids = [
        m["id"] for m in raw
        if m["operation"] != "heartbeat"
    ]

    proof = {
        "hub_agent_id": hub_agent_id,
        "message_source": message_source,
        "heartbeat_message_ids": heartbeat_ids,
        "additional_message_ids": other_ids,
        "captured_at": time.time(),
    }

    Path(output_path).write_text(json.dumps(proof, indent=2) + "\n")
    print(f"Captured {len(heartbeat_ids)} heartbeat + {len(other_ids)} "
          f"additional message(s)")
    print(f"  hub_agent_id:   {hub_agent_id}")
    print(f"  message_source: {message_source}")
    for mid in heartbeat_ids:
        print(f"  heartbeat: {mid[:16]}…")
    for mid in other_ids:
        print(f"  additional: {mid[:16]}…")
    return 0


# ── verify ─────────────────────────────────────────────────────────────────


def _list_hub_nadi_files() -> list[dict] | None:
    token = (os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        print("error: GH_TOKEN not set", file=sys.stderr)
        return None
    result = subprocess.run(
        ["gh", "api", "repos/kimeisele/steward-federation/contents/nadi"],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "GH_TOKEN": token},
    )
    if result.returncode != 0:
        print(f"error: cannot list hub files: {result.stderr.strip()[:120]}",
              file=sys.stderr)
        return None
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("error: hub API returned invalid JSON", file=sys.stderr)
        return None
    return entries if isinstance(entries, list) else None


def _fetch_hub_file(api_url: str) -> list | None:
    result = subprocess.run(
        ["gh", "api", api_url],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("encoding") != "base64":
        return None
    encoded = data.get("content")
    if not isinstance(encoded, str):
        return None
    try:
        raw_bytes = base64.b64decode(encoded)
        raw_text = raw_bytes.decode("utf-8")
        parsed = json.loads(raw_text)
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def cmd_verify(proof_path: str) -> int:
    ppath = Path(proof_path)
    if not ppath.exists():
        print(f"error: proof file not found: {ppath}", file=sys.stderr)
        return 2

    try:
        proof = json.loads(ppath.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: proof file invalid: {exc}", file=sys.stderr)
        return 2

    hub_agent_id = proof.get("hub_agent_id", "")
    message_source = proof.get("message_source", "")
    heartbeat_ids = proof.get("heartbeat_message_ids", [])
    captured_at = proof.get("captured_at", 0)

    if not hub_agent_id:
        print("error: proof missing hub_agent_id (pre-fix proof file?)",
              file=sys.stderr)
        return 2
    if not message_source:
        print("error: proof missing message_source", file=sys.stderr)
        return 2
    if not heartbeat_ids:
        print("error: proof missing heartbeat_message_ids", file=sys.stderr)
        return 2

    if captured_at > time.time() + 300:
        print("error: captured_at is in the future", file=sys.stderr)
        return 1

    # List hub files
    entries = _list_hub_nadi_files()
    if entries is None:
        return 1

    # Find files matching hub_agent_id (nadi-kit uses agent_id = city_id)
    prefix = f"{hub_agent_id}_to_"
    matching = [e for e in entries
                if isinstance(e, dict)
                and e.get("name", "").startswith(prefix)]

    if not matching:
        print(
            f"error: no hub files for hub_agent_id {hub_agent_id}. "
            f"Files: {', '.join(e.get('name','?') for e in entries[:10]) or '(none)'}",
            file=sys.stderr,
        )
        return 1

    # Read matching files, validate heartbeat IDs with correct source + op
    found_ids: set[str] = set()
    for entry in matching:
        api_url = entry.get("url", "")
        if not api_url:
            continue
        content = _fetch_hub_file(api_url)
        if isinstance(content, list):
            for msg in content:
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                msg_source = msg.get("source")
                msg_op = msg.get("operation")
                if (mid and mid in heartbeat_ids
                        and msg_source == message_source
                        and msg_op == "heartbeat"):
                    found_ids.add(mid)

    missing = [mid for mid in heartbeat_ids if mid not in found_ids]
    if missing:
        print(
            f"error: {len(missing)}/{len(heartbeat_ids)} heartbeat ID(s) "
            f"not confirmed in hub for hub_agent_id {hub_agent_id}",
            file=sys.stderr,
        )
        for mid in missing:
            print(f"  missing: {mid[:16]}…", file=sys.stderr)
        return 1

    print(f"Hub postcondition verified: {len(found_ids)} heartbeat ID(s) "
          f"for hub_agent_id {hub_agent_id} "
          f"in {len(matching)} hub file(s)")
    for mid in found_ids:
        print(f"  confirmed: {mid[:16]}…")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Capture and verify heartbeat hub postcondition")
    sub = parser.add_subparsers(dest="command")

    cap = sub.add_parser("capture")
    cap.add_argument("--outbox", default="data/federation/nadi_outbox.json")
    cap.add_argument("--peer-json", default="data/federation/peer.json")
    cap.add_argument("--output", default="heartbeat-proof.json")

    ver = sub.add_parser("verify")
    ver.add_argument("--proof", default="heartbeat-proof.json")

    args = parser.parse_args()

    if args.command == "capture":
        return cmd_capture(args.outbox, args.peer_json, args.output)
    if args.command == "verify":
        return cmd_verify(args.proof)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
