import json
import os
import sys
from typing import List, Dict

def load_config_file(file_path: str) -> List[Dict[str, str]]:
    """Load configuration from a JSON file."""
    # First check if the file exists
    if not os.path.exists(file_path):
        print(f"Note: Config file does not exist, creating a new one: {file_path}")
        save_config_file(file_path, [])
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            config = json.load(file)
        if not isinstance(config, list):
            raise ValueError("Config file must contain a list of doc sources")
        return config
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading config file: {e}", file=sys.stderr)
        sys.exit(1)

def save_config_file(file_path: str, doc_sources: List[Dict[str, str]]) -> None:
    """Save a list of doc sources to a JSON config file."""
    try:
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(doc_sources, file, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving config file: {e}", file=sys.stderr)
        sys.exit(1) 