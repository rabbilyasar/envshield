# envguard/core/scanner.py
# The core secret scanning engine for EnvGuard.

import re
from typing import List, Dict
from rich.console import Console

console = Console()

# A curated list of high-confidence regex patterns for common secrets.
# Each pattern has a name for clear reporting.
SECRET_PATTERNS: List[Dict[str, str]] = [
    {
        "name": "AWS Access Key ID",
        "pattern": r"AKIA[0-9A-Z]{16}"
    },
    {
        "name": "GitHub Personal Access Token (Classic)",
        "pattern": r"ghp_[0-9a-zA-Z]{36}"
    },
    {
        "name": "GitHub Personal Access Token (Fine-grained)",
        "pattern": r"github_pat_[0-9a-zA-Z]{22}_[0-9a-zA-Z]{59}"
    },
    {
        "name": "Generic Private Key",
        "pattern": r"-----BEGIN ((EC|PGP|DSA|RSA|OPENSSH) )?PRIVATE KEY( BLOCK)?-----"
    },
    {
        "name": "Stripe API Key",
        "pattern": r"(sk|pk)_(test|live)_[0-9a-zA-Z]{24}"
    },
]

def scan_file_for_secrets(file_path: str) -> List[Dict]:
    """
    Scans a single file for any of the defined secret patterns.

    Args:
        file_path: The path to the file to scan.

    Returns:
        A list of dictionaries, where each dictionary represents a found secret.
        Returns an empty list if no secrets are found.
    """
    findings = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            # We read line by line to get the line number for reporting.
            for line_num, line in enumerate(f, 1):
                for secret in SECRET_PATTERNS:
                    # re.search finds the first occurrence in the line
                    if re.search(secret["pattern"], line):
                        findings.append({
                            "file_path": file_path,
                            "line_num": line_num,
                            "secret_type": secret["name"],
                            "line_content": line.strip()
                        })
                        # We break after the first find per line to avoid duplicate reports
                        # for the same line if multiple patterns match.
                        break
    except IOError:
        # Fails silently if a file can't be opened (e.g., broken symlink)
        return []

    return findings
