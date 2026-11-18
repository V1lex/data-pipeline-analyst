from __future__ import annotations

from typing import Any

"""Raw formal-model transport.

This module intentionally performs no validation or conversion. formal_check
owns validation and compilation of this JSON.
"""


type PipelineFormalModel = dict[str, Any]
