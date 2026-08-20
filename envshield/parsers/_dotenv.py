# envshield/parsers/_dotenv.py
# A simple parser for key-value .env files.
import os
import re
from typing import Dict, Set, Union

from ._base import BaseParser

_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")

# A commented-out line whose remainder (after stripping '#'/'export ') is
# exactly an identifier immediately followed by '=' -- i.e. it would parse
# as a real assignment if it weren't commented out. Deliberately stricter
# than the active-line parser's own "split on the first '='" rule: ordinary
# prose that happens to contain '=' (a URL query string, a parenthetical
# example inside a real comment) doesn't match this and is correctly not
# counted, since matching on '=' alone would be indistinguishable from any
# other comment that mentions an equals sign.
_COMMENTED_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")


class DotenvParser(BaseParser):
    """
    Parses traditional .env files.
    """

    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> Union[Set[str], Dict[str, str]]:
        """
        Extracts variable names from a .env file.
        - Ignores lines starting with '#' (comments).
        - Ignores empty lines.
        - Strips a leading 'export ' keyword (shell-style .env files).
        - Splits lines by the first '=' to get the key.
        - Strips matching surrounding quotes from values, and strips
          inline comments from unquoted values.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        variables = {}
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key:
                    variables[key] = self._parse_value(value.strip())

        return variables if get_values else set(variables.keys())

    @staticmethod
    def _parse_value(raw_value: str) -> str:
        """Strips matching surrounding quotes, or an inline comment if unquoted."""
        if (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in ("'", '"')
        ):
            return raw_value[1:-1]

        if raw_value[:1] in ("'", '"'):
            # An opening quote with no matching close (e.g. SECRET_KEY="abc,
            # never terminated) -- the two branches above only handle a
            # correctly *matched* pair, so this would otherwise fall through
            # to the plain-value path below and be returned with the
            # leading quote character baked into it as if it were real
            # content, corrupting the value (a secret's actual value would
            # then differ from what every command validates against it,
            # with nothing ever flagging the mismatch). Stripping the
            # leading quote is the same best-effort correction already
            # applied by the matched-pair case above, just without a
            # trailing quote to also remove.
            return raw_value[1:]

        return _INLINE_COMMENT_RE.sub("", raw_value).strip()

    @staticmethod
    def count_commented_out_assignments(file_path: str) -> int:
        """
        Counts commented-out lines that otherwise look exactly like a real
        'KEY=value' assignment, e.g. '#PAPERLESS_OCR_LANGUAGE=eng' -- a
        common self-hosted-project convention for documenting available
        settings as inactive examples. get_vars() correctly never treats
        these as live values; this exists so a caller (currently:
        importer.generate_schema_from_file) can warn that they exist
        instead of silently producing a schema that looks complete but is
        missing every documented-but-inactive setting.

        Never auto-imports or infers anything about these lines beyond
        their count -- deciding whether/how to treat a commented-out
        example as an optional schema entry is a separate, not-yet-made
        design decision.
        """
        if not os.path.exists(file_path):
            return 0

        count = 0
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("#"):
                    continue
                candidate = line.lstrip("#").strip()
                if candidate.startswith("export "):
                    candidate = candidate[len("export ") :].strip()
                if _COMMENTED_ASSIGNMENT_RE.match(candidate):
                    count += 1
        return count
