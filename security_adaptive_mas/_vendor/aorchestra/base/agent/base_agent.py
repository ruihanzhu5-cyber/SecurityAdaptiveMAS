from abc import abstractmethod
from typing import List, Optional, Any, Dict

from pydantic import Field, BaseModel

from security_adaptive_mas._vendor.aorchestra.base.agent.base_action import BaseAction
from typing import Callable, Awaitable
AsyncLLM = Callable[[str], Awaitable[str]]

class BaseAgent(BaseAction, BaseModel):
    
    # Core attributes
    name: str = Field(..., description="Unique name of the agent")
    description: Optional[str] = Field(None, description="Optional agent description")

    # Prompts
    system_prompt: Optional[str] = Field(
        None, description="System-level instruction prompt"
    )
    next_step_prompt: Optional[str] = Field(
        None, description="Prompt for determining next action"
    )

    # Dependencies
    # Make LLM optional; concrete agents may initialize it from config.
    llm: Optional[AsyncLLM] = Field(default=None, description="Language model instance")

    # Execution control
    max_steps: int = Field(default=10, description="Maximum steps before termination")
    current_step: int = Field(default=0, description="Current step in execution")

    # Agent-As-An-Action
    parameters: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True
        
    def model_post_init(self, __context):
        if not callable(self.llm):
            raise ValueError('worker_requires_injected_callable')

    @abstractmethod
    async def step(self):
        """Execute a single step in the agent's workflow.

        Must be implemented by subclasses to define specific behavior.
        """

    @abstractmethod
    async def run(self, request: Optional[str] = None) -> str:
        """Execute the agent's main loop asynchronously.
        
        Args:
            request: Optional initial user request to process.
        """

    async def __call__(self, **kwargs) -> Any:
        """Execute the agent with given parameters."""
        return await self.run(**kwargs)
    
    def to_param(self) -> Dict[str, Any]:
        return {
            "type": "agent-as-function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }