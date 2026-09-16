# MCP boundary (DevOS)

Canonical product vision: [`plans/DEVOS_PRODUCT_VISION.md`](../../plans/DEVOS_PRODUCT_VISION.md).

## Intent

**MCP is primarily the bridge to external applications and services.**

### Internal (preferred)

```text
Nuha → Specialist Agent → Native DevOS Capability → Resource
```

Examples of native internal resources: workspace files (`FileService`), governed jobs, UCIP-gated shell, project memory APIs.

### External

```text
Nuha / Agent → MCP → External Application / Service
```

Examples: third-party SaaS MCP servers, external GitHub MCP when used as an external integration surface.

## Non-goals

- MCP must not replace the internal agent/capability/governance architecture.
- Filesystem-style MCP presets that target internal project trees are **not** the preferred path when native DevOS file capabilities exist.
- Connecting MCP does not by itself imply UCIP authorization for consequential internal mutations; native governance remains required for governed capabilities.

## Status

MCP client/server connection surfaces: **PARTIALLY IMPLEMENTED**.  
Policy/docs alignment: this document. Code behavior is unchanged by this documentation pass.
