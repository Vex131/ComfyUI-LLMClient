import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function setButtonLabel(button, text) {
    button.value = text;
    if (button.options) {
        button.options.content = text;
    }
}

async function testConnection(node, button, prefix) {
    const baseUrl = getWidget(node, "base_url")?.value ?? "";
    const apiKey = getWidget(node, "api_key")?.value ?? "";

    setButtonLabel(button, prefix + ": ...");
    node.setDirtyCanvas(true, true);

    try {
        const resp = await api.fetchApi("/llmclient/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
        });
        if (!resp.ok) {
            throw new Error("HTTP " + resp.status);
        }
        const data = await resp.json();
        if (data.ok) {
            const ids = (data.models || []).map((m) => m.id);
            const model = getWidget(node, "model");
            if (model && ids.length > 0) {
                if (model.options && typeof model.options === "object" && !Array.isArray(model.options)) {
                    model.options.values = ids;
                } else {
                    model.options = { values: ids };
                }
                if (!ids.includes(model.value)) {
                    model.value = ids[0];
                }
            }
            const parts = [`${ids.length} models`];
            if (data.profile && data.profile !== "generic") {
                parts.push(data.profile);
            }
            if (data.context) {
                parts.push(`${data.context} ctx`);
            }
            setButtonLabel(button, `${prefix}: OK (${parts.join(" · ")})`);
        } else {
            setButtonLabel(button, prefix + ": FAILED");
            console.warn("LLM Client connection test failed:", data.error);
        }
    } catch (error) {
        console.error("LLM Client connection test failed:", error);
        setButtonLabel(button, prefix + ": FAILED");
    }
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "LLMClient.Provider",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMProvider") {
            return;
        }
        const test = node.addWidget("button", "test_connection", "Test Connection", () =>
            testConnection(node, test, "Test Connection")
        );
    },
});

function parsePatternLabels(text) {
    const labels = [];
    for (let line of (text || "").split("\n")) {
        line = line.trim();
        if (!line || line.startsWith("#")) {
            continue;
        }
        const idx = line.indexOf("=");
        const label = (idx >= 0 ? line.slice(0, idx) : line).trim();
        if (label) {
            labels.push(label);
        }
    }
    return labels;
}

function applyExtractLabels(node) {
    if (!node.outputs) {
        return;
    }
    const labels = parsePatternLabels(getWidget(node, "patterns")?.value);
    node.outputs.forEach((output, i) => {
        output.name = labels[i] || `field_${i + 1}`;
    });
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "LLMClient.Extract",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMPatternExtract") {
            return;
        }
        const patterns = getWidget(node, "patterns");
        if (patterns) {
            const original = patterns.callback;
            patterns.callback = function (value, ...args) {
                original?.call(this, value, ...args);
                applyExtractLabels(node);
            };
        }
        const originalConfigure = node.configure.bind(node);
        node.configure = function (info) {
            const result = originalConfigure(info);
            applyExtractLabels(node);
            return result;
        };
        applyExtractLabels(node);
    },
});
