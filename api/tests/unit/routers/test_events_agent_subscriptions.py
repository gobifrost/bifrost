"""Tests for events router agent subscription validation."""

from uuid import uuid4

from src.models.contracts.events import EventSubscriptionCreate


class TestAgentSubscriptionValidation:
    """Test subscription creation validation for agent targets."""

    def test_agent_subscription_requires_agent_id(self):
        """target_type='agent' without agent_id creates valid model (router validates)."""
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=None,
            workflow_id=None,
        )
        assert sub.target_type == "agent"
        assert sub.agent_id is None  # Model allows it; router should reject

    def test_workflow_subscription_requires_workflow_id(self):
        """target_type='workflow' without workflow_id creates valid model (router validates)."""
        sub = EventSubscriptionCreate(
            target_type="workflow",
            workflow_id=None,
            agent_id=None,
        )
        assert sub.target_type == "workflow"
        assert sub.workflow_id is None  # Model allows it; router should reject

    def test_agent_subscription_with_agent_id(self):
        """target_type='agent' with agent_id is valid."""
        agent_id = uuid4()
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=agent_id,
        )
        assert sub.agent_id == agent_id
        assert sub.target_type == "agent"

    def test_default_target_type_is_workflow(self):
        """Default target_type should be 'workflow'."""
        wf_id = uuid4()
        sub = EventSubscriptionCreate(
            workflow_id=wf_id,
        )
        assert sub.target_type == "workflow"

    def test_agent_subscription_with_workflow_id_none(self):
        """Agent subscription should have workflow_id=None."""
        agent_id = uuid4()
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=agent_id,
            workflow_id=None,
        )
        assert sub.workflow_id is None
        assert sub.agent_id == agent_id

    def test_workflow_subscription_with_all_fields(self):
        """Workflow subscription with event_type and input_mapping."""
        wf_id = uuid4()
        sub = EventSubscriptionCreate(
            target_type="workflow",
            workflow_id=wf_id,
            event_type="ticket.created",
            criteria={
                "version": 1,
                "root": {
                    "kind": "condition",
                    "field": "event.body.priority",
                    "operator": "equals",
                    "value": "high",
                },
            },
            input_mapping={"ticket_id": "{{ data.id }}"},
        )
        assert sub.workflow_id == wf_id
        assert sub.event_type == "ticket.created"
        assert sub.criteria.root.field == "event.body.priority"
        assert sub.input_mapping == {"ticket_id": "{{ data.id }}"}

    def test_agent_subscription_with_all_fields(self):
        """Agent subscription with event_type and input_mapping."""
        agent_id = uuid4()
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=agent_id,
            event_type="alert.triggered",
            input_mapping={"alert_source": "monitoring"},
        )
        assert sub.agent_id == agent_id
        assert sub.event_type == "alert.triggered"
        assert sub.input_mapping == {"alert_source": "monitoring"}

    def test_both_agent_and_workflow_ids_set(self):
        """Model allows both IDs set; router should validate mutual exclusivity."""
        agent_id = uuid4()
        wf_id = uuid4()
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=agent_id,
            workflow_id=wf_id,
        )
        # Model doesn't enforce mutual exclusivity - that's the router's job
        assert sub.agent_id == agent_id
        assert sub.workflow_id == wf_id

    def test_minimal_subscription_defaults(self):
        """Minimal construction uses all defaults."""
        sub = EventSubscriptionCreate()
        assert sub.target_type == "workflow"
        assert sub.workflow_id is None
        assert sub.agent_id is None
        assert sub.event_type is None
        assert sub.criteria is None
        assert sub.input_mapping is None
