"""Hourly delivery: send filtered jobs to users whose next_send_at <= now."""
import argparse
import logging

from app.delivery import deliver_due

log = logging.getLogger("delivery_job")


def run(dry_run: bool = False) -> None:
    stats = deliver_due(dry_run=dry_run)
    log.info(
        "delivery done: users=%d jobs_sent=%d errors=%d",
        stats["users"], stats["jobs_sent"], stats["errors"],
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
