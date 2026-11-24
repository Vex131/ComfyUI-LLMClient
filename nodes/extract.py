import logging

from comfy_api.latest import io
from comfy_execution.graph_utils import ExecutionBlocker

from ..providers import extract

MAX_FIELDS = 12


class LLMPatternExtract(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="LLMPatternExtract",
            display_name="LLM Pattern Extract",
            category="LLM Client",
            inputs=[
                io.String.Input("text", multiline=True, default="",
                                tooltip="Text to parse when text_input is not connected."),
                io.String.Input("text_input", optional=True, force_input=True,
                                tooltip="Optional connected text to parse; overrides the text box."),
                io.Combo.Input(
                    "mode",
                    options=["json", "regex"],
                    default="json",
                    tooltip="json: key paths into a JSON object output. regex: one match per pattern.",
                ),
                io.String.Input(
                    "patterns",
                    multiline=True,
                    default="positive_prompt =\nnegative_prompt =",
                    tooltip="One 'label = pattern' per line; socket labels update as you type. "
                            "json mode: pattern = key path (empty = label; use dots for nesting, e.g. "
                            "sampler.name). regex mode: pattern = regex; named group (?P<value>...) or "
                            "group 1 is extracted; prefix (?s) for multiline matches. Lines starting with "
                            "# are ignored.",
                ),
                io.Combo.Input(
                    "on_missing",
                    options=["continue", "block"],
                    default="continue",
                    optional=True,
                    tooltip="continue: return empty strings for missing values. block: skip downstream "
                            "nodes if any configured value is missing or empty.",
                ),
            ],
            outputs=[io.String.Output("field_{}".format(i + 1), tooltip="Extracted value for pattern {}.".format(i + 1))
                     for i in range(MAX_FIELDS)],
        )

    @classmethod
    def execute(cls, text, mode, patterns, on_missing="continue", text_input=None) -> io.NodeOutput:
        if text_input is not None:
            text = text_input
        pairs = extract.parse_patterns(patterns)
        if not pairs:
            raise ValueError("Set at least one 'label = pattern' line in the patterns field.")
        if len(pairs) > MAX_FIELDS:
            raise ValueError("At most {} patterns are supported, got {}.".format(MAX_FIELDS, len(pairs)))
        try:
            values = extract.extract_all(text, mode, pairs)
        except extract.ExtractError as exc:
            if mode != "json":
                raise ValueError(str(exc)) from exc
            logging.warning("LLM Pattern Extract: %s", exc)
            values = [""] * len(pairs)
        missing = [label for (label, _), value in zip(pairs, values) if not value.strip()]
        if missing:
            logging.warning("LLM Pattern Extract: no value for %s (%s).",
                            ", ".join(missing), on_missing)
            if on_missing == "block":
                return io.NodeOutput(*([ExecutionBlocker(None)] * MAX_FIELDS))
        values += [""] * (MAX_FIELDS - len(values))
        return io.NodeOutput(*values)
