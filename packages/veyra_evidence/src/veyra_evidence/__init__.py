"""Evidence primitives: sealed segments, key providers, Merkle digests and the signed ledger.

The archiver writes segments with :mod:`veyra_evidence.segment`, the integrity service signs
window roots into the ledger, and the evidence API verifies against both.
"""

__version__ = "0.1.0"
