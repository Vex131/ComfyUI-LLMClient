"""Run-level statistics from provider usage and local timing."""
import time

from .context import estimate_tokens, usage_anchor


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class RunStats:
    def __init__(self, max_context=-1, model_key=None):
        self.started = time.monotonic()
        self.max_context = max_context
        self.model_key = model_key
        self.requests = []

    def start(self, payload, compaction=False):
        request = {"start": time.monotonic(), "first": None, "end": None, "usage": None,
                   "timings": None, "compaction": compaction}
        self.update_payload(request, payload)
        self.requests.append(request)
        return request

    def update_payload(self, request, payload):
        request["estimate"] = estimate_tokens(payload.get("messages", []))
        request["anchor"] = usage_anchor(payload.get("messages", []), payload.get("tools"),
                                         self.model_key, 0)

    def chunk(self, request, chunk):
        if isinstance(chunk.get("usage"), dict):
            request["usage"] = chunk["usage"]
        if isinstance(chunk.get("timings"), dict):
            request["timings"] = chunk["timings"]
        choices = chunk.get("choices") or []
        delta = (choices[0].get("delta") or {}) if choices else {}
        if request["first"] is None and any(delta.get(k) for k in
                                            ("content", "reasoning", "reasoning_content", "tool_calls")):
            request["first"] = time.monotonic()

    def finish(self, request):
        request["end"] = time.monotonic()

    def last_anchor(self):
        for request in reversed(self.requests):
            if request["compaction"]:
                continue
            tokens = _number((request.get("usage") or {}).get("prompt_tokens"))
            if request.get("end") is not None and tokens is not None and request.get("anchor"):
                return dict(request["anchor"], tokens=tokens)
            return None
        return None

    def render(self):
        normal = [r for r in self.requests if not r["compaction"]]
        latest = normal[-1] if normal else None
        usage = (latest.get("usage") or {}) if latest else {}
        actual = _number(usage.get("prompt_tokens"))
        used = actual if actual is not None else (latest["estimate"] if latest else None)
        context = "N/A"
        if used is not None:
            context = ("~" if actual is None else "") + "{:,}".format(used)
            if isinstance(self.max_context, int) and self.max_context > 0:
                context += " / {:,} ({:.1f}%)".format(self.max_context, 100 * used / self.max_context)
            else:
                context += " / N/A"

        inputs, outputs, reasoning, cached, total = [], [], [], [], []
        durations = []
        pp_tokens = pp_seconds = pp_count = 0
        for r in self.requests:
            u = r["usage"] or {}
            prompt, completion = _number(u.get("prompt_tokens")), _number(u.get("completion_tokens"))
            prompt_details = u.get("prompt_tokens_details")
            completion_details = u.get("completion_tokens_details")
            cache = _number(prompt_details.get("cached_tokens")) if isinstance(prompt_details, dict) else None
            thought = (_number(completion_details.get("reasoning_tokens"))
                       if isinstance(completion_details, dict) else None)
            cached.append(cache)
            inputs.append(prompt - cache if prompt is not None and cache is not None and cache <= prompt else None)
            reasoning.append(thought)
            outputs.append(completion - thought if completion is not None and thought is not None
                           and thought <= completion else None)
            total.append(prompt + completion if prompt is not None and completion is not None else None)
            if completion is not None and r["first"] is not None and r["end"] is not None:
                durations.append((completion, r["end"] - r["first"]))
            timings = r["timings"] or {}
            n, ms = _number(timings.get("prompt_n")), timings.get("prompt_ms")
            if n is not None and isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
                pp_tokens += n
                pp_seconds += ms / 1000
                pp_count += 1

        def summation(values):
            return sum(values) if values and all(v is not None for v in values) else None

        def display(value):
            return "{:,}".format(value) if value is not None else "N/A"

        input_total, cache_total = summation(inputs), summation(cached)
        rate = ("{:.1f}%".format(100 * cache_total / (input_total + cache_total))
                if input_total is not None and cache_total is not None and input_total + cache_total else "N/A")
        generation_seconds = sum(seconds for _, seconds in durations)
        tps = ("{:.1f} tok/s".format(sum(n for n, _ in durations) / generation_seconds)
               if len(durations) == len(self.requests) and generation_seconds > 0 else "N/A")
        pp = ("{:.1f} tok/s".format(pp_tokens / pp_seconds)
              if pp_seconds and pp_count == len(self.requests) else "N/A")
        ttft = ("{:.2f} s".format(normal[0]["first"] - normal[0]["start"])
                if normal and normal[0]["first"] is not None else "N/A")
        elapsed = int(time.monotonic() - self.started)
        runtime = "{:02d}:{:02d}:{:02d}".format(elapsed // 3600, elapsed // 60 % 60, elapsed % 60)
        return "\n".join([
            "Context:    " + context, "Steps:      " + str(len(normal)),
            "Input:      " + display(input_total), "Output:     " + display(summation(outputs)),
            "Reasoning:  " + display(summation(reasoning)), "Cache Read: " + display(cache_total),
            "Cache Rate: " + rate, "Total:      " + display(summation(total)), "",
            "TPS:        " + tps, "PP:         " + pp, "TTFT:       " + ttft,
            "Run Time:   " + runtime,
        ])
