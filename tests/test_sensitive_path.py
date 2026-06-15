# tests/test_sensitive_path.py — regression suite for _is_sensitive_path
#
# Covers the critical bypass corpus:
#   1. Case-variant paths (.CLAUDE, .ENV, X.SH, Claude.md …)
#   2. Absolute paths (/Users/kreuzer/stock-bot/.claude/…)
#   3. Traversal tricks (.claude/../.claude/…, ./.claude/…)
#   4. Trailing-dot/space filenames (x.sh., x.sh )
#   5. Allow-list: normal data/py/md files must NOT be blocked

import pytest
from mcp_tools._helpers import _is_sensitive_path


# ── MUST BLOCK (return True) ─────────────────────────────────────────────────

BLOCK_CASES = [
    # .claude/ — exact and case-variants
    ".claude/settings.json",
    "./.claude/settings.json",
    ".claude/../.claude/settings.json",   # traversal resolves to .claude/settings.json
    ".CLAUDE/settings.json",
    ".Claude/settings.json",
    # absolute path pointing into .claude/
    "/Users/kreuzer/stock-bot/.claude/settings.json",
    # CLAUDE.md — case-insensitive, any depth
    "CLAUDE.md",
    "claude.md",
    "a/b/CLAUDE.md",
    # scripts/hooks/ — case-insensitive
    "scripts/hooks/x.sh",
    "Scripts/Hooks/x.sh",
    # .sh extension — case-insensitive
    "x.sh",
    "X.SH",
    # .yml / .yaml — case-insensitive
    "x.yml",
    "deploy.YAML",
    # .env variants — case-insensitive
    ".env",
    ".ENV",
    ".env.bak2",
    ".env.bak_dart_0611",
    # data/token_cache.json — exact and case-variant
    "data/token_cache.json",
    "DATA/TOKEN_CACHE.JSON",
    # .git/ — defense-in-depth (git objects/hooks)
    ".git/config",
    ".git/hooks/pre-commit",
    ".GIT/config",
    # .github/ — workflow injection
    ".github/workflows/ci.yml",
    # Trailing-dot/space (filesystem strips them; the function strips before matching)
    "x.sh ",
    "x.sh.",
]

@pytest.mark.parametrize("path", BLOCK_CASES)
def test_should_block(path):
    assert _is_sensitive_path(path) is True, f"Expected BLOCK for: {path!r}"


# ── MUST ALLOW (return False) ─────────────────────────────────────────────────

ALLOW_CASES = [
    "data/PROGRESS.md",
    "data/foo.json",
    "kis_api/dart.py",
    "README.md",
    "data/report_pdfs/x.pdf",
    "docs/notes.txt",
]

@pytest.mark.parametrize("path", ALLOW_CASES)
def test_should_allow(path):
    assert _is_sensitive_path(path) is False, f"Expected ALLOW for: {path!r}"
