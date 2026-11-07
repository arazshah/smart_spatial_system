#!/usr/bin/env python3
"""Upload this benchmark folder to a Hugging Face *dataset* repo.

    pip install huggingface_hub
    huggingface-cli login          # paste a WRITE token when prompted; it is stored locally
    python scripts/upload_to_hf.py --repo-id s3geo/persian-geoquery-tehran --private

README.md in this folder becomes the dataset card. .env files and caches are never uploaded.
"""
import argparse
import pathlib

from huggingface_hub import HfApi

ROOT = pathlib.Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--repo-id", default="s3geo/persian-geoquery-tehran")
ap.add_argument("--private", action="store_true", help="create as private first (recommended for the first upload)")
args = ap.parse_args()

api = HfApi()
api.create_repo(args.repo_id, repo_type="dataset", private=args.private, exist_ok=True)
api.upload_folder(
    folder_path=str(ROOT),
    repo_id=args.repo_id,
    repo_type="dataset",
    ignore_patterns=[".env", "**/.env", "**/__pycache__/**", "*.pyc", "results/runs_*_dry.jsonl"],
    commit_message="Upload Persian GeoQuery benchmark (Tehran)",
)
print(f"done: https://huggingface.co/datasets/{args.repo_id}")
