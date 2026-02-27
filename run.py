#!/usr/bin/env python3
"""Entry point for FAM Market Day Transaction Manager."""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fam.app import run

if __name__ == '__main__':
    run()
