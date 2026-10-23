import sys
from pathlib import Path

# make the project root and tests/ importable (receipt_parser, evaluate)
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
