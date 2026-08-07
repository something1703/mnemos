"""Mnemos Warden — the governance plane.

The only component in Mnemos that can destroy anything or change policy, and
the only one guaranteed to contain no model call. That combination is the point:
destruction is deterministic, reviewable, and never the output of a token
sampler.

Guarantees, each enforced rather than promised (PHASE_06_GOVERNANCE_WARDEN.md):

  1. IAM: the Warden's execution role carries an explicit Deny on
     bedrock:InvokeModel and every other model-invocation action.
  2. CI: `make no-model-in-warden` fails the build if an LLM client appears
     anywhere in this package's transitive import graph.
  3. Runtime: a startup self-check asserts both and refuses to serve otherwise.

Every operation here requires an authenticated admin scope, an explicit confirm,
a stored reason, and — where dual control is enabled — a second admin key.
"""

__version__ = "0.1.0"
