#!/usr/bin/env python3
from pathlib import Path
import sys

from dispatch import UsageError, run_selected, split_legacy_flag


def main(argv=None):
    try:
        legacy, remaining = split_legacy_flag(sys.argv[1:] if argv is None else argv)
    except UsageError as error:
        print("stream-mpi: %s" % error, file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    return run_selected(legacy, root / "qemu_integration/run_stream_mpi.py", "reconstructed.stream_mpi", remaining)


if __name__ == "__main__":
    raise SystemExit(main())
