#!/usr/bin/env python3
"""Find and buy a Twilio phone number for GhostLine.

Usage:
    uv run python scripts/buy_number.py
    # or with explicit creds:
    uv run python scripts/buy_number.py --sid ACxxx --token xxx --country CA

Reads TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN from .env if present.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from twilio.rest import Client


def _load_creds(args: argparse.Namespace) -> tuple[str, str]:
    """Load Twilio credentials from CLI args or .env."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    sid = args.sid or os.environ.get("TWILIO_ACCOUNT_SID")
    token = args.token or os.environ.get("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        print("ERROR: Need TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.")
        print("Either:")
        print("  1. Add them to .env")
        print("  2. Pass via --sid ACxxx --token xxx")
        sys.exit(1)
    if not sid.startswith("AC"):
        print(f"ERROR: SID must start with 'AC', got: {sid[:4]}...")
        sys.exit(1)
    return sid, token


def _search_numbers(client: Client, country: str, area_code: int | None) -> list[Any]:
    """Search for available local numbers, falling back to toll-free."""
    print(f"\nSearching for available numbers in {country}...")
    if area_code:
        print(f"  (filtering for area code {area_code})")
    try:
        search = client.available_phone_numbers(country).local
        if area_code:
            return search.list(area_code=area_code, limit=5)
        return search.list(limit=5)
    except Exception as exc:
        print(f"ERROR listing local numbers: {exc}")
        print("Trying toll-free numbers instead...")
        try:
            return client.available_phone_numbers(country).toll_free.list(limit=5)
        except Exception as exc2:
            print(f"ERROR: {exc2}")
            sys.exit(1)


def _print_numbers(numbers: list[Any]) -> None:
    """Display available numbers with capabilities."""
    print(f"\nFound {len(numbers)} available number(s):\n")
    for i, num in enumerate(numbers, 1):
        print(f"  [{i}] {num.phone_number}")
        caps = getattr(num, "capabilities", {}) or {}
        print(f"      voice: {caps.get('voice', '?')}, sms: {caps.get('sms', '?')}")
        if hasattr(num, "monthly_cost") and num.monthly_cost:
            print(f"      cost: ${num.monthly_cost}/mo")
    print()


def _pick_number(numbers: list[Any], buy_first: bool) -> Any:
    """Prompt the user to pick a number (or auto-pick the first)."""
    if buy_first:
        return numbers[0]
    try:
        choice = int(input(f"Buy which number? [1-{len(numbers)}] (or 0 to quit): "))
    except (ValueError, EOFError):
        print("No selection. Exiting.")
        sys.exit(0)
    if choice == 0:
        print("No number bought. Exiting.")
        sys.exit(0)
    if not 1 <= choice <= len(numbers):
        print(f"Invalid choice {choice}. Exiting.")
        sys.exit(1)
    return numbers[choice - 1]


def _buy_and_report(client: Client, phone: str) -> None:
    """Purchase the number and print next steps."""
    print(f"\nBuying {phone}...")
    try:
        purchased = client.incoming_phone_numbers.create(phone_number=phone)
    except Exception as exc:
        print(f"ERROR buying number: {exc}")
        print("\nIf this is a trial account, you may need to add a payment method")
        print("or verify your account first: https://help.twilio.com/articles/14796221038999")
        sys.exit(1)
    print(f"\n✅ SUCCESS! Bought number: {phone}")
    print(f"   Twilio SID: {purchased.sid}")
    print("\nAdd this to your .env:")
    print(f"  TWILIO_FROM_NUMBER={phone}")
    print("\n⚠️  If this is a TRIAL account, also verify your cell at:")
    print("   https://console.twilio.com → Phone Numbers → Verified Caller IDs")
    print("   (you can only call verified numbers on trial accounts)")


def main() -> None:
    """CLI entrypoint: parse args, search, pick, buy."""
    parser = argparse.ArgumentParser(description="Buy a Twilio number for GhostLine.")
    parser.add_argument("--sid", help="Twilio Account SID (overrides .env)")
    parser.add_argument("--token", help="Twilio Auth Token (overrides .env)")
    parser.add_argument(
        "--country",
        default="CA",
        help="ISO country code (CA=Canada, US=USA). Default: CA.",
    )
    parser.add_argument(
        "--area-code",
        type=int,
        default=None,
        help="Preferred area code (e.g. 236 for BC, 415 for SF). Optional.",
    )
    parser.add_argument(
        "--buy-first",
        action="store_true",
        help="Skip the prompt and buy the first available number immediately.",
    )
    args = parser.parse_args()

    sid, token = _load_creds(args)
    client = Client(sid, token)
    numbers = _search_numbers(client, args.country, args.area_code)
    if not numbers:
        print("No numbers found. Try a different country or area code.")
        sys.exit(1)
    _print_numbers(numbers)
    selected = _pick_number(numbers, args.buy_first)
    _buy_and_report(client, selected.phone_number)


if __name__ == "__main__":
    main()
