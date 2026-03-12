"""Tests for core/prompt/manager.py."""

from __future__ import annotations

import pytest

from src.core.prompt.manager import PromptManager, get_prompt_manager, reset_prompt_manager
from src.core.prompt.template import PromptTemplate


class TestPromptManager:
    """Test cases for PromptManager class."""

    def setup_method(self) -> None:
        """Reset manager before each test."""
        reset_prompt_manager()

    def test_manager_singleton(self) -> None:
        """Test that PromptManager is a singleton."""
        manager1 = PromptManager()
        manager2 = PromptManager()

        assert manager1 is manager2

    def test_manager_initialization(self) -> None:
        """Test manager initialization."""
        manager = PromptManager()

        assert manager.count() == 0
        assert manager.list_templates() == []

    def test_register_template(self) -> None:
        """Test registering a template."""
        manager = PromptManager()
        tmpl = PromptTemplate(name="test", template="Hello {name}")

        manager.register_template(tmpl)

        assert manager.count() == 1
        assert manager.has_template("test") is True

    def test_register_duplicate_overwrites(self) -> None:
        """Test that registering duplicate overwrites."""
        manager = PromptManager()
        tmpl1 = PromptTemplate(name="test", template="Template 1: {value}")
        tmpl2 = PromptTemplate(name="test", template="Template 2: {value}")

        manager.register_template(tmpl1)
        manager.register_template(tmpl2)

        retrieved = manager.get_template("test")
        assert retrieved.template == "Template 2: {value}"

    def test_unregister_template(self) -> None:
        """Test unregistering a template."""
        manager = PromptManager()
        tmpl = PromptTemplate(name="test", template="Hello {name}")

        manager.register_template(tmpl)
        assert manager.has_template("test") is True

        result = manager.unregister_template("test")
        assert result is True
        assert manager.has_template("test") is False

    def test_unregister_nonexistent_template(self) -> None:
        """Test unregistering non-existent template."""
        manager = PromptManager()

        result = manager.unregister_template("nonexistent")
        assert result is False

    def test_get_template(self) -> None:
        """Test getting a template."""
        manager = PromptManager()
        tmpl = PromptTemplate(name="test", template="Hello {name}")

        manager.register_template(tmpl)
        retrieved = manager.get_template("test")

        assert retrieved is not None
        assert retrieved.name == "test"
        assert retrieved.template == "Hello {name}"

    def test_get_nonexistent_template(self) -> None:
        """Test getting non-existent template."""
        manager = PromptManager()

        retrieved = manager.get_template("nonexistent")
        assert retrieved is None

    def test_get_or_create_new(self) -> None:
        """Test get_or_create with new template."""
        manager = PromptManager()

        tmpl = manager.get_or_create(
            name="test",
            template="Hello {name}",
        )

        assert tmpl is not None
        assert tmpl.name == "test"
        assert tmpl.template == "Hello {name}"
        assert manager.count() == 1

    def test_get_or_create_existing(self) -> None:
        """Test get_or_create returns existing template."""
        manager = PromptManager()

        tmpl1 = manager.get_or_create(
            name="test",
            template="Template 1: {value}",
        )

        tmpl2 = manager.get_or_create(
            name="test",
            template="Template 2: {value}",
        )

        # Should return the same instance
        assert tmpl1 is tmpl2
        assert tmpl1.template == "Template 1: {value}"

    def test_get_or_create_with_policies(self) -> None:
        """Test get_or_create with policies."""
        from src.core.prompt.policies import trim

        manager = PromptManager()

        tmpl = manager.get_or_create(
            name="test",
            template="Value: {value}",
            policies={"value": trim()},
        )

        assert "value" in tmpl.policies

    def test_has_template(self) -> None:
        """Test checking if template exists."""
        manager = PromptManager()
        tmpl = PromptTemplate(name="test", template="Hello {name}")

        assert manager.has_template("test") is False

        manager.register_template(tmpl)
        assert manager.has_template("test") is True

    def test_list_templates(self) -> None:
        """Test listing all templates."""
        manager = PromptManager()

        tmpl1 = PromptTemplate(name="test1", template="A")
        tmpl2 = PromptTemplate(name="test2", template="B")
        tmpl3 = PromptTemplate(name="test3", template="C")

        manager.register_template(tmpl1)
        manager.register_template(tmpl2)
        manager.register_template(tmpl3)

        templates = manager.list_templates()
        assert set(templates) == {"test1", "test2", "test3"}

    def test_list_templates_empty(self) -> None:
        """Test listing templates when none exist."""
        manager = PromptManager()

        assert manager.list_templates() == []

    def test_clear(self) -> None:
        """Test clearing all templates."""
        manager = PromptManager()

        tmpl1 = PromptTemplate(name="test1", template="A")
        tmpl2 = PromptTemplate(name="test2", template="B")

        manager.register_template(tmpl1)
        manager.register_template(tmpl2)
        assert manager.count() == 2

        manager.clear()
        assert manager.count() == 0
        assert manager.list_templates() == []

    def test_count(self) -> None:
        """Test counting templates."""
        manager = PromptManager()

        assert manager.count() == 0

        for i in range(5):
            tmpl = PromptTemplate(name=f"test{i}", template=f"{i}")
            manager.register_template(tmpl)

        assert manager.count() == 5

    def test_manager_repr(self) -> None:
        """Test string representation."""
        manager = PromptManager()
        tmpl = PromptTemplate(name="test", template="Hello")
        manager.register_template(tmpl)

        repr_str = repr(manager)
        assert "PromptManager" in repr_str
        assert "templates=1" in repr_str


class TestGlobalManager:
    """Test cases for global manager functions."""

    def setup_method(self) -> None:
        """Reset global manager before each test."""
        reset_prompt_manager()

    def test_get_prompt_manager_singleton(self) -> None:
        """Test that get_prompt_manager returns singleton."""
        manager1 = get_prompt_manager()
        manager2 = get_prompt_manager()

        assert manager1 is manager2

    def test_get_prompt_manager_creates_instance(self) -> None:
        """Test that get_prompt_manager creates instance on first call."""
        manager = get_prompt_manager()

        assert manager is not None
        assert isinstance(manager, PromptManager)

    def test_reset_prompt_manager(self) -> None:
        """Test resetting global manager."""
        manager1 = get_prompt_manager()
        manager1.register_template(PromptTemplate(name="test", template="Hello"))

        assert manager1.count() == 1

        reset_prompt_manager()

        manager2 = get_prompt_manager()
        assert manager2 is not manager1
        assert manager2.count() == 0

    @pytest.mark.asyncio
    async def test_global_manager_integration(self) -> None:
        """Test using global manager with templates."""
        manager = get_prompt_manager()

        tmpl = PromptTemplate(
            name="greet",
            template="Hello {name}",
        )

        # Template doesn't auto-register to global manager
        # (that's done by PromptTemplate if needed, but currently not implemented)
        # So we register manually
        manager.register_template(tmpl)

        retrieved = manager.get_template("greet")
        assert retrieved is not None

        result = await retrieved.set("name", "World").build()
        assert result == "Hello World"


class TestManagerIntegration:
    """Integration tests for manager with templates."""

    def setup_method(self) -> None:
        """Reset manager before each test."""
        reset_prompt_manager()

    @pytest.mark.asyncio
    async def test_workflow_full_cycle(self) -> None:
        """Test full workflow: create, register, retrieve, use."""
        manager = get_prompt_manager()

        # Create and register
        tmpl = manager.get_or_create(
            name="kb_query",
            template="问题：{query}\n\n{context}\n\n请回答：",
        )

        # Retrieve and use
        retrieved = manager.get_template("kb_query")
        assert retrieved is tmpl

        result = await retrieved.set("query", "如何学习Python？").set("context", "").build()
        assert "如何学习Python？" in result

    @pytest.mark.asyncio
    async def test_multiple_templates_management(self) -> None:
        """Test managing multiple templates."""
        manager = get_prompt_manager()

        templates = [
            PromptTemplate(name="greet", template="Hello {name}"),
            PromptTemplate(name="farewell", template="Goodbye {name}"),
            PromptTemplate(name="question", template="{question}?"),
        ]

        for tmpl in templates:
            manager.register_template(tmpl)

        assert manager.count() == 3
        assert set(manager.list_templates()) == {"greet", "farewell", "question"}

        # Use each template
        greet = manager.get_template("greet")
        farewell = manager.get_template("farewell")
        question = manager.get_template("question")

        assert greet is not None
        assert farewell is not None
        assert question is not None

        assert await greet.set("name", "Alice").build() == "Hello Alice"
        assert await farewell.set("name", "Bob").build() == "Goodbye Bob"
        assert await question.set("question", "How are you").build() == "How are you?"

    @pytest.mark.asyncio
    async def test_template_isolation(self) -> None:
        """Test that templates are isolated from each other."""
        manager = get_prompt_manager()

        tmpl1 = manager.get_or_create("test1", template="{a} {b}")
        tmpl2 = manager.get_or_create("test2", template="{x} {y}")

        tmpl1.set("a", 1).set("b", 2)
        tmpl2.set("x", "X").set("y", "Y")

        # tmpl1 should not have x, y
        assert tmpl1.get("x") is None
        assert tmpl1.get("y") is None

        # tmpl2 should not have a, b
        assert tmpl2.get("a") is None
        assert tmpl2.get("b") is None

        # Build results should be correct
        assert await tmpl1.build() == "1 2"
        assert await tmpl2.build() == "X Y"
