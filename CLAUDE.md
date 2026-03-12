# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Neo-MoFox is a refactored chatbot framework with a strict three-layer architecture:

- **kernel** - Basic capabilities layer providing platform-independent technical infrastructure (database, vector DB, scheduler, event bus, LLM, config, logger, concurrency, storage)
- **core** - Domain/mental layer implementing memory, conversation, and behavior using kernel capabilities (components, managers, prompt, transport, models)
- **app** - Application layer assembling kernel and core into a runnable Bot system with plugin extensions

See [MoFox 重构指导总览.md](MoFox 重构指导总览.md) for complete architecture documentation.

## Development Commands

### Dependency Management
The project uses `uv` for dependency management (Python >= 3.11 required).

```bash
# Add a new dependency
uv add <package_name>

# Install ruff for linting
uv tool install ruff
```

### Testing
```bash
# Run all tests
pytest

# Run a specific test file
pytest test/path/to/test_file.py

# Run with coverage
pytest --cov=src
```

Test coverage must reach 100% for all code in `src/`.

### Code Quality
```bash
# Run ruff for linting
ruff check src/

# Run ruff with auto-fix
ruff check --fix src/
```

## Architecture Highlights

### Kernel Layer (`src/kernel/`)

Provides low-level technical capabilities with minimal business logic:

- **db/** - Database abstraction with SQLAlchemy engines, CRUD operations, and query builder
- **vector_db/** - Vector database interface (currently ChromaDB-based)
- **scheduler/** - Unified task scheduler supporting time-based and custom triggers
- **event/** - Minimal Pub/Sub event bus
- **llm/** - Multi-vendor LLM interface with standardized payloads and response handling
- **config/** - Type-safe configuration system using Pydantic and TOML files
- **logger/** - Unified logging with color support and metadata tracking
- **concurrency/** - Async task management with TaskGroup and WatchDog monitoring
- **storage/** - Simple JSON-based local persistence

### Core Layer (`src/core/`)

Contains plugin components and their managers:

**Component Types** (in `components/base/`):
- Action - "Active" responses triggered by LLM tool calling (e.g., "send message")
- Tool - "Query" functions for LLM (e.g., calculator, translator)
- Adapter - Platform communication bridge following mofox-wire standard
- Chatter - Bot's intelligence core defining conversation logic
- Command - Command handlers (e.g., `/help`, `/mute`) with routing tree
- Collection - Nested groups of Actions/Tools for LLM discovery
- Config - Plugin configuration with hot-reload support
- EventHandler - Event subscriber for system/plugin events
- Service - Exposed functionality for inter-plugin communication
- Router - FastAPI HTTP route definitions
- Plugin - Root component containing all other plugin components

**Key Managers** (in `components/managers/`):
- action_manager - Schema generation, activation filtering, execution routing
- chatter_manager - Chatter lifecycle and LLMUsable filtering
- tool_manager - MCP adaptation, tool history, execution tracking
- plugin_manager - Plugin loading (folder/zip/.mfp) and lifecycle

### App Layer (`src/app/`)

- plugin_system/ - Plugin base classes and API exports
- built_in/ - Built-in plugins
- runtime/ - Bot runtime (bot.py)
- main.py - Application entry point

## Code Standards

From [代码规范.md](代码规范.md):

1. **PEP 8** style guide compliance
2. **Type annotations** required for all function parameters and return values
3. **Docstrings** required for all functions, classes, and file headers
4. **100% test coverage** for all `src/` code
5. No fallback mechanism abuse - ensure code robustness
6. No AI-generated commit messages without human review
7. Strict human review for all AI-generated code

## Component Signature Format

Components are identified by signatures in format: `plugin_name:component_type:component_name`

Example: `"my_plugin:action:send_emoji"`

## LLM Request/Response Pattern

The LLM module uses a chainable pattern:

```python
from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text, Tool

llm_request = LLMRequest(model_set, "my_request")
llm_request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

# Supports both streaming and non-streaming via unified interface
llm_response = await llm_request.send()
# OR: async for chunk in llm_request.send(): ...

# Response can chain back into requests
llm_response.add_payload(LLMPayload(ROLE.USER, Text("Follow up")))
result = await llm_response.send()
```

## Task Management

Use `task_manager` instead of `asyncio.create_task()` for all async operations:

```python
from src.kernel.concurrency import get_task_manager

tm = get_task_manager()

# Basic task
tm.create_task(func(), name="my_task")

# TaskGroup for scoped tasks
async with tm.group(name="group_name", timeout=30, cancel_on_error=True) as tg:
    tg.create_task(func1())
    tg.create_task(func2())
```

## Config System Pattern

Define configs using `ConfigBase` and `SectionBase`:

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

class MyConfig(ConfigBase):
    @config_section("general")
    class GeneralSection(SectionBase):
        enabled: bool = Field(default=True, description="Enable feature")

my_config = MyConfig.load("config/my_config.toml")
```

## Database Operations

```python
from src.kernel.db import CRUDBase, QueryBuilder

# CRUD operations
crud = CRUDBase(MyModel)
result = await crud.get_by(id=123)

# Query builder
result = await QueryBuilder(MyModel).filter(field="value").first()
```
