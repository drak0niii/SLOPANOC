"""Phase 5.1E: cross-cutting separation-of-concerns checks -- no
domain/document-type-specific governance implementation, and
applicability evaluation stays entirely outside lifecycle/version
governance (instruction sections 3, 16, 42).
"""
from __future__ import annotations

import ast
from pathlib import Path

_GOVERNANCE_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "governance"


def test_no_document_type_specific_governance_classes_exist() -> None:
    """No MopGovernanceService/RcaGovernanceService/SopGovernanceService
    or equivalent -- one authoritative governance implementation for
    every document type. Checked via real AST class declarations, not
    raw text, so this package's own docstrings are never a false
    positive.
    """
    forbidden_fragments = ("MOP", "RCA", "SOP", "KB")
    violations: list[tuple[str, str]] = []
    for path in sorted(_GOVERNANCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                upper = node.name.upper()
                for fragment in forbidden_fragments:
                    if fragment in upper:
                        violations.append((path.name, node.name))
    assert violations == [], f"document-type-specific governance class found: {violations}"


def test_no_hardcoded_business_vocabulary_constants() -> None:
    """No vendor/technology/customer/product/domain/source-system
    vocabulary constant anywhere in governance/ -- the same discipline
    already enforced for applicability (5.1B) and ingestion (5.1C).
    """
    forbidden_substrings = ("SUPPORTED_SOURCE", "SUPPORTED_VENDOR", "SUPPORTED_TECHNOLOGY", "KNOWN_DIMENSION", "KNOWN_VENDOR")
    violations: list[tuple[str, str]] = []
    for path in sorted(_GOVERNANCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                declared_names.append(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                declared_names.extend(t.id for t in targets if isinstance(t, ast.Name))
        for name in declared_names:
            upper = name.upper()
            for forbidden in forbidden_substrings:
                if forbidden in upper:
                    violations.append((path.name, name))
    assert violations == [], f"hardcoded business vocabulary found: {violations}"


def test_governance_never_imports_or_calls_evaluate_applicability() -> None:
    """CURRENT and APPLICABLE remain distinct: a version may be
    APPROVED + CURRENT but not applicable to a given operational
    situation, and vice versa. Governance must never call 5.1B's
    evaluator to make a lifecycle/version decision.
    """
    violations: list[tuple[str, str]] = []
    for path in sorted(_GOVERNANCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
                if name == "evaluate_applicability":
                    violations.append((path.name, "evaluate_applicability call"))
            if isinstance(node, ast.ImportFrom) and node.module and "applicability" in node.module:
                for alias in node.names:
                    if alias.name == "evaluate_applicability":
                        violations.append((path.name, "evaluate_applicability import"))
    assert violations == [], f"governance must never invoke applicability evaluation: {violations}"


def test_no_version_label_comparison_operators_used_for_ordering() -> None:
    """Static proof, complementing the runtime opaque-label tests in
    test_governance_versioning.py: no `<`/`>`/`<=`/`>=` comparison is
    ever applied to a `.label` attribute anywhere in governance/ --
    version labels are identifiers, never ordering information.
    """
    ordering_ops = (ast.Lt, ast.Gt, ast.LtE, ast.GtE)
    violations: list[str] = []
    for path in sorted(_GOVERNANCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            label_operand = any(isinstance(operand, ast.Attribute) and operand.attr == "label" for operand in operands)
            if label_operand and any(isinstance(op, ordering_ops) for op in node.ops):
                violations.append(path.name)
    assert violations == [], f"an ordering comparison was applied to a version label: {violations}"
