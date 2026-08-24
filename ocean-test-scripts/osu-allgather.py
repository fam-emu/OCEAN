#!/usr/bin/env python3
from pathlib import Path
import sys

from dispatch import UsageError, run_selected, split_legacy_flag


def main(argv=None):
    try:
        legacy, remaining = split_legacy_flag(sys.argv[1:] if argv is None else argv)
    except UsageError as error:
        print("osu-allgather: %s" % error, file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    return run_selected(legacy, root / "qemu_integration/run_osu_allgather.py", "reconstructed.osu_allgather", remaining)


if __name__ == "__main__":
    raise SystemExit(main())
