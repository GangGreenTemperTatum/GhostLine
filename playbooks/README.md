# GhostLine Playbooks

Author your own call flows in YAML. Copy any bundled playbook (e.g. `it-test.yaml`) and tweak.

* Stage names must match `SalesStage` in `src/ghostline/taxonomy.py` (e.g. `RAPPORT`, `CLOSE`).
* Leave `custom_prompt` blank to inherit the default stage prompt.
* `success_regex` matches against the target's utterance; on match → `goto_on_success` (or next stage).
* `goto_on_fail` fires when `max_cycles` retries are exhausted without a `success_regex` match.
* `silent_until` (seconds) overrides the default silence threshold per stage.
* `ambient_ratio` (0–1) sets the background-noise mix weight for that stage's TTS.
* `language_hint` (BCP-47 tag, e.g. `es-ES`) biases the LLM toward a language for that stage.

Open a PR to share new playbooks!
