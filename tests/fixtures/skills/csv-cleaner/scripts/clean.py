"""Fixture script: echoes stdin, standing in for a real cleaning step."""
import sys

sys.stdout.write(sys.stdin.read())
