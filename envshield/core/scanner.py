# envshield/core/scanner.py
# The core secret scanning engine for EnvShield.

import re
from typing import Dict, List

from rich.console import Console

console = Console()

# A comprehensive, curated list of high-confidence regex patterns for common secrets,
# inspired by community projects like https://github.com/mazen160/secrets-patterns-db
SECRET_PATTERNS: List[Dict[str, str]] = [
    # --- Generic High-Confidence Patterns ---
    {
        "name": "Generic API Key",
        "pattern": r"(?i)(key|api(?!version)|token|secret|password|auth|credential)[a-z0-9_ .\-,]{0,25}\s*[:=]\s*['\"]([0-9a-zA-Z\-_=]{16,64})['\"]",
    },
    {
        "name": "Private Key",
        "pattern": r"-----BEGIN (?:EC|PGP|DSA|RSA|OPENSSH|ENCRYPTED)? ?PRIVATE KEY(?: BLOCK)?-----",
    },
    {
        "name": "JSON Web Token (JWT)",
        "pattern": r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    },
    {
        "name": "Database Connection String",
        "pattern": r"(?i)(postgres|mysql|mongodb(?:\+srv)?|redis)://[^:]+:[^@]+@",
    },
    # --- Cloud & IaaS Providers ---
    {
        "name": "AWS Access Key ID",
        "pattern": r"\b(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b",
    },
    {
        "name": "AWS Secret Access Key",
        "pattern": r"(?i)aws(.{0,20})?['\"][0-9a-zA-Z\/+=]{40}['\"]",
    },
    {"name": "Google Cloud API Key", "pattern": r"\bAIza[0-9A-Za-z\-_]{35}\b"},
    {"name": "Google OAuth Access Token", "pattern": r"\bya29\.[0-9A-Za-z\-_]+\b"},
    # --- Version Control & DevOps ---
    {
        "name": "GitHub Personal Access Token (Classic)",
        "pattern": r"\bghp_[0-9a-zA-Z]{36}\b",
    },
    {
        "name": "GitHub Personal Access Token (Fine-grained)",
        "pattern": r"\bgithub_pat_[0-9a-zA-Z]{22}_[0-9a-zA-Z]{59}\b",
    },
    {"name": "GitHub OAuth Access Token", "pattern": r"\bgho_[0-9a-zA-Z]{36}\b"},
    {"name": "GitHub App Token", "pattern": r"\b(ghu|ghs)_[0-9a-zA-Z]{36}\b"},
    {
        "name": "Terraform Cloud/Enterprise Token",
        "pattern": r"\b[a-zA-Z0-9]+\.atlasv1\.[a-zA-Z0-9\-_=]{60,70}\b",
    },
    # --- Communication & Messaging ---
    {"name": "Slack Token", "pattern": r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b"},
    {"name": "Telegram Bot Token", "pattern": r"\b[0-9]{8,10}:[a-zA-Z0-9_-]{35}\b"},
    {"name": "Twilio API Key", "pattern": r"\bSK[0-9a-fA-F]{32}\b"},
    {
        "name": "SendGrid API Key",
        "pattern": r"\bSG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}\b",
    },
    {"name": "Mailchimp API Key", "pattern": r"\b[0-9a-f]{32}-us[0-9]{1,2}\b"},
    {"name": "Mailgun API Key", "pattern": r"\bkey-[0-9a-zA-Z]{32}\b"},
    # --- SaaS, PaaS & APIs ---
    {
        "name": "Stripe API Key",
        "pattern": r"\b(sk|pk)_(test|live)_[0-9a-zA-Z]{24,99}\b",
    },
    {
        "name": "Heroku API Key",
        "pattern": r"(?i)heroku[a-z0-9_\- ]*['\"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['\"]",
    },
    {
        "name": "Discord Bot Token",
        "pattern": r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}\b",
    },
    # --- Package Managers ---
    {"name": "npm Token", "pattern": r"\bnpm_[a-zA-Z0-9]{36}\b"},
    {
        "name": "PyPI Upload Token",
        "pattern": r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9-_]{50,1000}\b",
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
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            # We read line by line to get the line number for reporting.
            for line_num, line in enumerate(f, 1):
                for secret in SECRET_PATTERNS:
                    # re.search finds the first occurrence in the line
                    if re.search(secret["pattern"], line):
                        findings.append(
                            {
                                "file_path": file_path,
                                "line_num": line_num,
                                "secret_type": secret["name"],
                                "line_content": line.strip(),
                            }
                        )
                        # We break after the first find per line to avoid duplicate reports
                        # for the same line if multiple patterns match.
                        break
    except IOError:
        # Fails silently if a file can't be opened (e.g., broken symlink)
        return []

    return findings
