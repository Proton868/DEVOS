"""Test Brain Router — agent selection based on goal analysis."""
import pytest

from brain.agents import (
    get_best_agent_for_goal, describe_worker_selection,
    list_agents, AGENT_LIBRARY, AgentPersona,
)


class TestAgentLibrary:
    def test_library_is_populated(self):
        assert len(AGENT_LIBRARY) > 0

    def test_all_agents_have_required_fields(self):
        for slug, agent in AGENT_LIBRARY.items():
            assert agent.slug, f"Missing slug for agent"
            assert agent.name, f"Missing name for {slug}"
            assert agent.division, f"Missing division for {slug}"
            assert agent.description, f"Missing description for {slug}"

    def test_all_agents_have_to_dict(self):
        for slug, agent in AGENT_LIBRARY.items():
            d = agent.to_dict()
            assert d["slug"] == slug
            assert "name" in d
            assert "division" in d


class TestGetBestAgentForGoal:
    def test_frontend_goal_selects_frontend_developer(self):
        agent = get_best_agent_for_goal("build a React dashboard with Tailwind CSS")
        assert agent is not None
        assert agent.slug in ("frontend-developer", "fullstack-engineer", "ui-designer")

    def test_python_goal_selects_backend(self):
        agent = get_best_agent_for_goal("write a Python FastAPI backend with PostgreSQL")
        assert agent is not None
        assert agent.slug in ("python-specialist", "backend-engineer", "fullstack-engineer")

    def test_devops_goal_selects_devops(self):
        agent = get_best_agent_for_goal("set up a Docker CI/CD pipeline with GitHub Actions")
        assert agent is not None
        assert agent.slug in ("devops-engineer", "infrastructure-engineer")

    def test_data_goal_selects_data_engineer(self):
        # "data", "csv", "excel", "pandas", "analysis", "chart", "visualization" are routing keywords
        agent = get_best_agent_for_goal("process a csv file with pandas and create a chart")
        assert agent is not None
        assert agent.slug in ("data-engineer", "python-specialist")

    def test_security_goal_selects_security_engineer(self):
        agent = get_best_agent_for_goal("audit the codebase for security vulnerabilities")
        assert agent is not None
        assert agent.slug in ("security-engineer", "code-reviewer")


class TestDescribeWorkerSelection:
    def test_explanation_includes_routing_info(self):
        explanation = describe_worker_selection("build a React dashboard")
        assert len(explanation) > 20

    def test_explanation_contains_agent_name(self):
        agent = get_best_agent_for_goal("build a React dashboard")
        explanation = describe_worker_selection("build a React dashboard")
        assert agent.slug in explanation or agent.name.lower() in explanation.lower() or "frontend" in explanation.lower()


class TestListAgents:
    def test_list_all_agents(self):
        agents = list_agents()
        assert len(agents) > 0
        for a in agents:
            assert "slug" in a
            assert "name" in a

    def test_list_by_division(self):
        # Use actual division names from the agent library
        all_agents = list_agents()
        known_divisions = {a["division"] for a in all_agents}
        assert len(known_divisions) > 0, "No divisions found in agent library"
        # Pick a division with many agents
        for div in ("engineering", "development", "strategy"):
            agents = list_agents(division=div)
            if len(agents) > 0:
                break
        assert len(agents) > 0, f"No agents found for any division. Known: {known_divisions}"
        first_div = agents[0]["division"]
        for a in agents:
            assert a["division"] == first_div