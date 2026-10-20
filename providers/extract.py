import json
import re


class ExtractError(ValueError):
    pass


def parse_patterns(text: str) -> list:
    pairs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            label, spec = line.split("=", 1)
        else:
            label, spec = line, ""
        label = label.strip()
        if not label:
            continue
        pairs.append((label, spec.strip()))
    return pairs


def parse_json_output(text: str) -> dict:
    candidate = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        data = json.loads(candidate)
    except ValueError:
        raise ExtractError(
            "Output is not valid JSON. Fix the agent's JSON output, or switch this node to regex mode."
        )
    if not isinstance(data, dict):
        raise ExtractError("Expected a top-level JSON object, got {}.".format(type(data).__name__))
    return data


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def extract_all(text: str, mode: str, pairs: list) -> list:
    data = parse_json_output(text) if mode == "json" else None
    values = []
    for label, spec in pairs:
        if mode == "json":
            path = spec or label
            current = data
            found = True
            for part in path.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    found = False
                    break
            values.append(_stringify(current) if found else "")
        else:
            if not spec:
                raise ExtractError("regex mode needs a pattern for '{}', e.g. '{} = ...'".format(label, label))
            try:
                match = re.search(spec, text or "")
            except re.error as exc:
                raise ExtractError("Invalid regex for '{}': {}".format(label, exc))
            if not match:
                values.append("")
                continue
            groups = match.groupdict()
            if groups.get("value") is not None:
                values.append(groups["value"])
            elif match.groups():
                values.append(match.group(1) if match.group(1) is not None else "")
            else:
                values.append(match.group(0))
    return values
