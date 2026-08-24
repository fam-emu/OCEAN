#!/usr/bin/env python3
import sys

from dispatch import UsageError, split_legacy_flag


def main(argv=None):
    try:
        legacy, remaining = split_legacy_flag(sys.argv[1:] if argv is None else argv)
    except UsageError as error:
        print("boot-to-dax: %s" % error, file=sys.stderr)
        return 2
    if legacy:
        print("boot-to-dax is reconstructed-image only", file=sys.stderr)
        return 2
    from reconstructed.boot_to_dax import main as candidate_main
    return int(candidate_main(remaining))


if __name__ == "__main__":
    raise SystemExit(main())
