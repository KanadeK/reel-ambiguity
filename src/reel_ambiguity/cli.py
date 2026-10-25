import argparse
import json
import sys

from . import __version__
from .core import RATES, InputError, audit, read_text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check video cut-EDL source ambiguity before conform"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("edl")
    parser.add_argument("inventory", help="UTF-8 CSV: clip_id,reel,start,end (exclusive end)")
    parser.add_argument("--rate", required=True, choices=RATES)
    parser.add_argument(
        "--reel-width", type=int, help="explicitly truncate both reel labels to N characters"
    )
    parser.add_argument(
        "--case-fold", action="store_true", help="match ASCII reel names without case"
    )
    parser.add_argument(
        "--handles", type=int, default=0, help="required available frames on EACH side"
    )
    args = parser.parse_args(argv)
    try:
        result = audit(
            read_text(args.edl),
            read_text(args.inventory),
            args.rate,
            reel_width=args.reel_width,
            case_fold=args.case_fold,
            handles=args.handles,
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result["ok"] else 1
    except (InputError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
