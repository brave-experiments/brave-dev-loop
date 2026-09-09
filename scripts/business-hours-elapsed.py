#!/usr/bin/env python3
"""Check if a threshold of business hours has elapsed since a given timestamp.

Weekend days (Saturday and Sunday) are excluded from the count entirely.
Only weekday (Mon-Fri) hours are counted.

Usage: business-hours-elapsed.py <iso-timestamp> [threshold-hours]
  threshold-hours defaults to 24.
Prints the number of business hours elapsed.
Exit code 0 if >= threshold business hours, 1 if not, 2 on error.
"""

import sys
from datetime import datetime, timedelta, timezone


def business_hours_between(ref, now):
    """Count hours elapsed between ref and now, excluding weekend days."""
    if ref >= now:
        return 0.0

    total_seconds = 0.0
    day = ref.replace(hour=0, minute=0, second=0, microsecond=0)

    while day < now:
        next_day = day + timedelta(days=1)

        if day.weekday() < 5:  # Monday=0 through Friday=4
            period_start = max(day, ref)
            period_end = min(next_day, now)
            if period_start < period_end:
                total_seconds += (period_end - period_start).total_seconds()

        day = next_day

    return total_seconds / 3600


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: business-hours-elapsed.py <iso-timestamp> [threshold-hours]",
            file=sys.stderr,
        )
        print("Prints business hours elapsed (weekdays only).", file=sys.stderr)
        print("Exits 0 if >= threshold (default 24h), 1 if not.", file=sys.stderr)
        sys.exit(2)

    try:
        ref = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    except ValueError as e:
        print(f"Error: Invalid timestamp: {e}", file=sys.stderr)
        sys.exit(2)

    threshold = 24.0
    if len(sys.argv) >= 3:
        try:
            threshold = float(sys.argv[2])
        except ValueError as e:
            print(f"Error: Invalid threshold-hours: {e}", file=sys.stderr)
            sys.exit(2)

    now = datetime.now(timezone.utc)
    hours = business_hours_between(ref, now)

    print(f"{hours:.1f}")
    sys.exit(0 if hours >= threshold else 1)
