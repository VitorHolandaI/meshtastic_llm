"""
Radio-layer utilities: packet chunking, model discovery, interactive menus.
"""

import os

from chat_mesh.config import MESH_MAX_CHUNK


def chunk_text(text: str, size: int = MESH_MAX_CHUNK) -> list[str]:
    """Split text into word-boundary chunks that fit within *size* bytes."""
    words = text.split()
    chunks, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate.encode()) > size:
            if current:
                chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def find_models(search_dir: str = ".") -> list[str]:
    """Walk *search_dir* and return directories that contain OpenVINO .xml files."""
    candidates = []
    for root, dirs, files in os.walk(search_dir):
        if any(f.endswith(".xml") for f in files):
            candidates.append(root)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    return sorted(set(candidates))


def choose(prompt_text: str, options: list, allow_custom: bool = False) -> str:
    """Print a numbered menu and return the user's selection."""
    print(f"\n{prompt_text}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    if allow_custom:
        print("  [0] Enter custom path")
    while True:
        raw = input("  > ").strip()
        if raw.isdigit():
            idx = int(raw)
            if allow_custom and idx == 0:
                return input("  Custom path: ").strip()
            if 1 <= idx <= len(options):
                return options[idx - 1]
        print("  Invalid choice, try again.")
