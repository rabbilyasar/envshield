# envshield/parsers/_dotenv.py
# A simple parser for key-value .env files.
import os
import re
from typing import Dict, Set, Union

from ._base import BaseParser

_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


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
        with open(file_path, "r") as f:
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
