"""
Manual matching trigger — runs a single matching cycle from the command line.

Usage:
    python scripts/run_matching.py

Useful for testing the matching engine without waiting for the Celery beat schedule.
"""

import asyncio
import json

from app.matching_engine.engine import matching_engine


async def main():
    """Run a single matching cycle and print the report."""
    print("Starting manual matching cycle...")
    result = await matching_engine.run_cycle()

    print("\n=== Matching Cycle Report ===")
    print(json.dumps(result, indent=2, default=str))
    print(f"\nTotal matches: {result['results']['total_matches']}")
    print(f"Volume matched: {result['results']['total_volume_matched']}")
    print(f"Timeouts routed to CIPS: {result['timeouts']['count']}")


if __name__ == "__main__":
    asyncio.run(main())
