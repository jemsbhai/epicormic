"""Vocabulary and typography scan for epicormic documents and docstrings.

Rejects em-dashes, en-dashes, curly quotes, and the words listed in
tools/banned_words.txt. Markdown files are scanned in full. Python files
are scanned only in their comments and string literals (which includes
docstrings), so an identifier imported from a third-party package never
trips the scan.

Usage:
    python tools/scan_vocabulary.py            # scan the repository
    python tools/scan_vocabulary.py PATH ...   # scan the given files or directories

Exit status is 1 when any hit is found, 0 otherwise.
"""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BANNED_WORDS_FILE = REPO_ROOT / "tools" / "banned_words.txt"
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    ".hypothesis",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "htmlcov",
}
TYPOGRAPHY = {
    "\u2014": "em-dash",
    "\u2013": "en-dash",
    "\u2018": "curly single quote",
    "\u2019": "curly single quote",
    "\u201c": "curly double quote",
    "\u201d": "curly double quote",
}


def load_banned_words(path: Path = BANNED_WORDS_FILE) -> re.Pattern[str]:
    words = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    alternation = "|".join(re.escape(word) for word in words)
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE)


def scan_text(text: str, pattern: re.Pattern[str], first_line: int = 1) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for offset, line in enumerate(text.splitlines()):
        number = first_line + offset
        for match in pattern.finditer(line):
            hits.append((number, f"banned word '{match.group(0)}'"))
        for char, name in TYPOGRAPHY.items():
            if char in line:
                hits.append((number, name))
    return hits


def scan_markdown(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    return scan_text(path.read_text(encoding="utf-8"), pattern)


def scan_python(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    hits: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                hits.extend(scan_text(token.string, pattern, first_line=token.start[0]))
    except tokenize.TokenError as error:
        hits.append((0, f"could not tokenize: {error}"))
    return hits


def iter_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
            continue
        for path in sorted(root.rglob("*")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix in {".md", ".py"} and path.is_file():
                files.append(path)
    return [path for path in files if path.resolve() != BANNED_WORDS_FILE.resolve()]


def main(argv: list[str]) -> int:
    pattern = load_banned_words()
    roots = [Path(arg) for arg in argv] or [REPO_ROOT]
    total = 0
    for path in iter_files(roots):
        hits = scan_markdown(path, pattern) if path.suffix == ".md" else scan_python(path, pattern)
        shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        for line, reason in hits:
            total += 1
            print(f"{shown}:{line}: {reason}")
    if total:
        print(f"{total} hit(s)")
        return 1
    print("vocabulary scan clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
