# envshield/utils/paths.py
# Low-level, dependency-free path-safety primitives shared across layers
# that otherwise have no reason to depend on each other.
import os


def is_within(candidate: str, root: str) -> bool:
    """
    Whether `candidate` resolves inside `root`, following symlinks on both
    sides via os.path.realpath -- a purely lexical comparison (abspath/
    normpath without realpath) would be satisfied by a symlink whose real
    target escapes `root` while its own path lexically sits inside it,
    which is exactly the P0-6-class vulnerability every caller of this
    function exists to close.

    Extracted 2026-09-03 from two independent, previously-duplicated
    implementations -- config/manager.py's _ensure_within_project
    (envshield.yml-sourced paths: schema/local_file/example_file/
    additional_source_roots/extends/deployment-manifest) and parsers/
    _deployment.py's ensure_within_project (a deployment manifest's own
    'env_file:' reference, BL-008) -- once a review confirmed the two
    callers' *actual join/return semantics genuinely differ* (one
    resolves relative to the project root and returns the original,
    portable path for persistence into envshield.yml; the other resolves
    relative to the manifest's own directory and returns the resolved
    absolute path for immediate use) while the *security-critical
    comparison itself* was byte-for-byte identical -- exactly the kind of
    duplication BL-010's own shared-cycle-detection fix (the same review
    pass) already argued against for a sibling case ("a future fix can't
    patch only one"). This module has zero imports of its own, matching
    git_utils.py's own already-established role as a leaf utility both
    config/manager.py and core/dependency_snapshot.py already depend on
    without creating any cross-layer coupling -- config/manager.py and
    parsers/ each depend downward on this module; neither depends on the
    other.

    Each caller keeps its own join logic, its own return contract, and
    its own UnsafePathError call (with its own caller-appropriate label
    and displayed path) -- only the boolean containment check itself is
    shared.
    """
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    try:
        return os.path.commonpath([real_root, real_candidate]) == real_root
    except ValueError:
        # Raised on Windows when the two paths are on different drives --
        # definitionally not "within" the project.
        return False
