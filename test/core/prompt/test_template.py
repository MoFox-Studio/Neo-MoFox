"""Tests for core/prompt/template.py."""

from __future__ import annotations

import asyncio

import pytest

import src.kernel.event.core as event_core
from src.core.prompt.template import PromptTemplate, PROMPT_BUILD_EVENT
from src.core.prompt.policies import trim, min_len, header
from src.kernel.event import get_event_bus, EventDecision


class TestPromptTemplate:
    """Test cases for PromptTemplate class."""

    def test_template_creation(self) -> None:
        """Test creating a PromptTemplate."""
        tmpl = PromptTemplate(
            name="test",
            template="Hello {name}",
        )
        assert tmpl.name == "test"
        assert tmpl.template == "Hello {name}"
        assert tmpl.values == {}

    @pytest.mark.asyncio
    async def test_template_set_and_build(self) -> None:
        """Test setting values and building."""
        tmpl = PromptTemplate(
            name="greet",
            template="Hello {name}, you are {age} years old",
        )
        result = await tmpl.set("name", "Alice").set("age", 25).build()
        assert result == "Hello Alice, you are 25 years old"

    @pytest.mark.asyncio
    async def test_template_set_chaining(self) -> None:
        """Test that set returns self for chaining."""
        tmpl = PromptTemplate(name="test", template="{a} {b} {c}")
        result = await tmpl.set("a", 1).set("b", 2).set("c", 3).build()
        assert result == "1 2 3"

    @pytest.mark.asyncio
    async def test_template_with_policy(self) -> None:
        """Test template with render policy."""
        tmpl = PromptTemplate(
            name="test",
            template="Name: {name}\n{bio}",
            policies={"bio": trim().then(min_len(5)).then(header("About:"))},
        )
        result = await tmpl.set("name", "Alice").set("bio", "A developer").build()
        assert result == "Name: Alice\nAbout:\nA developer"

    @pytest.mark.asyncio
    async def test_template_policy_with_empty_value(self) -> None:
        """Test that policy handles empty values."""
        tmpl = PromptTemplate(
            name="test",
            template="{content}",
            policies={"content": header("# Title")},
        )
        result = await tmpl.set("content", "").build()
        assert result == ""

    def test_template_get(self) -> None:
        """Test getting value from template."""
        tmpl = PromptTemplate(name="test", template="{name}")
        tmpl.set("name", "Alice")

        assert tmpl.get("name") == "Alice"
        assert tmpl.get("unknown", "default") == "default"
        assert tmpl.get("unknown") is None

    def test_template_has(self) -> None:
        """Test checking if value exists."""
        tmpl = PromptTemplate(name="test", template="{name}")
        assert tmpl.has("name") is False

        tmpl.set("name", "Alice")
        assert tmpl.has("name") is True

    def test_template_remove(self) -> None:
        """Test removing a value."""
        tmpl = PromptTemplate(name="test", template="{name}")
        tmpl.set("name", "Alice")
        assert tmpl.has("name") is True

        tmpl.remove("name")
        assert tmpl.has("name") is False

    def test_template_clear(self) -> None:
        """Test clearing all values."""
        tmpl = PromptTemplate(name="test", template="{a} {b} {c}")
        tmpl.set("a", 1).set("b", 2).set("c", 3)
        assert len(tmpl.values) == 3

        tmpl.clear()
        assert len(tmpl.values) == 0

    @pytest.mark.asyncio
    async def test_template_build_strict_mode_missing_key(self) -> None:
        """Test build in strict mode with missing key."""
        tmpl = PromptTemplate(name="test", template="{a} {b}")
        tmpl.set("a", 1)

        with pytest.raises(KeyError):
            await tmpl.build(strict=True)

    @pytest.mark.asyncio
    async def test_template_build_non_strict_mode(self) -> None:
        """Test build in non-strict mode (default)."""
        tmpl = PromptTemplate(name="test", template="{a} {b}")
        tmpl.set("a", 1)

        result = await tmpl.build(strict=False)
        assert result == "1 "

    @pytest.mark.asyncio
    async def test_template_build_default_non_strict(self) -> None:
        """Test that default build is non-strict."""
        tmpl = PromptTemplate(name="test", template="{a} {b}")
        tmpl.set("a", 1)

        # Should not raise KeyError
        result = await tmpl.build()
        assert result == "1 "

    def test_template_build_partial(self) -> None:
        """Test partial build keeps unrendered placeholders."""
        tmpl = PromptTemplate(name="test", template="Hello {name}, you are {age}")
        tmpl.set("name", "Alice")

        result = tmpl.build_partial()
        assert result == "Hello Alice, you are {age}"

    def test_template_clone(self) -> None:
        """Test cloning a template."""
        tmpl = PromptTemplate(
            name="test",
            template="{name}",
            policies={"name": trim()},
        )
        tmpl.set("name", "  Alice  ")

        clone = tmpl.clone()

        # Clone should have same values
        assert clone.name == tmpl.name
        assert clone.template == tmpl.template
        assert clone.values == tmpl.values
        assert clone.policies == tmpl.policies

        # Modifying clone should not affect original
        clone.set("name", "Bob")
        assert tmpl.values["name"] == "  Alice  "
        assert clone.values["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_template_with_values(self) -> None:
        """Test creating new template with values."""
        tmpl = PromptTemplate(name="test", template="{name} {age}")

        new_tmpl = tmpl.with_values(name="Alice", age=25)

        # Original should be unchanged
        assert tmpl.values == {}

        # New template should have values
        assert new_tmpl.values == {"name": "Alice", "age": 25}
        assert await new_tmpl.build() == "Alice 25"

    def test_template_repr(self) -> None:
        """Test string representation."""
        tmpl = PromptTemplate(name="test", template="{a} {b}")
        tmpl.set("a", 1).set("b", 2)

        repr_str = repr(tmpl)
        assert "PromptTemplate" in repr_str
        assert "name='test'" in repr_str
        assert "values" in repr_str

    @pytest.mark.asyncio
    async def test_template_complex_policies(self) -> None:
        """Test template with complex policy chains."""
        tmpl = PromptTemplate(
            name="kb_query",
            template="问题：{query}\n\n{context}\n\n回答：",
            policies={
                "context": trim()
                .then(min_len(10))
                .then(header("# 相关内容", sep="\n")),
            },
        )

        # Short context should be filtered out (note: newlines remain)
        result1 = await tmpl.set("query", "test").set("context", "short").build()
        assert result1 == "问题：test\n\n\n\n回答："

        # Long context should be included
        result2 = await tmpl.set("query", "test").set("context", "This is a long enough context").build()
        assert "This is a long enough context" in result2
        assert "# 相关内容" in result2

    @pytest.mark.asyncio
    async def test_template_with_list_value(self) -> None:
        """Test template with list value."""
        from src.core.prompt.policies import join_blocks

        tmpl = PromptTemplate(
            name="test",
            template="Items:\n{items}",
            policies={"items": join_blocks("\n")},
        )

        result = await tmpl.set("items", ["apple", "banana", "cherry"]).build()
        assert result == "Items:\napple\nbanana\ncherry"

    @pytest.mark.asyncio
    async def test_template_with_nested_placeholder(self) -> None:
        """Test template with dot notation placeholder.

        Note: Python's str.format doesn't natively support nested access like {user.name}.
        The placeholder name is treated as a literal string key.
        """
        tmpl = PromptTemplate(
            name="test",
            template="{user_name} is {user_age} years old",
        )

        result = await tmpl.set("user_name", "Alice").set("user_age", 25).build()
        assert result == "Alice is 25 years old"

    @pytest.mark.asyncio
    async def test_template_special_characters(self) -> None:
        """Test template with special characters."""
        tmpl = PromptTemplate(
            name="test",
            template="Hello {{escaped}} {name}",
        )

        result = await tmpl.set("name", "World").build()
        # {{escaped}} should become {escaped}
        assert result == "Hello {escaped} World"


class TestOnPromptBuildEvent:
    """Tests for on_prompt_build event integration."""

    def setup_method(self) -> None:
        """每个测试前清理事件总线中的 on_prompt_build 订阅。"""
        bus = get_event_bus()
        for handler in bus.get_subscribers(PROMPT_BUILD_EVENT):
            bus.unsubscribe(PROMPT_BUILD_EVENT, handler)

    @pytest.mark.asyncio
    async def test_build_fires_event(self) -> None:
        """build 应触发 on_prompt_build 事件并将元数据广播给订阅者。"""
        received: list[dict] = []

        async def handler(event_name: str, params: dict):
            received.append(dict(params))
            return (EventDecision.SUCCESS, params)

        bus = get_event_bus()
        bus.subscribe(PROMPT_BUILD_EVENT, handler)

        tmpl = PromptTemplate(name="evt_test", template="Hello {name}")
        await tmpl.set("name", "World").build()

        assert len(received) == 1
        assert received[0]["name"] == "evt_test"
        assert received[0]["template"] == "Hello {name}"
        assert received[0]["values"]["name"] == "World"

    @pytest.mark.asyncio
    async def test_build_subscriber_can_modify_values(self) -> None:
        """订阅者修改 values 后，build 应使用修改后的值渲染。"""

        async def inject_suffix(event_name: str, params: dict):
            params["values"]["name"] = params["values"]["name"] + "！"
            return (EventDecision.SUCCESS, params)

        bus = get_event_bus()
        bus.subscribe(PROMPT_BUILD_EVENT, inject_suffix)

        tmpl = PromptTemplate(name="modify_test", template="Hi {name}")
        result = await tmpl.set("name", "Alice").build()

        assert result == "Hi Alice！"

    @pytest.mark.asyncio
    async def test_build_subscriber_can_modify_template(self) -> None:
        """订阅者修改 template 后，build 应使用修改后的模板渲染。"""

        async def replace_template(event_name: str, params: dict):
            params["template"] = "Goodbye {name}"
            return (EventDecision.SUCCESS, params)

        bus = get_event_bus()
        bus.subscribe(PROMPT_BUILD_EVENT, replace_template)

        tmpl = PromptTemplate(name="tmpl_replace_test", template="Hello {name}")
        result = await tmpl.set("name", "Bob").build()

        assert result == "Goodbye Bob"

    @pytest.mark.asyncio
    async def test_build_subscriber_exception_is_silenced(self) -> None:
        """订阅者抛出异常时，build 应静默降级并使用原始数据渲染。"""

        async def bad_handler(event_name: str, params: dict):
            raise RuntimeError("intentional error")

        bus = get_event_bus()
        bus.subscribe(PROMPT_BUILD_EVENT, bad_handler)

        tmpl = PromptTemplate(name="fallback_test", template="Hello {name}")
        result = await tmpl.set("name", "Charlie").build()
        assert result == "Hello Charlie"

    @pytest.mark.asyncio
    async def test_build_subscriber_timeout_is_silenced(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """订阅者卡住时，build 应在超时后降级并继续渲染。"""

        async def hung_handler(event_name: str, params: dict):
            await asyncio.Event().wait()
            return (EventDecision.SUCCESS, params)

        monkeypatch.setattr(event_core, "EVENT_HANDLER_TIMEOUT_SECONDS", 0.01)

        bus = get_event_bus()
        bus.subscribe(PROMPT_BUILD_EVENT, hung_handler)

        tmpl = PromptTemplate(name="timeout_test", template="Hello {name}")
        result = await tmpl.set("name", "Delta").build()

        assert result == "Hello Delta"

    @pytest.mark.asyncio
    async def test_build_no_subscriber_skips_event(self) -> None:
        """无订阅者时，build 应直接渲染，不触发事件调度。"""
        tmpl = PromptTemplate(name="no_sub_test", template="{greeting} {who}")
        result = await tmpl.set("greeting", "Hey").set("who", "there").build()
        assert result == "Hey there"

    @pytest.mark.asyncio
    async def test_build_event_params_contain_correct_keys(self) -> None:
        """on_prompt_build 事件 params 必须包含所有规定的字段。"""
        received_keys: list[set] = []

        async def capture(event_name: str, params: dict):
            received_keys.append(set(params.keys()))
            return (EventDecision.SUCCESS, params)

        bus = get_event_bus()
        bus.subscribe(PROMPT_BUILD_EVENT, capture)

        tmpl = PromptTemplate(name="keys_test", template="{x}")
        await tmpl.set("x", "1").build()

        assert received_keys[0] == {"name", "template", "values", "policies", "strict"}
