"""Test-only Streamlit entrypoint; never use as the production launch command."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.phase4a_acceptance import run_acceptance_app

run_acceptance_app()
