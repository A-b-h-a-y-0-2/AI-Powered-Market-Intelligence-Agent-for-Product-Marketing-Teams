"""Base agent interface.

Every agent has:
- A named input type (Pydantic model)
- A named output type (Pydantic model)
- A named error type
- A documented description of what triggers it and what it guarantees

Agents receive dependencies as constructor arguments — never instantiate them internally.
Agents communicate through shared state, not by calling each other's functions directly.
Terminal states are explicit: COMPLETED, FAILED, PARTIAL_SUCCESS, QUARANTINED.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Abstract base for all agents in the system.

    Subclasses must implement run() and declare their input/output types.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine-readable agent name used in logs and traces."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """What this agent does, what triggers it, and what it guarantees."""
        ...

    @abstractmethod
    async def run(self, input_data: InputT) -> OutputT:
        """Execute the agent's primary responsibility.

        Must:
        - Log every significant step with structured JSON
        - Trace every LLM call
        - Raise AgentError subclasses with named codes on failure
        - Never return None on failure — raise or return a terminal state
        """
        ...

    async def health_check(self) -> dict[str, Any]:
        """Report agent health. Subclasses should override to check dependencies."""
        return {"agent": self.name, "status": "ok"}
