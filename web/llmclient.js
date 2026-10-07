import { app } from "../../../scripts/api.js";

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

async function testConnection(node, button, prefix) {
    const baseUrl = getWidget(node, "base_url")?.value ?? "";
    const apiKey = getWidget(node, "api_key")?.value ?? "";
    const profile = getWidget(node, "profile")?.value ?? "auto";

    button.label = prefix + ": ...";
    node.setDirtyCanvas(true, true);

    try {
        const resp = await app.fetchApi("/llmclient/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, profile }),
        });
        if (!resp.ok) {
            throw new Error("HTTP " + resp.status);
        }
        const data = await resp.json();
        if (data.ok) {
            const ids = (data.models || []).map((m) => m.id);
            const model = getWidget(node, "model");
            if (model && ids.length > 0) {
                model.options = ids;
                if (!ids.includes(model.value)) {
                    model.value = ids[0];
                }
            }
            button.label = `${prefix}: OK (${ids.length} models)`;
        } else {
            button.label = prefix + ": FAILED";
            console.warn("LLM Client connection test failed:", data.error);
        }
    } catch (error) {
        console.error("LLM Client connection test failed:", error);
        button.label = prefix + ": FAILED";
    }
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "LLMClient.Provider",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMProvider") {
            return;
        }
        const test = node.addWidget("button", "Test Connection", null, () => {});
        const refresh = node.addWidget("button", "Refresh Models", null, () => {});
        test.callback = () => testConnection(node, test, "Test Connection");
        refresh.callback = () => testConnection(node, refresh, "Refresh Models");
    },
});
