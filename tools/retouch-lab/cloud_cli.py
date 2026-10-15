#!/usr/bin/env python3
"""Test the cloud adapter independently of Discord."""
import argparse
from pathlib import Path
import time

from cloud import RetouchClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--profile", required=True)
parser.add_argument("--mode", choices=("ai", "standard"), default="ai")
parser.add_argument("image", type=Path)
parser.add_argument("mask", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
client = RetouchClient(args.profile)
try:
    start = time.monotonic()
    output = client.clean(args.image.read_bytes(), args.mask.read_bytes(), mode=args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(f"Saved cloud result to {args.output}; elapsed {time.monotonic() - start:.1f}s")
finally:
    client.close()
