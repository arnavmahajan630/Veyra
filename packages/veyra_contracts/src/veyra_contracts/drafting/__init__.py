"""The LLM drafter (C4): samples in, a verified contract template out.

The pipeline, one module per step:

- ``classify``: which envelope layers wrap the template text;
- ``request``: the tokens, which of them vary, and the IF-LLM-DRAFT request;
- ``options``: the fixed lists every answer is chosen from;
- ``schema``: the IF-LLM-DRAFT response and its closed-vocabulary checks;
- ``ollama`` / ``decision`` / ``heuristic`` / ``cache`` / ``drafter``: where a response
  comes from, and ``backends`` picks the model client from settings;
- ``generalize``: token ids → a template pattern, map and contract YAML;
- ``verify``: compile, provenance, type checks.
"""
