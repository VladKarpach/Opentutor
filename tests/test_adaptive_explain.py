"""Regression tests for the explanation endpoint (FR-7.1 / FR-7.2).

Two bugs lived here and both failed only at runtime, never at import time:

1. ``GeneratedAsset`` exposes its JSON column as the attribute ``metadata_``
   (``metadata`` is reserved by SQLAlchemy's declarative base). Passing
   ``metadata_json=`` raised TypeError, so every explanation request 500'd.
2. A local named ``content`` shadowed the imported ``content`` service module,
   which the handler calls right after — so the regenerate branch broke the
   module reference.

These assert the handler uses the real attribute names and does not shadow the
module, by reading the source rather than by calling the model.
"""

import ast
import inspect
from pathlib import Path

import pytest

from models.generated_asset import GeneratedAsset

ROUTER = Path(__file__).resolve().parents[1] / "apps" / "api" / "routers" / "adaptive_learning.py"


def _handler_source() -> str:
    source = ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "explain_topic":
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("explain_topic not found")


class TestGeneratedAssetFieldNames:
    """The attribute is metadata_, never metadata_json."""

    def test_model_exposes_metadata_underscore(self):
        assert hasattr(GeneratedAsset, "metadata_")

    def test_model_does_not_expose_metadata_json(self):
        assert not hasattr(GeneratedAsset, "metadata_json")

    def test_column_is_named_metadata(self):
        assert GeneratedAsset.__table__.c["metadata"] is not None


class TestExplainHandlerFieldNames:
    def test_handler_does_not_pass_metadata_json(self):
        """Checked on the AST, not the text: a comment naming the bug once would
        otherwise make this test fail on its own documentation."""
        source = _handler_source()
        tree = ast.parse(source)
        handler = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "explain_topic"
        )
        keywords = [
            kw.arg
            for n in ast.walk(handler)
            if isinstance(n, ast.Call)
            for kw in n.keywords
            if kw.arg
        ]
        assert "metadata_json" not in keywords

    def test_handler_uses_metadata_underscore(self):
        source = _handler_source()
        tree = ast.parse(source)
        handler = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "explain_topic"
        )
        keywords = [
            kw.arg
            for n in ast.walk(handler)
            if isinstance(n, ast.Call)
            for kw in n.keywords
            if kw.arg
        ]
        assert "metadata_" in keywords

    def test_handler_stores_text_inside_the_content_object(self):
        """content is a JSON column, so a bare string would be the wrong shape."""
        assert 'content={"text": text}' in _handler_source()


class TestExplainHandlerShadowing:
    """A local named `content` breaks the service module call below it."""

    def test_no_local_named_content_is_assigned(self):
        tree = ast.parse(_handler_source())
        handler = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "explain_topic"
        )
        assigned = [
            t.id
            for n in ast.walk(handler)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
        ]
        assert "content" not in assigned

    def test_handler_still_calls_the_service_module(self):
        """``content.explain_topic(...)`` must survive: the module is not shadowed."""
        source = _handler_source()
        tree = ast.parse(source)
        handler = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "explain_topic"
        )
        module_calls = [
            n.func.value.id
            for n in ast.walk(handler)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
        ]
        assert "content" in module_calls
        assert any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "explain_topic"
            for n in ast.walk(handler)
        )


@pytest.mark.parametrize("name", ["explain_topic"])
def test_handler_is_importable(name):
    import routers.adaptive_learning as router

    assert hasattr(router, name)
    assert inspect.iscoroutinefunction(getattr(router, name))