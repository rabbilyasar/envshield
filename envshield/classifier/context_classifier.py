"""
Context Classifier for EnvShield Secret Scanner

Three-level classification model optimized for Git-hook blocking behavior:
- LIKELY_SECRET: High confidence secret value (e.g., string literal)
- LIKELY_CODE: High confidence code pattern (e.g., type annotation, function arg)
- AMBIGUOUS: Uncertain context (preserve uncertainty for policy layer)

Key principle: Reason about syntactic role, not identifier names.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple

# Import tokenizer from same module
from .tokenizer import extract_context


class Classification(Enum):
    """Three-level classification for secret candidates."""

    LIKELY_SECRET = "likely_secret"  # High confidence: looks like a secret value
    LIKELY_CODE = "likely_code"  # High confidence: syntactic code pattern
    AMBIGUOUS = "ambiguous"  # Uncertain: could be either


@dataclass
class ClassificationResult:
    """
    Result of classifying a secret candidate.

    Attributes:
        classification: The classification level (LIKELY_SECRET, LIKELY_CODE, AMBIGUOUS)
        confidence: Confidence level ("HIGH", "MEDIUM", "LOW")
        reason: Human-readable explanation of the classification
        rule_name: Name of the rule that matched (for debugging/testing)
        context_summary: Key context fields (never includes secret values)
    """

    classification: Classification
    confidence: str
    reason: str
    rule_name: str
    context_summary: Dict[str, Any]

    def __repr__(self) -> str:
        return (
            f"ClassificationResult("
            f"classification={self.classification.value}, "
            f"confidence={self.confidence}, "
            f"reason='{self.reason}', "
            f"rule_name={self.rule_name})"
        )


class ContextClassifier:
    """
    Classifies regex match candidates based on syntactic context.

    Three-level classification model:
    - LIKELY_SECRET: High confidence secret value (e.g., string literal)
    - LIKELY_CODE: High confidence code pattern (e.g., type annotation)
    - AMBIGUOUS: Uncertain context

    Designed for Git-hook blocking: preserves uncertainty for policy layer.

    Example:
        classifier = ContextClassifier()

        # Function keyword argument (identifier) → LIKELY_CODE
        result = classifier.classify("func(key=CONSTANT,)", 9, 17)
        assert result.classification == Classification.LIKELY_CODE

        # Function argument with string literal → LIKELY_SECRET
        result = classifier.classify("func(key='secret')", 9, 17)
        assert result.classification == Classification.LIKELY_SECRET

        # Type annotation → LIKELY_CODE
        result = classifier.classify("def foo(token: str):", 15, 18)
        assert result.classification == Classification.LIKELY_CODE
    """

    def classify(
        self, line: str, match_start: int, match_end: int
    ) -> ClassificationResult:
        """
        Classify a regex match candidate based on syntactic context.

        Args:
            line: Source line containing the match
            match_start: Character offset of match start (0-indexed)
            match_end: Character offset of match end (exclusive)

        Returns:
            ClassificationResult with classification, confidence, reason, rule name

        Classification rules (highest priority first):
            1. String literal → LIKELY_SECRET (HIGH confidence)
            2. Type annotation → LIKELY_CODE (HIGH confidence)
            3. Function keyword argument (identifier) → LIKELY_CODE (HIGH confidence)
            4. Multi-line keyword argument heuristic → LIKELY_CODE (MEDIUM-HIGH confidence)
            5. Destructuring → LIKELY_CODE (HIGH confidence)
            6. Identifier reference → LIKELY_CODE (MEDIUM confidence)
            7. Default → AMBIGUOUS
        """
        # Extract syntactic context from tokenizer
        context = extract_context(line, match_start, match_end)

        # Rule 1: String literal → LIKELY_SECRET
        if context.is_string_literal:
            return ClassificationResult(
                classification=Classification.LIKELY_SECRET,
                confidence="HIGH",
                reason="string literal value",
                rule_name="string_literal",
                context_summary={"is_string_literal": True},
            )

        # Rule 2: Type annotation → LIKELY_CODE
        if context.is_type_annotation:
            return ClassificationResult(
                classification=Classification.LIKELY_CODE,
                confidence="HIGH",
                reason="type annotation (: Type syntax)",
                rule_name="type_annotation",
                context_summary={
                    "is_type_annotation": True,
                    "preceding_operator": context.preceding_operator,
                },
            )

        # Rule 3: Function keyword argument (identifier) → LIKELY_CODE
        # Example: func(key=CONSTANT,) or func(key=CONSTANT)
        if (
            context.inside_function_call
            and not context.is_string_literal
            and context.preceding_operator == "="
            and context.following_char in (",", ")")
        ):
            return ClassificationResult(
                classification=Classification.LIKELY_CODE,
                confidence="HIGH",
                reason="function keyword argument (identifier, not literal)",
                rule_name="function_keyword_argument",
                context_summary={
                    "inside_function_call": True,
                    "preceding_operator": "=",
                    "following_char": context.following_char,
                    "is_string_literal": False,
                },
            )

        # Rule 4: Multi-line keyword argument heuristic → LIKELY_CODE
        # Catches cases like:
        #     key=SESSION_COOKIE_NAME,
        # where opening paren is on previous line
        if (
            not context.is_string_literal
            and context.preceding_operator == "="
            and context.following_char in (",", ")")
        ):
            return ClassificationResult(
                classification=Classification.LIKELY_CODE,
                confidence="MEDIUM-HIGH",
                reason="keyword argument pattern (multi-line heuristic)",
                rule_name="multiline_keyword_argument",
                context_summary={
                    "preceding_operator": "=",
                    "following_char": context.following_char,
                    "is_string_literal": False,
                },
            )

        # Rule 5: Destructuring → LIKELY_CODE
        # Example: const {key, token} = config
        if context.inside_braces and not context.is_string_literal:
            return ClassificationResult(
                classification=Classification.LIKELY_CODE,
                confidence="HIGH",
                reason="destructuring assignment",
                rule_name="destructuring",
                context_summary={"inside_braces": True, "is_string_literal": False},
            )

        # Rule 6: REMOVED - Identifier reference overlaps with BL-129
        # Original intent: api_key = DEFAULT_KEY → LIKELY_CODE
        # Problem: This does semantic analysis (is DEFAULT_KEY defined locally?)
        # which requires cross-reference tracking the tokenizer doesn't have.
        # BL-129 already handles this correctly for cases where the identifier
        # has a local definition. For external references (no local definition),
        # they SHOULD remain findings - the classifier was suppressing them
        # incorrectly. Removing this rule to let BL-129 handle all identifier
        # reference cases with proper semantic context.
        #
        # if (
        #     not context.is_string_literal
        #     and context.preceding_operator == '='
        #     and context.following_char not in (',', ')')
        # ):
        #     return ClassificationResult(
        #         classification=Classification.LIKELY_CODE,
        #         confidence="MEDIUM",
        #         reason="identifier reference (not literal)",
        #         rule_name="identifier_reference",
        #         context_summary={
        #             "preceding_operator": "=",
        #             "is_string_literal": False
        #         }
        #     )

        # Rule 7: Default → AMBIGUOUS
        # Insufficient evidence for high-confidence classification
        return ClassificationResult(
            classification=Classification.AMBIGUOUS,
            confidence="LOW",
            reason="insufficient syntactic context for confident classification",
            rule_name="default",
            context_summary={
                "is_string_literal": context.is_string_literal,
                "preceding_operator": context.preceding_operator,
                "following_char": context.following_char,
                "inside_function_call": context.inside_function_call,
                "inside_braces": context.inside_braces,
                "is_type_annotation": context.is_type_annotation,
            },
        )

    def is_likely_secret(self, line: str, match_start: int, match_end: int) -> bool:
        """
        Convenience method: returns True if classification is LIKELY_SECRET.

        Args:
            line: Source line containing the match
            match_start: Character offset of match start (0-indexed)
            match_end: Character offset of match end (exclusive)

        Returns:
            True if classification is LIKELY_SECRET, False otherwise
        """
        return (
            self.classify(line, match_start, match_end).classification
            == Classification.LIKELY_SECRET
        )

    def is_likely_code(self, line: str, match_start: int, match_end: int) -> bool:
        """
        Convenience method: returns True if classification is LIKELY_CODE.

        Args:
            line: Source line containing the match
            match_start: Character offset of match start (0-indexed)
            match_end: Character offset of match end (exclusive)

        Returns:
            True if classification is LIKELY_CODE, False otherwise
        """
        return (
            self.classify(line, match_start, match_end).classification
            == Classification.LIKELY_CODE
        )

    def is_ambiguous(self, line: str, match_start: int, match_end: int) -> bool:
        """
        Convenience method: returns True if classification is AMBIGUOUS.

        Args:
            line: Source line containing the match
            match_start: Character offset of match start (0-indexed)
            match_end: Character offset of match end (exclusive)

        Returns:
            True if classification is AMBIGUOUS, False otherwise
        """
        return (
            self.classify(line, match_start, match_end).classification
            == Classification.AMBIGUOUS
        )

    def classify_batch(
        self, candidates: list[Tuple[str, int, int]]
    ) -> list[ClassificationResult]:
        """
        Classify multiple candidates in batch.

        Args:
            candidates: List of (line, match_start, match_end) tuples

        Returns:
            List of ClassificationResult objects in same order as input
        """
        return [self.classify(line, start, end) for line, start, end in candidates]


# Convenience function for one-shot classification
def classify_match(line: str, match_start: int, match_end: int) -> ClassificationResult:
    """
    One-shot convenience function: classify a single match.

    Args:
        line: Source line containing the match
        match_start: Character offset of match start (0-indexed)
        match_end: Character offset of match end (exclusive)

    Returns:
        ClassificationResult

    Example:
        result = classify_match("api_key = 'sk_live_abc123'", 10, 27)
        assert result.classification == Classification.LIKELY_SECRET
    """
    classifier = ContextClassifier()
    return classifier.classify(line, match_start, match_end)
