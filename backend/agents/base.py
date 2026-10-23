"""What every agent hands back: the shared AgentOutput, plus the data the next agent needs.

AgentOutput is the contract the audit log and the UI read. `data` carries the working details
(extracted fields, codes, the engine's assessment) that the orchestrator passes along.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from backend.schemas import AgentOutput

HUMAN = "human_review"  # an agent sets `next` to this when a person should look before the claim goes out


@dataclass(frozen=True)
class AgentResult:
    output: AgentOutput
    data: BaseModel

    @property
    def wants_human(self) -> bool:
        return self.output.next == HUMAN
