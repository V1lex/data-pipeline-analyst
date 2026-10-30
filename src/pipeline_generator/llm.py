from typing import Any, Protocol

from .models import Completion


class LlmClient(Protocol):
    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> Completion: ...
