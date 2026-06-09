"""WorkflowExecutionRequest carries an explicit solution_id install scope."""
from src.models.contracts.executions import WorkflowExecutionRequest


def test_solution_id_defaults_to_none():
    req = WorkflowExecutionRequest(workflow_id="workflows/foo.py::main")
    assert req.solution_id is None


def test_solution_id_accepts_value():
    sid = "11111111-1111-1111-1111-111111111111"
    req = WorkflowExecutionRequest(workflow_id="workflows/foo.py::main", solution_id=sid)
    assert req.solution_id == sid
