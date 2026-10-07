"""Loading the wake word model says nothing it does not mean.

openWakeWord asks onnxruntime for CUDA unconditionally, so every voice start on a processor-only
build logged "Specified provider 'CUDAExecutionProvider' is not in available provider names" —
a warning about nothing, read as the voice model being broken.
"""
from __future__ import annotations

import warnings

import pytest

pytest.importorskip("openwakeword")

from jarvis.audio.local_wake import LocalWakeWord  # noqa: E402


def test_loading_the_wake_model_does_not_warn_about_cuda():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        LocalWakeWord()
    assert not [w for w in caught if "CUDAExecutionProvider" in str(w.message)]


def test_other_warnings_are_still_heard():
    """Only that one message is dropped, and only while the model loads."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        LocalWakeWord()
        warnings.warn("something else", UserWarning)
    assert [w for w in caught if "something else" in str(w.message)]
