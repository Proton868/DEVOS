import inspect
from brain.llm import BrainLLM
from core.loop import BrainExecutionLoop
from core.task_contract import TaskRequest, TaskResult

def test_brain_llm_has_user_credential_loader():
    assert "load_user_provider_key" in inspect.getsource(BrainLLM.for_user)

def test_worker_loop_uses_user_credential_path():
    source = inspect.getsource(BrainExecutionLoop.run)
    assert "BrainLLM.for_user" in source
    assert 'purpose="worker"' in source

def test_task_contract_has_no_credential_fields():
    forbidden = {"api_key", "apikey", "credential", "credentials", "secret", "token"}
    assert not (set(TaskRequest.__dataclass_fields__) & forbidden)
    assert not (set(TaskResult.__dataclass_fields__) & forbidden)
