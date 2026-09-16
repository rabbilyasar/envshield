# envshield/core/scanner.py
import difflib
import fnmatch
import logging
import os
import re
import shlex
import stat
from typing import Any, Dict, List, Optional

import questionary
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..config import manager as config_manager
from ..core.exceptions import EnvShieldException, SchemaNotFoundError
from ..utils import git_utils
from . import discovery

console = Console()
logger = logging.getLogger(__name__)

# Files (and, for importer.py's default-value suggestion path, individual
# values) larger than this are skipped rather than fully read/embedded --
# reading a multi-MB file/value in full, or baking one into a generated
# artifact verbatim, is a real cost (and, for a generated schema, a
# correctness problem) with no proportional benefit. Named and exported so
# any other caller with the same "don't fully process something this
# large" concern reuses this exact threshold instead of picking its own.
MAX_SCANNABLE_SIZE_BYTES = 1_000_000

SECRET_PATTERNS: List[Dict[str, str]] = [
    {
        # Value may be quoted (Python/JSON-style: KEY = "value") or bare
        # (dotenv-style: KEY=value) -- real .env files are conventionally
        # unquoted, so requiring quotes here used to make this pattern blind
        # to the exact file format EnvShield exists to protect. The unquoted
        # branch is bounded on both sides so it can't start/stop mid-token.
        "name": "Generic API Key",
        # No leading lookbehind on the unquoted branch: the value's start is
        # already unambiguously anchored by the preceding literal '[:=]\s*'
        # (unlike the AWS pattern below, which has no such anchor). Adding
        # one here would misfire on the single most common shape -- 'KEY='
        # with no space -- since '=' is itself a member of the value charset.
        #
        # Two regressions found on a real site codebase, both now fixed:
        #
        # 1. The keyword group used to have no boundary at all, so it
        # matched as a mid-word substring -- e.g. "auth" inside
        # "work_authorization", "key" inside "monkey" -- flagging an
        # ordinary Python type annotation (`work_authorization:
        # WorkAuthorization`) as a secret. (?<![a-z])...(?![a-z]) requires
        # the keyword to start and end its own word, not continue into (or
        # out of) more letters -- '_'/start-of-line/'='/':' are valid
        # boundaries, another letter is not. This mirrors
        # importer.py's key_contains_secret_keyword token-based philosophy,
        # but can't reuse its exact split("_")-based mechanism: the scanner
        # matches arbitrary line text, not an already-isolated variable
        # name, so a boundary check is the equivalent primitive here. The
        # four explicit no-underscore compounds (accesskey/secretkey/
        # authtoken/apikey) are listed before their single-word components
        # so they match as one fused unit -- same compounds importer.py's
        # SECRET_KEY_KEYWORDS already special-cases, for the same reason.
        #
        # 2. The unquoted branch's trailing exclusion only ruled out
        # stopping mid-token (another identifier char immediately
        # following); it didn't rule out stopping right before a '(' --
        # so a bare function name in a call (`cache_key =
        # compute_fit_cache_key(...)`) matched as if the call itself were
        # the secret value. Adding '(' to that exclusion set means a value
        # immediately followed by an opening paren -- i.e. actually a
        # function call, not a literal -- is never treated as a secret.
        #
        # Broader real-world validation (phineas, Zeus) found the same
        # underlying class through three more syntactic shapes, each an
        # equally strong signal that what follows is code, not a literal:
        # ')' -- the match is actually an ARGUMENT inside an already-open
        # call (`s.loads(token, max_age=...)`), not the call target itself;
        # '[' -- a subscript/dict lookup (`settings["api_key"]`); '<' -- a
        # generic/type-parameter list (TypeScript's `api:
        # DialogInstanceApi<T>`, found in a vendored .d.ts file, confirming
        # this isn't Python-specific). All three are added to the same
        # exclusion set '(' already established.
        #
        # '.' needs a narrower rule than the other three: unlike them, a
        # real unquoted secret can legitimately precede a literal '.' -- a
        # sentence-ending period in prose ("the key is abc123...xyz.").
        # Blanket-excluding '.' would risk a false NEGATIVE on exactly that
        # case. What's actually evidence of code, confirmed by phineas'
        # `SessionValueType.BLOB`-style attribute access, is a '.'
        # immediately continuing into another identifier -- so only that
        # is excluded ('\.[a-zA-Z_]'), leaving a '.' followed by
        # whitespace, end-of-string, or punctuation (an ordinary sentence
        # ending) unaffected.
        #
        # The bracket/paren exclusions tolerate whitespace before them
        # ('\s*[([<)]', not a bare character class) -- a real call/
        # subscript/generic can have space before its closing punctuation
        # (`call(  token = value  )`), and rejecting only the no-space
        # form would make the fix depend on one exact whitespace style.
        #
        # Real-world precision/recall measurement (Zeus, Issuebear,
        # Issuebear Management) found two more issues, both in the QUOTED
        # branch only -- the unquoted branch is deliberately left
        # untouched, since bare identifiers are already a much larger
        # false-positive surface (Milestones 1-2 above) and loosening it
        # further would compound that risk with no offsetting evidence:
        #
        # 3. The quoted branch's 16-char minimum missed real, hardcoded
        # short passwords -- confirmed via literal 'MYSQL_PASSWORD'/
        # 'MYSQL_ROOT_PASSWORD' values (8 and 13 chars) in committed
        # docker-compose files, identically in three separate repositories.
        # Lowered to 8 -- the smallest minimum that catches both confirmed
        # cases -- rather than guessing lower with no evidence behind it.
        #
        # 4. A recurring false positive: a '*_KEY'-named constant or
        # keyword argument (`SESSION_STATE_KEY = "xero_oauth_state"`,
        # `get_config(key="tourradar_username")`) whose quoted value is
        # itself a configuration/lookup NAME, not a credential -- 12
        # confirmed instances in Zeus alone. Every one of those 12 values
        # was pure ASCII letters/underscores with consistent casing (all
        # lower or all UPPER, e.g. 'xero_oauth_state', 'TOURRADAR_USERNAME')
        # and contained no digit; every real secret checked alongside them
        # (API_ADMIN_TOKEN, APP_SECRET, GOOGLE_API_KEY, AWS_ACCESS_KEY_ID,
        # etc.) contained at least one digit, with no exceptions found. The
        # two negative lookaheads below reject a quoted value only when
        # it's ENTIRELY lowercase-letters-and-underscores or ENTIRELY
        # UPPERCASE-LETTERS-AND-UNDERSCORES end to end -- i.e. shaped like
        # an identifier/name, not a generated token. A value containing
        # even one digit, one mixed-case pair, or any other character is
        # unaffected and still matches exactly as before. This is
        # deliberately narrower than "no digits" alone would be: a mixed-
        # case no-digit string (rare in this corpus, not evidenced either
        # way) is NOT excluded, only the two specific all-one-case shapes
        # actually observed causing false positives.
        #
        # '(?-i:...)' around each lookahead's character class: the whole
        # pattern runs under the leading '(?i)', which would otherwise make
        # '[a-z_]+' and '[A-Z_]+' identical to each other and to
        # '[A-Za-z_]+' -- silently broadening "all one case" into "all
        # letters, any case" and excluding real mixed-case secrets that
        # contain no digit. Scoping case-sensitivity back on for just these
        # two groups keeps the distinction the evidence actually supports:
        # only a value that is ENTIRELY one actual case is excluded.
        #
        # 5. Real-world validation found 5 more false positives past #4's
        # all-one-case rule: a value that's digit-free and clearly
        # word-segmented -- camelCase/PascalCase ('reportName',
        # 'layoutModeKey') or hyphen-separated words ('session-cookie',
        # a non-secret itsdangerous 'salt="user-login"' argument). Every
        # one of the 5 confirmed instances decomposed into segments of 4+
        # letters; every real secret checked alongside them (including one,
        # a real Google API key, that itself has 20+ internal case
        # transitions) contains at least one digit -- the four new
        # lookaheads below require the value to be ENTIRELY letters (no
        # digit ends the match early) AND cleanly segmented, so they cannot
        # exclude a real secret in this corpus regardless of its casing.
        #
        # Each segment (the leading run and every run after a capital, or
        # after a hyphen) must be 2+ letters, not 1+ -- confirmed necessary
        # by direct testing: a 1+ minimum also matches a string like
        # 'aZxQkLpmBvCdEfGhJk' (alternating single-letter case flips, the
        # exact shape a real generated token can produce), which is not a
        # real word boundary and must not be excluded. The 2+ floor is
        # still comfortably below the 4+ actually observed in every
        # confirmed false positive, so it isn't a tight fit to the evidence.
        "pattern": (
            r"(?i)(?<![a-z])(accesskey|secretkey|authtoken|apikey|api(?!version)|key|token|secret|password|auth|credential)(?![a-z])"
            r"[a-z0-9_ .\-,]{0,25}\s*[:=]\s*"
            r"(?:['\"]"
            r"(?!(?-i:[a-z_]+)['\"])(?!(?-i:[A-Z_]+)['\"])"
            r"(?!(?-i:[a-z]{2,}(?:[A-Z][a-z]{2,})+)['\"])"
            r"(?!(?-i:[A-Z][a-z]{2,}(?:[A-Z][a-z]{2,})*)['\"])"
            r"(?!(?-i:[a-z]{2,}(?:-[a-z]{2,})+)['\"])"
            r"(?!(?-i:[A-Z]{2,}(?:-[A-Z]{2,})+)['\"])"
            r"[0-9a-zA-Z\-_=]{8,64}['\"]"
            # Named so BL-129's file-local suppression check can read back
            # exactly the unquoted value that matched, without re-deriving
            # it (fragile: the value's own charset includes '=', so a
            # naive re-scan from the end of the match can't reliably tell
            # the value apart from a keyword like 'key=' immediately
            # preceding it). Purely observational -- doesn't change what
            # matches, only exposes what already did.
            r"|(?P<unquoted_value>[0-9a-zA-Z\-_=]{16,64})(?![0-9a-zA-Z\-_=]|\s*[([<)]|\.[a-zA-Z_]))"
        ),
    },
    {
        # Matches either the header or footer line, in case one was
        # deliberately stripped from a leaked key blob.
        "name": "Private Key",
        "pattern": r"-----(?:BEGIN|END) (?:EC|PGP|DSA|RSA|OPENSSH|ENCRYPTED)? ?PRIVATE KEY(?: BLOCK)?-----",
    },
    {
        "name": "JSON Web Token (JWT)",
        "pattern": r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    },
    {
        "name": "Database Connection String",
        # "postgres(?:ql)?" -- SQLAlchemy/Django/psycopg and most modern
        # Python drivers require the "postgresql://" scheme specifically;
        # "postgres://" alone (the shorter, older/node-style form) used to
        # be the only variant this matched, silently missing the single most
        # common real-world connection-string scheme.
        #
        # "(?<![a-zA-Z])" before the scheme alternation, "mariadb" added to
        # it, and "(?:\+[a-zA-Z0-9_]+)?" after it -- found on real Zeus/
        # issuebear/issuebear-management codebases: SQLAlchemy's
        # "dialect+driver://" syntax (e.g. "mysql+pymysql://",
        # "mariadb+pymysql://") was never modeled, so the only reason
        # "mysql+pymysql://" or "mariadb+pymysql://" ever matched at all was
        # an accidental substring match of "mysql" inside "pymysql" landing
        # right before "://". That accident fires just as easily when the
        # embedded "credentials" are actually unresolved Python f-string
        # placeholders (e.g. "{db_user}:{db_pass}"), producing a false
        # positive. Explicitly modeling the real dialect+driver grammar (with
        # a word-boundary lookbehind so "mysql" can't match as a substring of
        # an unrelated word) fixes the false positive without losing the
        # driver forms themselves -- "mariadb+pymysql://user:realpass@host"
        # with genuinely hardcoded credentials still matches, and
        # "postgresql+psycopg2://" is now recognized too (previously missed
        # entirely).
        #
        # "[^@{}]+" (was "[^@]+") for the password segment -- excludes "{"
        # and "}" from the password specifically, so a literal Python
        # f-string placeholder ("{db_pass}") can never be mistaken for a
        # real password. The username segment is deliberately left
        # unrestricted ("[^:]+"): a templated username next to a genuinely
        # hardcoded password (e.g. "{db_user}:realpass123@...") must still
        # be flagged, since the actual secret -- the password -- is real.
        # Known accepted tradeoff: a literal password that itself contains
        # "{" or "}" (rare) will no longer match; this mirrors the existing,
        # separately-tracked special-character recall gap in the Generic
        # API Key pattern rather than introducing a new class of problem.
        "pattern": (
            r"(?i)(?<![a-zA-Z])"
            r"(postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)"
            r"(?:\+[a-zA-Z0-9_]+)?://[^:]+:[^@{}]+@"
        ),
    },
    {
        "name": "URL with Embedded API Key (DSN-style)",
        # Generalizes the same "credential embedded in a URL's userinfo
        # segment" concept above to a DSN-style URL that carries a single
        # long token instead of a user:pass pair (e.g. a Sentry DSN:
        # https://<32-hex-char key>@o123456.ingest.sentry.io/789). Confirmed
        # via real onboarding testing: this shape fell through both this
        # file's own patterns and importer.py's DATABASE_URL/SENTRY_DSN-
        # style name-keyword heuristic, letting a real key get written as a
        # schema defaultValue into both env.schema.toml and .env.example.
        # The 20+ hex-char minimum is what keeps this from matching an
        # ordinary 'https://user@host' URL, where the userinfo segment is a
        # short, human-readable name rather than a generated token.
        "pattern": r"(?i)https?://[0-9a-f]{20,64}@[a-z0-9.-]+\.[a-z]{2,}",
    },
    {
        "name": "AWS Access Key ID",
        "pattern": r"\b(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b",
    },
    {
        "name": "AWS Secret Access Key",
        # Boundary checks deliberately exclude '=' (unlike the value charset
        # itself, which includes it for base64 padding): AWS secrets are
        # typically written straight after a bare '=' with no space, and '='
        # is also a legal trailing content char, so treating it as "still
        # part of a token" here would reject the exact 'KEY=<secret>' shape
        # this branch exists to catch.
        "pattern": r"(?i)aws(.{0,20})?(?:['\"][0-9a-zA-Z\/+=]{40}['\"]|(?<![0-9a-zA-Z\/+])[0-9a-zA-Z\/+=]{40}(?![0-9a-zA-Z\/+]))",
    },
    {"name": "Google Cloud API Key", "pattern": r"\bAIza[0-9A-Za-z\-_]{35}\b"},
    {"name": "Google OAuth Access Token", "pattern": r"\bya29\.[0-9A-Za-z\-_]+\b"},
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
    {"name": "Slack Token", "pattern": r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b"},
    {"name": "Telegram Bot Token", "pattern": r"\b[0-9]{8,10}:[a-zA-Z0-9_-]{35}\b"},
    {"name": "Twilio API Key", "pattern": r"\bSK[0-9a-fA-F]{32}\b"},
    {
        "name": "SendGrid API Key",
        "pattern": r"\bSG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}\b",
    },
    {"name": "Mailchimp API Key", "pattern": r"\b[0-9a-f]{32}-us[0-9]{1,2}\b"},
    {"name": "Mailgun API Key", "pattern": r"\bkey-[0-9a-zA-Z]{32}\b"},
    {
        # Only the 'sk_' (secret) prefix -- 'pk_' is Stripe's *publishable*
        # key, explicitly meant to ship in client-side code (e.g. a
        # NEXT_PUBLIC_/VITE_-prefixed var). Flagging it as a secret was a
        # false positive on exactly the values that are supposed to be public.
        "name": "Stripe Secret Key",
        "pattern": r"\bsk_(test|live)_[0-9a-zA-Z]{24,99}\b",
    },
    {
        "name": "Heroku API Key",
        "pattern": r"(?i)heroku[a-z0-9_\- ]*['\"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['\"]",
    },
    {
        "name": "Discord Bot Token",
        "pattern": r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}\b",
    },
    {"name": "npm Token", "pattern": r"\bnpm_[a-zA-Z0-9]{36}\b"},
    {
        "name": "PyPI Upload Token",
        "pattern": r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9-_]{50,1000}\b",
    },
]
# _scan_single_file's hot loop calls re.search() once per (line, pattern) --
# millions of times on a large repo (25M+ calls measured on a ~2,900-file
# real-world monorepo). re.search(pattern_string, line) re-hashes the
# pattern string and looks it up in re's internal compile cache on every
# single call; pre-compiling once here and calling Pattern.search(line)
# instead skips that per-call lookup entirely. SECRET_PATTERNS itself keeps
# its existing List[Dict[str, str]] shape unchanged -- importer.py and
# several tests call re.search(p["pattern"], ...) directly against it -- this
# is purely an internal fast path for this module's own hot loop.
_COMPILED_SECRET_PATTERNS = [
    (secret["name"], re.compile(secret["pattern"])) for secret in SECRET_PATTERNS
]

# Real-world recall gap (Zeus, Issuebear, Issuebear Management, all three
# identically): Docker Compose's own list-style 'environment:' syntax
# (`- MYSQL_PASSWORD=password`) writes literal credentials UNQUOTED, so they
# never reach Generic API Key's quoted branch at all, and the unquoted
# branch's 16-char floor (a floor that exists specifically because bare
# identifiers/references are a large false-positive surface in ordinary
# Python/JS source -- see Milestones 1-2) is too high for a real, short,
# hardcoded password (confirmed misses: 8 and 13 chars).
#
# Broadening the *generic* unquoted floor was considered and rejected:
# investigation found a real counter-example (a CodeBuild buildspec's
# 'parameter-store:' mapping maps a credential-named key to an SSM
# *path*, not a value) that would become a false positive under a general
# "short value near a credential keyword" rule. What's actually safe to
# recognize is much narrower: this exact, well-known file naming
# convention, combined with a complete line that has no other plausible
# reading. `_scan_single_file` already has the file path available -- no
# parser is introduced, only a second, self-contained, file-name-gated
# check.
#
# Deliberately keeps the SAME keyword requirement as Generic API Key
# (reused, not duplicated) rather than matching every '- KEY=value' entry
# in a Compose file: the exact same environment blocks that contain the
# two confirmed misses also contain 'MYSQL_USER=local' and
# 'REDIS_REPLICATION_MODE=master' on immediately adjacent lines -- neither
# is a credential, and a keyword-free rule would flag both. Requiring the
# same word-bounded keyword this file already uses elsewhere targets
# exactly the shape that's missing, without inventing a new "any
# environment variable is interesting" detector.
#
# Anchored to the WHOLE line ('^...$', tolerating only surrounding
# whitespace) so it cannot match a prefix of something else -- a quoted
# entire-entry ('- "traefik.enable=true"'), a mapping-style line
# ('KEY: value', no leading '-'), or a '${VAR}' reference (excluded by the
# value charset itself, which contains no '$', '{', or '}') all fail to
# match, confirmed via direct testing against real examples of each shape
# in this exact corpus. The value charset (alphanumeric, '-', '_', '.') is
# deliberately narrow -- the evidence (8/13-char plain-word passwords)
# doesn't call for anything broader, and a lower floor (4, versus the
# generic branches' 8/16) is defensible specifically because the line
# shape itself is already strong evidence, leaving little room for an
# ordinary short word to collide.
_DOCKER_COMPOSE_FILENAME_RE = re.compile(r"^docker-compose.*\.ya?ml$", re.IGNORECASE)
_COMPOSE_ENV_LIST_ENTRY_RE = re.compile(
    r"^\s*-\s+"
    r"(?=[A-Z][A-Z0-9_]*=)"
    r"(?=[A-Z0-9_]*(?i:(?<![a-z])(?:accesskey|secretkey|authtoken|apikey|"
    r"api(?!version)|key|token|secret|password|auth|credential)(?![a-z]))"
    r"[A-Z0-9_]*=)"
    r"[A-Z][A-Z0-9_]*=([0-9a-zA-Z\-_.]{4,64})\s*$"
)


def _is_docker_compose_file(file_path: str) -> bool:
    return bool(_DOCKER_COMPOSE_FILENAME_RE.match(os.path.basename(file_path)))


# Directories that are never useful to scan and are expensive/noisy to walk:
# dependency trees, VCS internals, virtualenvs, and build artifacts. These are
# always pruned in addition to whatever the user configures in envshield.yml.
#
# "vendor" was added after real-world scanning turned up FPs exclusively in
# third-party vendored code (a minified Private Key stub, a minified plugin
# bundle, and a vendored TypeScript .d.ts's type-signature parameters) with
# zero confirmed real credentials ever found under a vendor/ path across the
# validated corpus -- the same noisy-dependency-tree reasoning as
# node_modules above, not a new exclusion category. Matched by exact
# directory name (see _is_default_excluded_dir), so "my_vendor" or
# "vendored" are untouched -- only a path component literally named
# "vendor" is pruned, at any depth.
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "vendor",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
}


def _is_default_excluded_dir(dirname: str) -> bool:
    return (
        dirname in DEFAULT_EXCLUDED_DIRS
        or dirname.endswith(".egg-info")
        or dirname.endswith(".dist-info")
    )


# O_NOFOLLOW doesn't exist in the os module at all on Windows (creating a
# real filesystem symlink there also requires a privilege an ordinary user
# doesn't have by default, unlike POSIX) -- checked once at import time
# rather than via a try/except on every call.
_CAN_USE_O_NOFOLLOW = hasattr(os, "O_NOFOLLOW")


def _open_for_scan(file_path: str):
    """
    Opens a file for scanning -- the actual security boundary against a
    symlink that appears after _collect_files_to_scan's own islink() checks
    already ran and let the path through (a real, if narrow, TOCTOU
    window: those checks and this open are separate syscalls, with every
    other file ahead of this one in the scan list executing in between).

    On a platform with O_NOFOLLOW, the kernel refuses to open the path if
    its *final* path component is a symlink, as part of this single
    open() syscall -- there's no separate check to race against, because
    the check and the read happen atomically together. If the path became
    a symlink after collection, this raises OSError (ELOOP), which the
    caller already handles the same way as any other unreadable file --
    the scan skips that one file and continues, it doesn't crash.

    On a platform without O_NOFOLLOW (Windows), this falls back to a plain
    open with no symlink protection -- an accepted, documented gap there
    rather than a silent one, and one the base attack surface is already
    narrower against by default (see module-level note above). Deliberately
    out of scope: a symlinked *ancestor directory* swapped mid-walk (would
    need dir_fd-relative opens component by component) and the unrelated
    file-size TOCTOU on the >1MB skip check -- both tracked separately, not
    fixed here.
    """
    flags = os.O_RDONLY
    if _CAN_USE_O_NOFOLLOW:
        flags |= os.O_NOFOLLOW
    fd = os.open(file_path, flags)
    return os.fdopen(fd, "r", encoding="utf-8", errors="ignore")


def _redact_match(matched_text: str) -> str:
    """
    Turns a matched secret span into a safe, non-reversible preview: the
    character count only, never any part of the value itself.

    No boundary characters are shown, at any length. For a short secret (a
    placeholder, a short token) even one or two boundary characters can be
    a large fraction of the whole value, so there's no length threshold
    above which partial disclosure becomes safe enough to bother with --
    length alone is still enough to tell a real-looking key apart from an
    empty or placeholder value.
    """
    return f"<redacted, {len(matched_text)} chars>"


def _get_diff_lines(file_path: str) -> Optional[set]:
    """Get line numbers that are newly added in the staged version.

    Compares the staged version against HEAD to find lines that are new in
    this commit (present in staged but not in HEAD).

    Args:
        file_path: Path to the file to check

    Returns:
        set: Line numbers (1-indexed) that are newly added
        None: If the file doesn't exist in HEAD (brand new file) - scan all lines
        empty set: If there are no new lines
    """
    try:
        head_content = git_utils.get_head_file_content(file_path)

        # Brand new file - return None to indicate "scan all lines"
        if head_content is None:
            return None

        staged_content = git_utils.get_staged_file_content(file_path)
        if staged_content is None:
            return set()

        # Extract lines
        head_lines = head_content.splitlines()
        staged_lines = staged_content.splitlines()

        # A positional diff, not a content-set comparison: matching by exact
        # line text alone would treat a genuinely new line as "pre-existing"
        # whenever some unrelated line elsewhere in the file happens to have
        # identical text (e.g. a repeated comment or template block) --
        # letting a real new secret hide behind a coincidental text match.
        matcher = difflib.SequenceMatcher(
            None, head_lines, staged_lines, autojunk=False
        )
        new_line_numbers = set()
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                new_line_numbers.update(range(j1 + 1, j2 + 1))

        return new_line_numbers
    except Exception:
        # On any error, return empty set (conservative - don't scan)
        return set()


def _display_path(file_path: str) -> str:
    """
    Normalizes a collected file path to one consistent, cwd-relative form
    for display. Without this, a single-file argument (os.path.abspath'd)
    showed an absolute path, a directory walk of '.' showed a './'-prefixed
    relative path, and '--staged' (absolute, via git_utils.get_staged_files)
    showed yet a third form -- all for files in the same scan, and none
    matching 'undeclared's already-consistent relative style.
    """
    try:
        return os.path.relpath(file_path, os.getcwd())
    except ValueError:
        return file_path  # e.g. different drive on Windows -- not relatable


def _record_discovered_usages(
    undeclared_findings: List[Dict],
    usages,
    schema_vars: set,
    new_lines_only: Optional[set],
) -> None:
    """Shared adapter for both discovery.py engines: filters by
    new_lines_only and schema_vars, then appends in the same finding shape
    the (now-removed) per-line regex loop always used -- callers of
    _scan_single_file see no difference.

    BL-113: a medium-confidence usage (currently, a Flask
    current_app.config[...] read) never contributes to this list --
    'scan's undeclared-variable detection, like 'undeclared' itself, is a
    binary completeness/pre-commit-safe signal, and a finding built on
    "this might be an environment-sourced read, unproven" doesn't belong
    in that class of result. Medium-confidence usages remain visible only
    through 'explain', where a human reads the caveat directly.
    """
    for usage in usages:
        if usage.confidence != "high":
            continue
        if new_lines_only is not None and usage.line not in new_lines_only:
            continue
        if usage.variable not in schema_vars:
            undeclared_findings.append(
                {
                    "file_path": _display_path(usage.file_path),
                    "line_num": usage.line,
                    "variable_name": usage.variable,
                }
            )


# BL-129: file-local bare-identifier reference suppression -- Generic API
# Key's unquoted branch on a Python file only. Deliberately NOT a symbol
# table or parser: a real-world investigation (see this session's own
# milestone report) found the dominant remaining false-positive class was
# unquoted Python code -- `token=existing_token`, `client, api =
# some_fixture` -- being read as if the bare identifier were itself a
# secret literal. Every real credential in the validated corpus is a
# QUOTED string literal; none is an unquoted bare identifier, so this
# mechanism can only ever act on a shape no known real secret has.
#
# The rule is evidence-gated, not shape-gated (unlike BL-124/BL-126, which
# exclude on casing/segmentation alone): a bare-identifier candidate is
# suppressed ONLY when this exact same file positively shows, within the
# candidate's own enclosing function, either (A) the identifier is
# assigned from something other than a quoted string literal, or (B) the
# identifier is a parameter of that enclosing function. Absence of
# evidence never suppresses -- an unresolved identifier, an identifier
# whose only local definition IS a literal (`password = "..."; api =
# password` must stay detected), and module-level code with no enclosing
# function are all left flagged exactly as before.
#
# Scope is bounded by indentation, not real parsing: scanning backward
# from the candidate line for the nearest shallower `def` line
# approximates "the enclosing function" the same way a simple linter
# would, without tracking real block structure. This deliberately means a
# same-named identifier in an unrelated, non-enclosing function elsewhere
# in the file is never consulted -- evidence is drawn only from the one
# function actually enclosing the candidate.
_BARE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEF_LINE_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+\w+\s*\(")


def _find_enclosing_function_body(lines: List[str], line_num: int):
    """
    Returns (def_line_idx, body_start_idx, body_end_idx) -- 0-based indices
    into `lines` -- for the function whose body encloses `line_num` (1-based),
    approximated purely by indentation (no AST, no real block tracking).
    Returns None if no enclosing `def` is found (e.g. module-level code).
    """
    candidate = lines[line_num - 1]
    min_indent = len(candidate) - len(candidate.lstrip())

    for i in range(line_num - 2, -1, -1):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(")"):
            # The tail of a multi-line signature (or any other multi-line
            # construct) closing at column 0 -- e.g. ') -> str:' -- is not
            # itself a new, shallower statement; treating its own literal
            # indentation as a container would stop the backward scan
            # before it ever reaches the real 'def' line further up.
            continue
        indent = len(line) - len(line.lstrip())
        if indent < min_indent:
            if _DEF_LINE_RE.match(line):
                def_indent = indent
                # The function's own signature may itself span multiple
                # lines (e.g. one parameter per line) before the body
                # actually starts -- skip forward past every line that's
                # still part of that opening statement (tracked by paren
                # depth) so a parameter line's shallow indentation, or the
                # closing ') -> ReturnType:' line, is never mistaken for
                # the body already having ended.
                depth = line.count("(") - line.count(")")
                body_actually_starts = i + 1
                while depth > 0 and body_actually_starts < len(lines):
                    sig_line = lines[body_actually_starts]
                    depth += sig_line.count("(") - sig_line.count(")")
                    body_actually_starts += 1

                body_end = len(lines)
                for j in range(body_actually_starts, len(lines)):
                    later = lines[j]
                    if not later.strip():
                        continue
                    if len(later) - len(later.lstrip()) <= def_indent:
                        body_end = j
                        break
                return i, body_actually_starts, body_end
            # A shallower non-'def' line (an 'if'/'class'/'for' header, a
            # decorator, ...) isn't the enclosing function itself -- keep
            # scanning backward, but only ever compare against the new,
            # shallower indent level from here on.
            min_indent = indent

    return None


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """Splits on `sep` only outside any (), [], {} nesting."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _has_non_literal_local_assignment(
    lines: List[str], body_start: int, body_end: int, identifier: str
) -> bool:
    """
    True if `identifier` is assigned, as a plain statement-level target
    somewhere in lines[body_start:body_end], to something that does not
    itself start with a quote. A value whose ONLY local definition is a
    quoted literal (`password = "real-looking-secret"`) does not count --
    the point is exactly to keep `api = password` detectable in that case.
    """
    pattern = re.compile(r"^\s*" + re.escape(identifier) + r"\s*=(?!=)\s*(?!['\"])\S")
    return any(pattern.match(lines[i]) for i in range(body_start, body_end))


def _identifier_is_function_parameter(
    lines: List[str], def_line_idx: int, identifier: str
) -> bool:
    """
    True if `identifier` is a parameter name in the `def` signature starting
    at lines[def_line_idx] -- gathered across multiple lines via paren-depth
    balancing if the signature itself spans more than one line.
    """
    text = ""
    depth = 0
    started = False
    for j in range(def_line_idx, len(lines)):
        text += lines[j]
        for ch in lines[j]:
            if ch == "(":
                depth += 1
                started = True
            elif ch == ")":
                depth -= 1
        if started and depth <= 0:
            break

    match = re.search(r"\((.*)\)", text, re.DOTALL)
    if not match:
        return False

    for part in _split_top_level(match.group(1)):
        name_match = re.match(r"^\s*\*{0,2}([A-Za-z_][A-Za-z0-9_]*)", part)
        if name_match and name_match.group(1) == identifier:
            return True
    return False


def _should_suppress_bare_identifier_reference(
    match: "re.Match", lines: List[str], line_num: int
) -> bool:
    """
    Milestone B (BL-129): suppress a Generic API Key match ONLY when this
    exact value is an unquoted, plain-identifier-shaped candidate AND this
    same file's own enclosing-function evidence positively shows it's a
    non-literal reference (assignment or parameter), never on shape or
    absence of evidence alone.
    """
    # 'unquoted_value' is only present when the unquoted branch matched --
    # None here means the quoted branch matched, which is never eligible.
    # (Only ever called for a "Generic API Key" match, so the named group
    # always exists syntactically; it's just unset for the quoted branch.)
    value = match.group("unquoted_value")
    if not value:
        return False
    if not _BARE_IDENTIFIER_RE.match(value):
        # Contains '-' or '=' -- not a syntactically valid Python
        # identifier, so it can never be "a reference" in the first place.
        return False

    scope = _find_enclosing_function_body(lines, line_num)
    if scope is None:
        return False
    def_line_idx, body_start, body_end = scope

    if _has_non_literal_local_assignment(lines, body_start, body_end, value):
        return True
    return _identifier_is_function_parameter(lines, def_line_idx, value)


def _classify_and_maybe_suppress(
    line: str,
    match_start: int,
    match_end: int,
    match_object,
    detector_name: str,
    file_path: str,
    line_num: int,
) -> Optional[str]:
    """
    Classifies a match using the context classifier. Returns a suppression
    reason string if the match should be suppressed (LIKELY_CODE), or None
    if it should remain a finding (LIKELY_SECRET or AMBIGUOUS).

    The classifier is lazily imported to avoid initialization cost when it's
    not needed.

    Args:
        line: Source line containing the match
        match_start: Character offset of match start (0-indexed)
        match_end: Character offset of match end (exclusive)
        match_object: The re.Match object (needed for unquoted_value group)
        detector_name: Name of the detector that matched
        file_path: File being scanned (for logging)
        line_num: Line number (1-indexed)

    Returns:
        str: Suppression reason if LIKELY_CODE (e.g., "function keyword argument")
        None: If LIKELY_SECRET or AMBIGUOUS (retain as finding)
    """
    # Lazy import to avoid overhead when classifier isn't used
    from ..classifier import context_classifier

    # For Generic API Key with unquoted branch, classify only the value part
    # (not the "key=" prefix), since the classifier needs to analyze the
    # value's syntactic role (identifier vs literal, keyword arg vs assignment).
    classifier_start = match_start
    classifier_end = match_end
    try:
        unquoted_value = match_object.group("unquoted_value")
        if unquoted_value:
            # Find the value-only span within the full match
            value_start = line.index(unquoted_value, match_start)
            classifier_start = value_start
            classifier_end = value_start + len(unquoted_value)
    except (IndexError, AttributeError):
        # No unquoted_value group or it didn't match -- use full span
        pass

    classifier = context_classifier.ContextClassifier()
    result = classifier.classify(line, classifier_start, classifier_end)

    # Only suppress LIKELY_CODE (high-confidence FP)
    if result.classification == context_classifier.Classification.LIKELY_CODE:
        return (
            f"{result.reason} (confidence: {result.confidence}, rule: {result.rule_name})"
        )

    # LIKELY_SECRET and AMBIGUOUS remain findings
    return None


def _scan_single_file(
    file_path: str,
    schema_vars: set,
    content: Optional[str] = None,
    new_lines_only: Optional[set] = None,
) -> (List[Dict], List[Dict]):
    """
    Helper to scan one file for both secrets and undeclared variables.
    Returns two lists: one for secrets, one for undeclared variables.

    If `content` is provided, it's scanned directly instead of reading the
    file from disk — used for `--staged` scans, where we must scan what's
    actually staged in the Git index, not the working-tree copy (which can
    differ, e.g. if a secret was staged and then edited out without
    re-staging).

    If `new_lines_only` is provided, only those line numbers are scanned.
    Used for diff-aware scanning of excluded files.
    """
    secret_findings = []
    undeclared_findings = []

    try:
        if content is not None:
            lines = content.splitlines(keepends=True)
        else:
            with _open_for_scan(file_path) as f:
                lines = f.readlines()

        is_compose_file = _is_docker_compose_file(file_path)

        for line_num, line in enumerate(lines, 1):
            # If new_lines_only is specified, skip lines not in that set
            if new_lines_only is not None and line_num not in new_lines_only:
                continue

            # Check for secrets
            matched_generic = False
            for secret_name, compiled_pattern in _COMPILED_SECRET_PATTERNS:
                match = compiled_pattern.search(line)
                if match:
                    matched_generic = True
                    if (
                        secret_name == "Generic API Key"
                        and file_path.endswith(".py")
                        and _should_suppress_bare_identifier_reference(
                            match, lines, line_num
                        )
                    ):
                        # BL-129: positively evidenced as a non-secret
                        # reference within its own enclosing function --
                        # still "claims" the line (matched_generic stays
                        # True) so nothing lower in SECRET_PATTERNS piles a
                        # second finding onto the same line.
                        break

                    # Milestone 4: Context classifier suppression for Generic API Key
                    # Applies AFTER BL-129 (line-local takes precedence), Python only
                    # (tokenizer designed for source code, not .env files), and only
                    # suppresses LIKELY_CODE classifications (high-confidence FPs like
                    # function keyword arguments, type annotations, destructuring).
                    # LIKELY_SECRET and AMBIGUOUS remain findings.
                    if secret_name == "Generic API Key" and file_path.endswith(".py"):
                        suppression_result = _classify_and_maybe_suppress(
                            line, match.start(), match.end(), match, secret_name, file_path, line_num
                        )
                        if suppression_result:
                            logger.debug(
                                f"Suppressed {secret_name} at {_display_path(file_path)}:{line_num}: "
                                f"{suppression_result}"
                            )
                            break

                    secret_findings.append(
                        {
                            "file_path": _display_path(file_path),
                            "line_num": line_num,
                            "secret_type": secret_name,
                            # Only the matched span's length, never the raw
                            # line or any part of the matched value itself --
                            # see _redact_match.
                            "redacted_preview": _redact_match(match.group(0)),
                        }
                    )
                    break

            # Only tried when nothing above already matched -- a longer
            # Compose credential (e.g. a 60+ char access key) is already
            # caught by Generic API Key itself; this exists solely for the
            # short values that pattern's unquoted floor misses.
            if not matched_generic and is_compose_file:
                compose_match = _COMPOSE_ENV_LIST_ENTRY_RE.match(line)
                if compose_match:
                    secret_findings.append(
                        {
                            "file_path": _display_path(file_path),
                            "line_num": line_num,
                            "secret_type": "Docker Compose Environment Credential",
                            "redacted_preview": _redact_match(compose_match.group(0)),
                        }
                    )

        # Undeclared-variable detection for both Python and JS/TS runs once
        # over the whole file (not per line, like the secret loop above),
        # since a usage can span multiple lines -- see discovery.py.
        # Skipped entirely when new_lines_only is an explicitly empty set:
        # nothing could survive that filter anyway.
        if new_lines_only != set():
            full_text = "".join(lines)
            if file_path.endswith(".py"):
                _record_discovered_usages(
                    undeclared_findings,
                    discovery.discover_python_usages(full_text, file_path),
                    schema_vars,
                    new_lines_only,
                )
            elif file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
                _record_discovered_usages(
                    undeclared_findings,
                    discovery.discover_js_usages(full_text, file_path),
                    schema_vars,
                    new_lines_only,
                )

    except (IOError, OSError):
        return [], []

    return secret_findings, undeclared_findings


def _collect_files_to_scan(paths: Optional[List[str]], staged_only: bool) -> List[str]:
    """Collects a list of files to be scanned based on user input."""

    if staged_only:
        console.print("Scanning [yellow]staged files[/yellow]...")
        files = git_utils.get_staged_files()
        if not files:
            console.print("[green]No staged files to scan.[/green]")
            raise typer.Exit()
        return files

    files_to_scan = []
    scan_paths = paths or ["."]

    # A typo'd path must fail loudly, not silently scan zero files and
    # report "no issues found" -- that reads as "your code is clean" when
    # what actually happened is "nothing was scanned at all."
    missing_paths = [p for p in scan_paths if not os.path.exists(p)]
    if missing_paths:
        raise EnvShieldException(
            f"Path not found: {', '.join(missing_paths)}. Check for a typo."
        )

    if "." in scan_paths:
        console.print("Scanning [yellow]current directory[/yellow] recursively...")

    # A symlink can point anywhere on disk -- reading through one would let
    # a file outside the directory the caller actually asked to scan be
    # read (and its findings reported) as if it were part of the scan.
    # os.walk's own symlink protection (followlinks=False, the default)
    # only stops it recursing into a symlinked subdirectory *discovered
    # during* the walk -- it does nothing for a symlinked file leaf, and
    # nothing at all for the top-level path argument itself if that's a
    # symlink to a directory. Both are checked explicitly below so no
    # symlink -- however it's reached -- is ever opened.
    skipped_symlinks = []

    for path in scan_paths:
        if os.path.islink(path):
            skipped_symlinks.append(path)
            continue
        if os.path.isfile(path):
            files_to_scan.append(os.path.abspath(path))
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                # Prune in-place so os.walk doesn't descend into these dirs at all.
                dirs[:] = [d for d in dirs if not _is_default_excluded_dir(d)]
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.islink(file_path):
                        skipped_symlinks.append(file_path)
                        continue
                    files_to_scan.append(file_path)

    if skipped_symlinks:
        console.print(
            f"[dim]ℹ️  Skipping {len(skipped_symlinks)} symlink(s) -- not "
            "followed, to avoid reading a file outside the scanned "
            f"directory: {', '.join(sorted(skipped_symlinks))}[/dim]"
        )

    # A git-ignored file (a real '.env', chief among them) is never going
    # to be committed, so flagging a secret inside it as "DANGER" is pure
    # noise against what this command actually exists to prevent -- and
    # actively contradicts its own suggestion text, which tells you to
    # move secrets INTO that same gitignored file. '--staged' is
    # unaffected: if an ignored file somehow got staged anyway, that's a
    # real, imminent risk worth flagging, not noise.
    ignored = git_utils.get_ignored_files(files_to_scan)
    if ignored:
        console.print(
            f"[dim]ℹ️  Skipping {len(ignored)} git-ignored file(s) -- not "
            "committable, so not scanned: "
            f"{', '.join(sorted(ignored))}[/dim]"
        )
        files_to_scan = [f for f in files_to_scan if f not in ignored]

    return files_to_scan


def _filter_files(files: List[str], exclude_patterns: List[str]) -> List[str]:
    """Filters a list of files against a list of glob patterns."""
    final_files = []
    for file_path in files:
        is_excluded = False
        normalized_path = file_path.replace(os.getcwd() + os.sep, "")
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(normalized_path, pattern):
                is_excluded = True
                break
        if not is_excluded:
            final_files.append(file_path)
    return final_files


def _build_undeclared_var_resolver(service_name: Optional[str]):
    """
    Returns a function mapping a scanned file path to the schema variable
    set it should be checked against for undeclared-variable detection --
    or None if there's no schema at all to check against.

    An explicit `service_name` checks every file against that one schema --
    unchanged, single-target behavior.

    Otherwise, each configured service's own schema is matched against
    files under that service's own directory (the directory its schema
    lives in) -- a file under 'alpha/' is checked against alpha's schema,
    not beta's. A single-service project's one (and only) service has a
    directory of '.' (its schema lives at the project root), which matches
    every file -- there's no separate "root schema" concept to fall back to
    anymore: envshield.yml always has at least one registered service (see
    generate_default_config_content), single-service or not, so every
    schema is a service's schema. Without the directory matching below,
    running the pre-commit hook's plain `envshield scan --staged` (no
    --service) on a multi-service project would have no way to tell which
    service's schema applies to which file.
    """
    if service_name:
        try:
            schema_vars = set(
                config_manager.load_schema(service_name=service_name).keys()
            )
            console.print("[dim]Schema loaded for compliance check.[/dim]")
        except SchemaNotFoundError:
            console.print(
                "[yellow]Warning: Schema not found. Skipping undeclared variable check.[/yellow]"
            )
            return None
        return lambda _file_path: schema_vars

    service_dirs = []
    for name in sorted(config_manager.get_services().keys()):
        try:
            service_dir = config_manager.normalize_path_for_service_match(
                config_manager.get_service_dir(name)
            )
            schema_vars = set(config_manager.load_schema(service_name=name).keys())
        except SchemaNotFoundError:
            continue
        except EnvShieldException as e:
            # A broken schema in one service (e.g. mid-edit, unrelated to
            # what's actually staged) must not block undeclared-variable
            # checking -- or the commit itself, via 'scan --staged' -- for
            # every other service in the project. Real incident this
            # reproduces: a malformed env.schema.toml sitting in service
            # A's working tree silently blocked every commit touching
            # service B, even though B was never involved.
            console.print(
                f"[yellow]Warning: Could not load schema for service '{name}': {e} "
                f"Skipping its undeclared-variable check.[/yellow]"
            )
            continue
        service_dirs.append((service_dir, schema_vars))
    # Longest directory first, so a nested service dir wins over a shorter
    # sibling -- and so a single-service project's '.' entry only ever acts
    # as the last-resort catch-all it should be, not a premature match.
    service_dirs.sort(key=lambda item: len(item[0]), reverse=True)

    if not service_dirs:
        console.print(
            "[yellow]Warning: No services configured. Skipping undeclared variable check.[/yellow]"
        )
        return None

    console.print("[dim]Per-service schemas loaded for compliance check.[/dim]")

    def _resolve(file_path: str) -> set:
        for service_dir, schema_vars in service_dirs:
            if config_manager.service_dir_contains(file_path, service_dir):
                return schema_vars
        return set()

    return _resolve


def _scan_files(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
    service_name: Optional[str] = None,
):
    """
    Does the actual file collection and scanning, returning the raw
    (secret_findings, undeclared_findings, skipped_large_files) lists.

    Extracted from run_scan so both the Rich-rendering path and the
    '--json' path (see scan_result) share one implementation instead of
    two copies that could quietly drift apart.

    If `service_name` is provided, scans for variables against that service's schema.
    Otherwise, on a multi-service project, each file is checked against
    whichever service's schema its directory belongs to.
    """
    all_exclusions = []
    try:
        config = config_manager.load_config(config_path)
        config_exclusions = config.get("secret_scanning", {}).get("exclude_files", [])
        all_exclusions.extend(config_exclusions)
    except EnvShieldException:
        pass

    if exclude_patterns:
        all_exclusions.extend(exclude_patterns)

    schema_resolver = _build_undeclared_var_resolver(service_name)

    # An explicit --service checks every file it's given against that one
    # schema (see _build_undeclared_var_resolver above) -- correct when the
    # caller also gave an explicit path/file, but paths defaults to ["."]
    # (the whole project) when the caller gave none, and --staged always
    # collects every staged file project-wide regardless of paths. Without
    # scoping those two "no explicit path" cases down to the named
    # service's own directory, --service X with no path argument (the
    # natural, documented "scan just my service" invocation) silently
    # scanned the entire monorepo and flagged every OTHER service's
    # genuinely-declared variables as undeclared against X's schema --
    # confirmed via a real multi-service reproduction. An explicitly-given
    # path is left alone either way: that's a deliberate, existing choice
    # (e.g. checking one shared file against a specific service's schema
    # on purpose), not something to silently override.
    service_scope_dir = None
    if service_name:
        try:
            candidate_dir = config_manager.get_service_dir(service_name)
        except EnvShieldException:
            candidate_dir = None
        if candidate_dir and candidate_dir != ".":
            service_scope_dir = candidate_dir

    if service_scope_dir and not staged_only and not paths:
        paths = [service_scope_dir]

    files_to_scan = _collect_files_to_scan(paths, staged_only)

    if service_scope_dir and staged_only:
        files_to_scan = [
            f
            for f in files_to_scan
            if config_manager.service_dir_contains(f, service_scope_dir)
        ]

    # For staged scans: keep excluded files for diff-aware scanning
    # For non-staged scans: filter out excluded files as before
    if staged_only:
        final_files_to_scan = files_to_scan
        excluded_files = set()
        for pattern in all_exclusions:
            for file_path in files_to_scan:
                normalized_path = file_path.replace(os.getcwd() + os.sep, "")
                if fnmatch.fnmatch(normalized_path, pattern):
                    excluded_files.add(file_path)
    else:
        final_files_to_scan = _filter_files(files_to_scan, all_exclusions)
        excluded_files = set()

    all_secret_findings = []
    all_undeclared_findings = []
    skipped_large_files = []

    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("Scanning [cyan]{task.description}[/cyan]"),
        console=console,
    ) as progress:
        scan_task = progress.add_task("files...", total=len(final_files_to_scan))
        for file_path in final_files_to_scan:
            progress.update(
                scan_task, description=os.path.basename(file_path), advance=1
            )

            schema_vars = schema_resolver(file_path) if schema_resolver else set()

            if staged_only:
                # Scan what's actually staged in the index, not the working-tree
                # copy on disk -- they can differ (see get_staged_file_content).
                content = git_utils.get_staged_file_content(file_path)
                if content is None:
                    continue

                # Diff-aware scanning for excluded files -- resolved BEFORE
                # the size check (see BL-004): an excluded file's total
                # size must never matter unless the whole file is actually
                # about to be read. A bounded diff-only scan of an excluded
                # file is never "incomplete" just because the file itself
                # happens to be large (e.g. a vendored lockfile).
                new_lines_only = None
                is_excluded = file_path in excluded_files
                if is_excluded:
                    new_lines = _get_diff_lines(file_path)
                    if new_lines is not None:
                        if len(new_lines) == 0:
                            # File is excluded and has no new lines - skip it
                            continue
                        new_lines_only = new_lines

                # The size check applies only when the whole file's content
                # is about to be read: a non-excluded file, or an
                # excluded-but-brand-new one (new_lines_only still None --
                # scanned in full despite the exclusion, below).
                if new_lines_only is None and len(content) > MAX_SCANNABLE_SIZE_BYTES:
                    skipped_large_files.append(file_path)
                    continue

                if is_excluded:
                    if new_lines_only is not None:
                        # File is excluded, but scan only newly-added lines
                        console.print(
                            f"[dim]ℹ️  {os.path.basename(file_path)} (excluded; diffs only: {len(new_lines_only)} new line(s))[/dim]"
                        )
                    else:
                        # Brand new file - scan all lines despite exclusion
                        console.print(
                            f"[yellow]ℹ️  Scanning new file {os.path.basename(file_path)} (despite exclusion)[/yellow]"
                        )

                secrets, undeclared = _scan_single_file(
                    file_path,
                    schema_vars,
                    content=content,
                    new_lines_only=new_lines_only,
                )
            else:
                if (
                    os.path.exists(file_path)
                    and os.path.getsize(file_path) > MAX_SCANNABLE_SIZE_BYTES
                ):
                    skipped_large_files.append(file_path)
                    continue
                secrets, undeclared = _scan_single_file(file_path, schema_vars)

            all_secret_findings.extend(secrets)
            all_undeclared_findings.extend(undeclared)

    return all_secret_findings, all_undeclared_findings, skipped_large_files


def run_scan(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
    service_name: Optional[str] = None,
):
    """
    The main function to orchestrate the scanning process.

    If `service_name` is provided, scans for variables against that service's schema.
    Otherwise, on a multi-service project, each file is checked against
    whichever service's schema its directory belongs to.
    """
    all_secret_findings, all_undeclared_findings, skipped_large_files = _scan_files(
        paths, staged_only, config_path, exclude_patterns, service_name
    )

    if skipped_large_files:
        console.print(
            f"\n[bold yellow]⚠️  Skipped {len(skipped_large_files)} file(s) over 1MB (not scanned -- coverage is incomplete for these):[/bold yellow]"
        )
        for skipped_path in skipped_large_files:
            console.print(f"    [dim]{skipped_path}[/dim]")

    found_issues = False
    if all_secret_findings:
        found_issues = True
        console.print(
            f"\n[bold red]🚨 DANGER: Found {len(all_secret_findings)} potential secret(s)![/bold red]"
        )
        table = Table(title="Secret Scan Results", border_style="red")
        table.add_column("File", style="cyan")
        table.add_column("Line", style="yellow")
        table.add_column("Secret Type", style="magenta")
        table.add_column("Preview", style="white")
        for finding in all_secret_findings:
            table.add_row(
                finding["file_path"],
                str(finding["line_num"]),
                finding["secret_type"],
                finding["redacted_preview"],
            )
        console.print(table)
        console.print(
            "\n[bold]Suggestion:[/bold] Remove the secret from this file and move it "
            "to '.env' (gitignored) instead. If it's a known false positive, add a "
            "targeted '--exclude' glob or a 'secret_scanning.exclude_files' entry in envshield.yml."
        )

    if all_undeclared_findings:
        found_issues = True
        console.print(
            f"\n[bold yellow]⚠️  WARNING: Found {len(all_undeclared_findings)} undeclared variable(s)![/bold yellow]"
        )
        undeclared_table = Table(
            title="Undeclared Variable Usage", border_style="yellow"
        )
        undeclared_table.add_column("File", style="cyan")
        undeclared_table.add_column("Line", style="yellow")
        undeclared_table.add_column("Variable Name", style="white")
        for finding in all_undeclared_findings:
            undeclared_table.add_row(
                finding["file_path"],
                str(finding["line_num"]),
                finding["variable_name"],
            )
        console.print(undeclared_table)
        console.print(
            "\n[bold]Suggestion:[/bold] Please add these variables to your 'env.schema.toml' to maintain your configuration contract."
        )

    # A skip means eligible content was never actually inspected -- this
    # scan cannot honestly be called "clean" outright, even when nothing
    # was found in what *was* scanned (see BL-004). Staged mode is exactly
    # what the installed pre-commit hook and CI gates invoke, so
    # incompleteness must be fatal there even with zero findings; a bare
    # interactive scan keeps its existing exit-0 UX, but never claims full
    # coverage in its printed message either.
    incomplete = bool(skipped_large_files)
    fatal_incomplete = incomplete and staged_only

    if not found_issues and not fatal_incomplete:
        if incomplete:
            console.print(
                "\n[bold yellow]⚠ Scan finished, but coverage was incomplete -- see the skipped file(s) above.[/bold yellow]"
            )
        else:
            console.print(
                "\n[bold green]✓ No issues found. Your configuration is secure and compliant![/bold green]"
            )
        return

    if staged_only:
        console.print(
            "\n[bold red]Commit aborted. Please fix the issues above before committing.[/bold red]"
        )

    raise typer.Exit(code=1)


def scan_result(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
    service_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Same scan as run_scan, but silences every Rich print/progress-bar (so
    stdout stays pure JSON) and returns a plain, JSON-serializable dict
    instead of rendering tables and raising typer.Exit -- for '--json'.
    """
    was_quiet = console.quiet
    console.quiet = True
    try:
        secrets, undeclared, skipped = _scan_files(
            paths, staged_only, config_path, exclude_patterns, service_name
        )
    finally:
        console.quiet = was_quiet

    return {
        "clean": not (secrets or undeclared),
        "secrets": secrets,
        "undeclared_variables": undeclared,
        "skipped_files": skipped,
        # True iff every eligible file was actually inspected -- False
        # when eligible content was skipped for a reason `scan` couldn't
        # verify around (today, only the >1MB case; see BL-004/BL-095).
        # Deliberately independent of `clean`: `clean` states whether
        # anything was *found* in what was scanned, `complete` states
        # whether the scan actually covered everything eligible -- a
        # caller that wants "should I trust this as fully clean" needs
        # both, since `clean=True` alone was exactly BL-004's false-clean
        # bug (skipped content never affected `clean`).
        "complete": not skipped,
    }


ENVSHIELD_HOOK_MARKER = "# Hook installed by EnvShield"


def remove_hooks() -> List[Dict[str, str]]:
    """
    Removes only a hook file EnvShield can prove is its own, unmodified
    output -- an exact content match against what EnvShield would generate
    right now, the same ownership standard install_pre_commit_hook/
    install_post_merge_hook already use (see their own comment: the marker
    comment alone is not proof of ownership -- a user can modify a
    generated hook, or hand-write one containing the same comment, while
    keeping the marker text intact). A hook that isn't provably
    EnvShield's own unmodified output is left alone -- whether it's
    genuinely foreign (Husky, a hand-written script) or an EnvShield hook
    a user has since extended.

    Returns one {"name": ..., "status": ...} entry per hook type EnvShield
    manages (pre-commit, post-merge), regardless of outcome -- "removed"
    (deleted, exact match), "preserved" (a file exists but its content
    doesn't exactly match current generated output -- hand-modified,
    foreign, or stale relative to the current config), or "missing" (no
    file at that path). This is the single source of truth for hook
    ownership; 'hook remove' filters for "removed", 'uninstall' also
    reports "preserved" -- neither re-derives the ownership check itself.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository.")

    hooks_dir = git_utils.get_hooks_dir()
    generators = {
        "pre-commit": _generate_pre_commit_hook_content,
        "post-merge": _generate_post_merge_hook_content,
    }
    results = []
    for hook_name, generate_content in generators.items():
        hook_path = os.path.join(hooks_dir, hook_name)
        if not os.path.exists(hook_path):
            results.append({"name": hook_name, "status": "missing"})
            continue
        with open(hook_path, "r") as f:
            content = f.read()
        if content != generate_content():
            results.append({"name": hook_name, "status": "preserved"})
            continue
        os.remove(hook_path)
        results.append({"name": hook_name, "status": "removed"})
    return results


def _describe_existing_hook(content: str, is_safely_regeneratable: bool) -> str:
    """
    Best-effort description of an existing hook file, for the overwrite
    warning. `is_safely_regeneratable` is an exact-content match against
    what EnvShield would generate right now -- the marker comment alone is
    not proof of that (see the call site's own comment): a user can modify
    a generated hook, or hand-write one, while keeping the marker text
    intact.
    """
    if is_safely_regeneratable:
        return "previously installed by EnvShield -- safe to regenerate"
    if ENVSHIELD_HOOK_MARKER in content:
        return (
            "previously installed by EnvShield, but its contents no longer match "
            "what EnvShield would generate now (hand-edited, or stale relative to "
            "your current config) -- overwriting could discard those changes"
        )
    if "husky.sh" in content or ".husky" in content:
        return "managed by Husky"
    non_comment_lines = [
        line
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return f"NOT installed by EnvShield -- overwriting will delete {len(non_comment_lines)} existing line(s) of hook logic"


def _warn_hooks_path_redirect(hooks_dir: str, git_root: str) -> None:
    default_dir = os.path.join(git_root, ".git", "hooks")
    if os.path.abspath(hooks_dir) != os.path.abspath(default_dir):
        console.print(
            f"[dim]ℹ️  core.hooksPath is set -- installing into {hooks_dir} instead of .git/hooks.[/dim]"
        )


def _generate_pre_commit_hook_content() -> str:
    """
    Generates the bash script content for the pre-commit hook: always scans
    staged files for secrets/undeclared vars, and -- when a staged change
    touches a service's schema -- also blocks the commit if that service's
    tracked template (.env.example) wasn't regenerated to match. Without
    this, a hand-edited env.schema.toml can go stale at the source, silent
    until whoever next pulls (and only then if they have the post-merge
    hook installed at all).
    """
    config = config_manager.load_config()
    services_config = config.get("services", {})

    schema_to_service = {
        service_config["schema"]: name
        for name, service_config in services_config.items()
        if isinstance(service_config, dict) and service_config.get("schema")
    }

    header = (
        "#!/bin/sh\n\n"
        "# Hook installed by EnvShield\n"
        "# Scans staged files for hardcoded secrets AND undeclared environment variables.\n"
        "envshield scan --staged\n"
        "STATUS=$?\n"
    )

    if not schema_to_service:
        return header + "exit $STATUS\n"

    # Each service's template sync is only checked when THAT service's own
    # schema was actually staged -- one 'if' per service, gated on its own
    # path with grep -F (a literal-string match, no alternation needed).
    # A single shared 'if' gating every service's check (the previous
    # shape) is the same class of bug the combined -E alternation was
    # meant to fix elsewhere: staging only 'web's schema still ran 'api's
    # check too, false-failing the commit over a service nobody touched.
    #
    # schema_path/name/example_file all come from envshield.yml -- a file
    # explicitly designed to be committed and PR-edited (see
    # config_manager.UnsafePathError's own docstring on this exact threat
    # model). Each is assigned to a shell variable via shlex.quote() -- a
    # single, self-contained safe shell word -- and every use after that
    # references it as "$VAR", never re-embeds the raw value as literal
    # script text. Double-quoted parameter expansion substitutes a
    # variable's value as opaque data with no further shell
    # interpretation of its content, regardless of what characters it
    # contains -- that's what actually closes the injection here, not
    # anything about the value's own contents being "safe-looking".
    blocks = []
    for schema_path, name in schema_to_service.items():
        lines = [
            f"_ENVSHIELD_SCHEMA_PATH={shlex.quote(schema_path)}",
            f"_ENVSHIELD_SERVICE_NAME={shlex.quote(name)}",
            'if git diff --cached --name-only | grep -qxF "$_ENVSHIELD_SCHEMA_PATH"; then',
        ]

        # 'schema sync --check' below only ever reads the template off
        # disk, not what's actually staged -- so running 'schema sync'
        # (which updates disk) and then forgetting to 'git add' the
        # result would pass that check while the commit itself still
        # lands with a stale template baked in. Real incident this
        # reproduces. Blocking on ANY unstaged template diff here closes
        # that gap without needing to duplicate schema/template parsing
        # against git's staged blob content. Python-module services have
        # no separate template file to check (see _check_example_file_sync).
        try:
            paths = config_manager.get_env_paths(service_name=name)
            if not paths["local_file"].endswith(".py"):
                example_file = paths["example_file"]
                lines.append(f"  _ENVSHIELD_EXAMPLE_FILE={shlex.quote(example_file)}")
                lines.append(
                    '  if git diff --name-only | grep -qxF "$_ENVSHIELD_EXAMPLE_FILE"; then\n'
                    "    echo \"✗ '$_ENVSHIELD_EXAMPLE_FILE' has unstaged changes -- did you "
                    "forget 'git add' after running 'envshield schema sync'?\"\n"
                    "    STATUS=1\n"
                    "  fi"
                )
        except EnvShieldException:
            pass

        lines.append(
            '  envshield schema sync --service "$_ENVSHIELD_SERVICE_NAME" --check || STATUS=1'
        )
        lines.append("fi")
        blocks.append("\n".join(lines))

    return (
        header
        + "\n# A staged schema change must also update its tracked template --\n"
        + "# otherwise .env.example goes stale the moment this commit lands.\n"
        + "\n".join(blocks)
        + "\n\n"
        + "exit $STATUS\n"
    )


def install_pre_commit_hook(force: bool = False, non_interactive: bool = False):
    """Installs the Git pre-commit hook."""
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository. Cannot install hook.")

    hooks_dir = git_utils.get_hooks_dir()
    _warn_hooks_path_redirect(hooks_dir, git_root)
    os.makedirs(hooks_dir, exist_ok=True)
    pre_commit_path = os.path.join(hooks_dir, "pre-commit")

    hook_script_content = _generate_pre_commit_hook_content()

    try:
        if os.path.exists(pre_commit_path):
            with open(pre_commit_path, "r") as f:
                existing_content = f.read()

            # A marker comment alone is not proof this file is EnvShield's own,
            # untouched output -- a user can modify a generated hook (or
            # hand-write one containing the same comment) while keeping the
            # marker text intact. Only an exact match against what EnvShield
            # would generate right now is provably safe to replace without
            # asking; non-interactive mode's fast path is scoped to exactly
            # that case -- anything else (including a marker-bearing file
            # that no longer matches) falls through to the same protected
            # confirmation/warning path as a genuinely foreign hook.
            is_safely_regeneratable = existing_content == hook_script_content

            if non_interactive and not is_safely_regeneratable:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A pre-commit hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield scan --staged' to your existing hook script."
                )
                return

            if not force and not non_interactive:
                overwrite = questionary.confirm(
                    f"A pre-commit hook already exists ({_describe_existing_hook(existing_content, is_safely_regeneratable)}). Do you want to overwrite it?",
                    default=False,
                ).ask()
                if not overwrite:
                    console.print("[yellow]Hook installation cancelled.[/yellow]")
                    raise typer.Exit()

        with open(pre_commit_path, "w") as f:
            f.write(hook_script_content)

        current_permissions = os.stat(pre_commit_path).st_mode
        os.chmod(
            pre_commit_path,
            current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
        )

        console.print(
            "[bold green]✓ Git pre-commit hook installed successfully![/bold green]"
        )

    except (IOError, OSError) as e:
        raise EnvShieldException(
            f"Failed to write or set permissions for the hook file: {e}"
        )
    except (TypeError, KeyboardInterrupt):
        console.print("[yellow]Hook installation cancelled by user.[/yellow]")
        raise typer.Exit()


def _generate_post_merge_hook_content() -> str:
    """
    Generates the bash script content for the post-merge hook. Smart: each
    service's 'doctor' check only runs when THAT service's own schema
    actually changed in the merge -- not every registered service
    whenever any one schema changed. Same class of false-positive-across-
    services bug the pre-commit hook had: merging a branch that only
    touched 'api's schema must not also run (and potentially report
    issues for) 'web', which nothing in this merge affected at all.
    """
    config = config_manager.load_config()
    services_config = config.get("services", {})

    schema_to_service = {
        service_config["schema"]: name
        for name, service_config in services_config.items()
        if isinstance(service_config, dict) and service_config.get("schema")
    }

    if not schema_to_service:
        # Hooks can be installed before any service is registered (e.g. a
        # standalone 'hook install' in a fresh repo) -- nothing to watch
        # for yet, so there's nothing this hook can usefully check.
        return (
            "#!/bin/sh\n\n"
            "# Hook installed by EnvShield\n"
            "# No services registered yet -- nothing to check.\n"
            "exit 0\n"
        )

    # One 'if' per service, gated on grep -F matching that service's own
    # schema path only (a literal-string match -- no alternation needed,
    # unlike the old shared gate this replaces).
    #
    # schema_path/name come from envshield.yml, untrusted for the same
    # reason noted in _generate_pre_commit_hook_content -- same fix here:
    # shlex.quote() into a shell variable, referenced afterward only as
    # "$VAR", never re-embedded as literal script text.
    checks = "\n".join(
        f"_ENVSHIELD_SCHEMA_PATH={shlex.quote(schema_path)}\n"
        f"_ENVSHIELD_SERVICE_NAME={shlex.quote(name)}\n"
        'if git diff --name-only HEAD@{1}..HEAD | grep -qxF "$_ENVSHIELD_SCHEMA_PATH" 2>/dev/null; then\n'
        '  envshield doctor --service "$_ENVSHIELD_SERVICE_NAME" 2>/dev/null\n'
        "fi"
        for schema_path, name in schema_to_service.items()
    )
    return (
        "#!/bin/sh\n\n"
        "# Hook installed by EnvShield\n"
        "# Smart: only runs for a service whose own schema actually changed in this merge.\n"
        "# If new required variables were added, it alerts the developer immediately.\n"
        "# Non-blocking: warns but doesn't fail the merge.\n\n"
        + checks
        + "\n\n"
        + "exit 0\n"
    )


def install_post_merge_hook(force: bool = False, non_interactive: bool = False):
    """
    Installs a Git post-merge hook that runs 'envshield doctor' after pulling changes.
    This ensures developers are alerted immediately if a pulled commit adds a new required
    environment variable, without waiting for a container restart.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository. Cannot install hook.")

    hooks_dir = git_utils.get_hooks_dir()
    _warn_hooks_path_redirect(hooks_dir, git_root)
    os.makedirs(hooks_dir, exist_ok=True)
    post_merge_path = os.path.join(hooks_dir, "post-merge")

    hook_script_content = _generate_post_merge_hook_content()

    try:
        if os.path.exists(post_merge_path):
            with open(post_merge_path, "r") as f:
                existing_content = f.read()

            # See install_pre_commit_hook's identical comment: a marker
            # comment alone is not proof this file is EnvShield's own,
            # untouched output. Only an exact match against what EnvShield
            # would generate right now is treated as provably safe.
            is_safely_regeneratable = existing_content == hook_script_content

            if non_interactive and not is_safely_regeneratable:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A post-merge hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield doctor' calls to your existing hook script."
                )
                return

            if not force and not non_interactive:
                overwrite = questionary.confirm(
                    f"A post-merge hook already exists ({_describe_existing_hook(existing_content, is_safely_regeneratable)}). Do you want to overwrite it?",
                    default=False,
                ).ask()
                if not overwrite:
                    console.print("[yellow]Hook installation cancelled.[/yellow]")
                    raise typer.Exit()

        with open(post_merge_path, "w") as f:
            f.write(hook_script_content)

        current_permissions = os.stat(post_merge_path).st_mode
        os.chmod(
            post_merge_path,
            current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
        )

        console.print(
            "[bold green]✓ Git post-merge hook installed successfully![/bold green]"
        )

    except (IOError, OSError) as e:
        raise EnvShieldException(
            f"Failed to write or set permissions for the hook file: {e}"
        )
    except (TypeError, KeyboardInterrupt):
        console.print("[yellow]Hook installation cancelled by user.[/yellow]")
        raise typer.Exit()
