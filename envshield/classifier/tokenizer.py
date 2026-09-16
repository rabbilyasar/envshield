"""
Lightweight line tokenizer for syntactic context extraction.

This module provides minimal tokenization sufficient to distinguish
syntactic contexts relevant to secret scanner FP reduction:
- Function keyword arguments vs assignments
- Type annotations vs values
- Destructuring vs assignments

Design constraints:
- Line-local only (no cross-line context)
- Regex-based (not a full parser)
- Tolerant of malformed input
- Fast (<1ms per line)
- Language-agnostic where practical (Python/JS/TS)
"""

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional, Tuple


class TokenType(Enum):
    """Token categories relevant for context classification."""

    IDENTIFIER = auto()  # variable names, function names
    STRING_LITERAL = auto()  # "..." or '...'
    NUMBER = auto()  # 123, 0x1a, 1.5
    OPERATOR = auto()  # =, :, ==, !=, etc.
    PUNCTUATION = auto()  # ( ) { } [ ] , ; .
    COMMENT = auto()  # # ... or // ...
    WHITESPACE = auto()  # spaces, tabs
    UNKNOWN = auto()  # anything else


@dataclass
class Token:
    """A single lexical token with position information."""

    text: str  # Raw token text
    type: TokenType
    start: int  # Character offset in line (0-indexed)
    end: int  # Character offset in line (exclusive)

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class SyntacticContext:
    """Syntactic context hints for a matched span."""

    # What's the matched span?
    match_start: int
    match_end: int

    # Is the matched value a string literal?
    is_string_literal: bool

    # What precedes the match?
    preceding_operator: Optional[str]  # '=', ':', ',', etc.

    # What follows the match?
    following_char: Optional[str]  # ',', ')', ';', etc.

    # Context hints
    inside_function_call: bool  # paren depth > 0
    inside_braces: bool  # brace depth > 0
    is_type_annotation: bool  # preceded by ':' not '='


# Token patterns (ordered by priority)
# Language-agnostic patterns that work for Python, JS, TS
TOKEN_PATTERNS = [
    # Comments (must come early to avoid matching inside comments)
    (TokenType.COMMENT, r'#[^\n]*'),  # Python
    (TokenType.COMMENT, r'//[^\n]*'),  # JS/TS line comment
    (TokenType.COMMENT, r'/\*.*?\*/'),  # JS/TS block comment (single-line only)
    # String literals
    (TokenType.STRING_LITERAL, r'"""[^"]*(?:"")?(?:")?'),  # Python docstring start
    (TokenType.STRING_LITERAL, r"'''[^']*(?:'')?(?:')?"),  # Python docstring start
    (TokenType.STRING_LITERAL, r'"(?:[^"\\]|\\.)*"'),  # Double-quoted string
    (TokenType.STRING_LITERAL, r"'(?:[^'\\]|\\.)*'"),  # Single-quoted string
    (TokenType.STRING_LITERAL, r'`(?:[^`\\]|\\.)*`'),  # Template literal (JS/TS)
    # Numbers
    (TokenType.NUMBER, r'0x[0-9a-fA-F]+'),  # Hex
    (TokenType.NUMBER, r'\d+\.\d+'),  # Float
    (TokenType.NUMBER, r'\d+'),  # Integer
    # Operators (multi-char first)
    (TokenType.OPERATOR, r'===|!==|==|!=|<=|>=|&&|\|\||<<|>>|\+=|-=|\*=|/=|%='),
    (TokenType.OPERATOR, r'[+\-*/%=<>!&|^~]'),
    # Punctuation
    (TokenType.PUNCTUATION, r'[(){}\[\],;:.]'),
    # Identifiers (must come after keywords if we add them)
    (TokenType.IDENTIFIER, r'[a-zA-Z_][a-zA-Z0-9_]*'),
    # Whitespace
    (TokenType.WHITESPACE, r'\s+'),
]

# Compile patterns once
_COMPILED_PATTERNS = [(ttype, re.compile(pattern)) for ttype, pattern in TOKEN_PATTERNS]


def tokenize_line(line: str) -> List[Token]:
    """
    Tokenize a single line into tokens.

    Args:
        line: Source code line (may be malformed/incomplete)

    Returns:
        List of tokens in order of appearance

    Note:
        - Tolerant of malformed input (skips unrecognized chars)
        - Does not validate syntactic correctness
        - Single-line only (no multi-line string handling)
    """
    tokens = []
    pos = 0
    length = len(line)

    while pos < length:
        # Try each pattern
        matched = False
        for token_type, pattern in _COMPILED_PATTERNS:
            match = pattern.match(line, pos)
            if match:
                text = match.group(0)
                tokens.append(
                    Token(
                        text=text,
                        type=token_type,
                        start=pos,
                        end=pos + len(text),
                    )
                )
                pos += len(text)
                matched = True
                break

        if not matched:
            # Skip unrecognized character
            tokens.append(
                Token(
                    text=line[pos],
                    type=TokenType.UNKNOWN,
                    start=pos,
                    end=pos + 1,
                )
            )
            pos += 1

    return tokens


def extract_context(
    line: str, match_start: int, match_end: int, tokens: Optional[List[Token]] = None
) -> SyntacticContext:
    """
    Extract syntactic context for a regex match span.

    Args:
        line: Full source line
        match_start: Start offset of regex match
        match_end: End offset of regex match
        tokens: Pre-computed tokens (optional, will tokenize if not provided)

    Returns:
        SyntacticContext with classification hints
    """
    if tokens is None:
        tokens = tokenize_line(line)

    # Filter out whitespace/comments for analysis
    significant_tokens = [
        t for t in tokens if t.type not in (TokenType.WHITESPACE, TokenType.COMMENT)
    ]

    # Find tokens overlapping the match
    match_tokens = [t for t in tokens if t.start < match_end and t.end > match_start]

    # Is the match a string literal?
    is_string_literal = any(t.type == TokenType.STRING_LITERAL for t in match_tokens)

    # Find preceding significant token
    preceding_tokens = [t for t in significant_tokens if t.end <= match_start]
    preceding_operator = None
    if preceding_tokens:
        last_token = preceding_tokens[-1]
        if last_token.type == TokenType.OPERATOR or last_token.text in (':',',','='):
            preceding_operator = last_token.text

    # Find following character
    following_char = None
    following_tokens = [t for t in significant_tokens if t.start >= match_end]
    if following_tokens:
        following_char = following_tokens[0].text

    # Calculate paren/brace depth at match position
    paren_depth = 0
    brace_depth = 0
    for token in significant_tokens:
        if token.start >= match_start:
            break
        if token.text == '(':
            paren_depth += 1
        elif token.text == ')':
            paren_depth = max(0, paren_depth - 1)
        elif token.text == '{':
            brace_depth += 1
        elif token.text == '}':
            brace_depth = max(0, brace_depth - 1)

    # Type annotation heuristic: preceded by ':' not '='
    is_type_annotation = (
        preceding_operator == ':'
        and not any(t.text == '=' for t in preceding_tokens[-3:] if t in preceding_tokens)
    )

    return SyntacticContext(
        match_start=match_start,
        match_end=match_end,
        is_string_literal=is_string_literal,
        preceding_operator=preceding_operator,
        following_char=following_char,
        inside_function_call=(paren_depth > 0),
        inside_braces=(brace_depth > 0),
        is_type_annotation=is_type_annotation,
    )


def classify_context(context: SyntacticContext) -> Tuple[str, str]:
    """
    Classify a syntactic context into FP-likelihood categories.

    Args:
        context: Extracted syntactic context

    Returns:
        (classification, reason) tuple:
            classification: 'likely_fp', 'likely_tp', 'uncertain'
            reason: Human-readable explanation

    Note:
        This is a heuristic classifier for the known FP classes.
        It does NOT replace the scanner's detector logic.
    """
    # String literals are potential TPs (or at least not the FP classes we're targeting)
    if context.is_string_literal:
        return 'likely_tp', 'string literal value'

    # Type annotation (TypeScript/Python)
    if context.is_type_annotation:
        return 'likely_fp', 'type annotation (: Type syntax)'

    # Function keyword argument (inside known function call)
    if context.inside_function_call and context.following_char in (',', ')'):
        if context.preceding_operator in ('=',):
            return 'likely_fp', 'function keyword argument'

    # Keyword argument pattern WITHOUT seeing opening paren:
    # identifier=identifier, or identifier=identifier)
    # Catches multi-line function calls where opening paren is on previous line
    if (
        context.preceding_operator == '='
        and not context.is_string_literal
        and context.following_char in (',', ')')
    ):
        return 'likely_fp', 'function keyword argument'

    # Destructuring (inside braces on LHS)
    if context.inside_braces and context.following_char in (',', '}'):
        return 'likely_fp', 'destructuring assignment'

    # Assignment with identifier RHS (not a literal)
    if context.preceding_operator == '=' and not context.is_string_literal:
        return 'likely_fp', 'identifier reference (not literal)'

    return 'uncertain', 'no clear FP pattern'


# Convenience function for the common case
def analyze_match(line: str, match_start: int, match_end: int) -> Tuple[str, str]:
    """
    One-shot analysis: tokenize + extract context + classify.

    Args:
        line: Source line
        match_start: Regex match start offset
        match_end: Regex match end offset

    Returns:
        (classification, reason) tuple from classify_context
    """
    tokens = tokenize_line(line)
    context = extract_context(line, match_start, match_end, tokens)
    return classify_context(context)
