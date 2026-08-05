# envshield/core/importer.py
import os
import re
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse

import toml
import questionary
from rich.console import Console

from .scanner import SECRET_PATTERNS
from ..parsers.factory import get_parser
from .exceptions import EnvShieldException

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

console = Console()

# Heuristics for smarter import
SECRET_KEY_KEYWORDS = ["secret", "token", "password", "key", "auth", "credential"]

# Naming conventions several frontend frameworks use to mark an env var as
# intentionally public: these get inlined straight into the client-side
# bundle at build time, so by design they are never secret -- regardless of
# what their name happens to contain (a Stripe *publishable* key legitimately
# has "key" in its name, e.g. NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY).
PUBLIC_KEY_PREFIXES = (
    "NEXT_PUBLIC_",  # Next.js
    "VITE_",  # Vite
    "REACT_APP_",  # Create React App
    "NUXT_PUBLIC_",  # Nuxt 3
    "PUBLIC_",  # SvelteKit
    "GATSBY_",  # Gatsby
)

# Specific well-known variable names that are public by convention even
# though the name alone would otherwise look secret-ish -- e.g. dotenvx's
# own encryption metadata variable, which holds a *public* key.
KNOWN_PUBLIC_VAR_NAMES = {"DOTENV_PUBLIC_KEY"}


def key_contains_secret_keyword(key: str, keywords=SECRET_KEY_KEYWORDS) -> bool:
    """
    Checks whether any '_'-delimited token in `key` matches a secret keyword.

    Token-based (not substring) matching avoids false positives on compound
    names that merely *contain* a keyword as a substring -- e.g. MONKEY_PATCH
    contains "key" and AUTHOR_NAME contains "auth", but neither is a secret.
    """
    tokens = key.lower().split("_")
    return any(keyword in tokens for keyword in keywords)


def _is_conventionally_public(key: str) -> bool:
    """
    True for a variable that's public by a well-known naming convention.
    Doesn't override a high-confidence match against an actual secret-shaped
    *value* -- see _classify_variable -- so a real secret accidentally
    placed under e.g. a NEXT_PUBLIC_ name is still flagged.
    """
    if key in KNOWN_PUBLIC_VAR_NAMES:
        return True
    return key.upper().startswith(PUBLIC_KEY_PREFIXES)


def _classify_variable(key: str, value: str) -> Tuple[bool, Any]:
    """
    Intelligently classifies a variable as a secret and suggests a default value.

    Returns:
        A tuple of (is_secret: bool, default_value: Any | None).
    """
    # 1. Check value against high-confidence secret patterns first -- this
    # always wins, even for a conventionally-public name.
    for secret in SECRET_PATTERNS:
        if re.search(secret["pattern"], value):
            return True, None

    # 2. A conventionally-public name is never treated as secret from its
    # name alone -- skip the keyword heuristic entirely for these.
    if _is_conventionally_public(key):
        return False, value or None

    # 3. Check the key for common secret-indicating keywords
    if key_contains_secret_keyword(key):
        return True, None

    # 4. Anything else with a concrete value already sitting in the source
    # file is worth suggesting as the default -- it's already committed (or
    # already on this developer's disk) and isn't keyword-flagged as a
    # secret, so carrying it forward as a schema default doesn't expose
    # anything new. This used to be limited to a small hardcoded whitelist
    # of variable names (DEBUG, LOG_LEVEL, PORT, HOST, ...), which missed
    # every project-specific non-secret var -- e.g. a Flask app's
    # `DB_NAME = "athena"` or `CACHE_PORT = 6379` got no suggested default
    # at all, just because the name wasn't on the list.
    if value:
        return False, value

    # 5. Blank value, no other signal: genuinely undetermined.
    return False, None


def _infer_type(key: str, value: str) -> Optional[str]:
    """
    Best-effort guess at a non-secret variable's schema `type`, from the
    shape of its one sample value -- so a freshly-imported schema starts
    with real type/format constraints (see schema_types.py) instead of
    every variable defaulting to an unconstrained plain string that has to
    be typed in by hand later. Only confident, unambiguous shapes are
    inferred; anything else is left alone rather than guessed at.
    """
    if not value:
        return None
    if re.fullmatch(r"-?\d+", value):
        return "port" if "port" in key.lower().split("_") else "int"
    if value.lower() in ("true", "false"):
        return "bool"
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return "url"
    if _EMAIL_RE.match(value):
        return "email"
    return None


def generate_schema_from_file(file_path: str, interactive: bool = False) -> str:
    """
    Reads an environment file and generates a TOML schema string, with an
    optional interactive mode for refinement.
    """
    if not os.path.exists(file_path):
        raise EnvShieldException(f"Input file not found at: {file_path}")

    parser = get_parser(file_path)
    if not parser:
        raise EnvShieldException(f"Could not find a suitable parser for '{file_path}'.")

    variables = parser.get_vars(file_path, get_values=True)

    schema_dict: Dict[str, Any] = {}
    secrets_found = 0
    defaults_found = 0
    types_found = 0

    console.print("\n[bold]Analyzing variables...[/bold]")

    for key, value in variables.items():
        is_secret, default_value = _classify_variable(key, value)
        inferred_type = None if is_secret else _infer_type(key, value)

        if interactive:
            console.print(
                f"\nVariable [bold cyan]{key}[/bold cyan] = [dim]'{value}'[/dim]"
            )
            is_secret = questionary.confirm(
                "  Mark as a secret?", default=is_secret
            ).ask()
            if not is_secret:
                use_default = questionary.confirm(
                    f"  Use '{value}' as the default value?",
                    default=(default_value is not None),
                ).ask()
                if use_default:
                    default_value = value
                else:
                    default_value = None
            else:
                inferred_type = None

        schema_dict[key] = {"description": "TODO: Add description."}
        if is_secret:
            schema_dict[key]["secret"] = True
            secrets_found += 1
        else:
            schema_dict[key]["secret"] = False
            if default_value is not None:
                schema_dict[key]["defaultValue"] = default_value
                defaults_found += 1
            if inferred_type:
                schema_dict[key]["type"] = inferred_type
                types_found += 1

    header = (
        "# This schema was auto-generated by 'envshield import'\n"
        "# Please review and add descriptions for each variable.\n\n"
    )

    console.print("\n[bold green]✓ Analysis complete![/bold green]")
    console.print(f"- Processed {len(variables)} variables.")
    console.print(f"- Marked {secrets_found} variable(s) as secrets.")
    console.print(f"- Suggested {defaults_found} default value(s).")
    console.print(f"- Inferred a type (int/port/bool/url/email) for {types_found} variable(s).")

    return header + toml.dumps(schema_dict)
