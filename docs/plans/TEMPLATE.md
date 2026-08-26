# M<N> — <title>

## Goal

One paragraph. What this milestone makes possible that was not possible
before. No implementation detail.

## Interface

Exact signatures and types — precise enough to build from without reading
the design conversation. Name every module, class, function, dataclass and
enum, with parameter types and return types. State which of them are
public API and which are internal. If an operation touches a target
machine, say explicitly whether it is LIVE, OFFLINE, or both, and how it
knows.

```python
# example
def example(arg: str) -> bool: ...
```

## Constraints

Hard requirements the implementation must respect: standard library only
unless stated, performance or timing limits, file layout, safety rules,
anything that must not be touched.

## Out of scope

What this milestone deliberately does not do, so BUILD does not improvise
it. Name the milestone that will do it instead where one exists.

## Done when

A single testable condition, not a description. It must be something a
person or a test run can check and get a yes or no from.

Example: `pytest tests/test_channel.py` passes and `ruff check` is clean,
and `python -m bedside.lab reset wipe-bootloader` returns exit code 0.
