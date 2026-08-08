"""The no-model guarantee, checked at runtime — the third of three
independent checks (static import scan in CI, IAM deny at deploy time, this
runtime assertion at process start). Each catches what the others cannot: a
dynamic `importlib.import_module("anthropic")` defeats a static grep but not
a check of `sys.modules` after import time.
"""

from __future__ import annotations

import sys

import pytest
from mnemos_warden.guarantees import NoModelGuaranteeViolated, assert_no_model_loaded

pytestmark = pytest.mark.invariant


def test_passes_in_the_normal_test_environment() -> None:
    """The suite itself must not have accidentally imported an LLM SDK —
    otherwise this whole test file is checking nothing."""
    assert_no_model_loaded()


def test_detects_a_statically_imported_forbidden_module() -> None:
    sys.modules["anthropic"] = sys.modules[__name__]  # any real module object will do
    try:
        with pytest.raises(NoModelGuaranteeViolated, match="anthropic"):
            assert_no_model_loaded()
    finally:
        del sys.modules["anthropic"]


def test_detects_a_dynamically_imported_forbidden_module() -> None:
    """The case a static source-code grep (make no-model-in-warden) cannot
    catch: `importlib.import_module("openai")` never appears as an `import`
    statement anywhere in the diff, but it still lands in sys.modules."""
    import importlib

    sys.modules["openai"] = importlib.import_module(__name__)
    try:
        with pytest.raises(NoModelGuaranteeViolated, match="openai"):
            assert_no_model_loaded()
    finally:
        del sys.modules["openai"]


def test_detects_a_forbidden_submodule() -> None:
    """anthropic.types, openai.resources.chat — a submodule import must be
    caught too, not just the top-level package name."""
    sys.modules["anthropic.types"] = sys.modules[__name__]
    try:
        with pytest.raises(NoModelGuaranteeViolated, match=r"anthropic\.types"):
            assert_no_model_loaded()
    finally:
        del sys.modules["anthropic.types"]


def test_does_not_false_positive_on_a_coincidentally_prefixed_name() -> None:
    """`anthropicsomethingelse` is not `anthropic` — the prefix check must
    require a dot boundary or exact match, not a bare startswith."""
    sys.modules["anthropicsomethingelse"] = sys.modules[__name__]
    try:
        assert_no_model_loaded()  # must NOT raise
    finally:
        del sys.modules["anthropicsomethingelse"]


def test_bedrock_probe_is_not_yet_wired() -> None:
    """Documents the current state rather than hiding it: the IAM-simulation
    probe is explicitly deferred to Phase 06.4, when a real deployed role
    exists to probe against. A silently-passing stub here would be worse than
    an explicit NotImplementedError — it would look like coverage that does
    not exist yet."""
    from mnemos_warden.guarantees import assert_bedrock_denied

    with pytest.raises(NotImplementedError):
        assert_bedrock_denied(object())
