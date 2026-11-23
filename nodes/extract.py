from comfy_api.latest import io

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
                                tooltip="LLM output to parse (connect LLM Chat / LLM Agent 'output')"),
                io.Combo.Input(
                    "mode",
                    options=["json", "regex"],
                    default="json",
                    tooltip="json: key paths into a JSON object output. regex: one match per pattern.",
                ),
                io.String.Input(
                    "patterns",
                    multiline=True,
                    default="positive =\nnegative =\nseed =",
                    tooltip="One 'label = pattern' per line; socket labels update as you type. "
                            "json mode: pattern = key path (empty = label; use dots for nesting, e.g. "
                            "sampler.name). regex mode: pattern = regex; named group (?P<value>...) or "
                            "group 1 is extracted; prefix (?s) for multiline matches. Lines starting with "
                            "# are ignored.",
                ),
            ],
            outputs=[io.String.Output("field_{}".format(i + 1), tooltip="Extracted value for pattern {}.".format(i + 1))
                     for i in range(MAX_FIELDS)],
        )

    @classmethod
    def execute(cls, text, mode, patterns) -> io.NodeOutput:
        pairs = extract.parse_patterns(patterns)
        if not pairs:
            raise ValueError("Set at least one 'label = pattern' line in the patterns field.")
        if len(pairs) > MAX_FIELDS:
            raise ValueError("At most {} patterns are supported, got {}.".format(MAX_FIELDS, len(pairs)))
        try:
            values = extract.extract_all(text, mode, pairs)
        except extract.ExtractError as exc:
            raise ValueError(str(exc))
        values += [""] * (MAX_FIELDS - len(values))
        return io.NodeOutput(*values)
