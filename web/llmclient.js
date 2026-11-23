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

    setButtonLabel(button, prefix + ": ...");
    node.setDirtyCanvas(true, true);

    try {
        const resp = await api.fetchApi("/llmclient/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ base_url: baseUrl }),
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
        node.addWidget("button", "api_keys", "Manage API Keys", () =>
            openApiKeys(getWidget(node, "base_url")?.value ?? "")
        );
    },
});

async function fetchVaultStatus() {
    const resp = await api.fetchApi("/llmclient/secrets/status");
    if (!resp.ok) {
        throw new Error("HTTP " + resp.status);
    }
    return resp.json();
}

async function postVaultSecret(body) {
    const resp = await api.fetchApi("/llmclient/secrets/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    let data = {};
    try {
        data = await resp.json();
    } catch (error) {
        data = {};
    }
    if (!resp.ok || data.error) {
        throw new Error(data.error || "HTTP " + resp.status);
    }
    return data;
}

async function postSearchSecret(body) {
    const resp = await api.fetchApi("/llmclient/secrets/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    let data = {};
    try {
        data = await resp.json();
    } catch (error) {
        data = {};
    }
    if (!resp.ok || data.error) {
        throw new Error(data.error || "HTTP " + resp.status);
    }
    return data;
}

function renderApiKeys(baseUrl = "") {
    const element = (tag, text, style = "") => {
        const el = document.createElement(tag);
        if (text) el.textContent = text;
        el.style.cssText = style;
        return el;
    };
    const button = (text) => {
        const el = element("button", text, "padding:7px 12px;border-radius:6px;cursor:pointer;");
        el.type = "button";
        return el;
    };
    const input = (type, placeholder, label) => {
        const el = element("input", "", "box-sizing:border-box;width:100%;padding:9px;border-radius:6px;");
        el.type = type;
        el.placeholder = placeholder;
        el.setAttribute("aria-label", label);
        if (type === "password") el.autocomplete = "off";
        return el;
    };
    const sectionStyle = "border:1px solid var(--border-color,#444);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:10px;";
    const muted = "font-size:12px;opacity:.75;line-height:1.5;";
    const wrap = element("div", "", "display:flex;flex-direction:column;gap:16px;padding:20px;max-width:720px;box-sizing:border-box;");
    const heading = element("h2", "API Keys", "margin:0;font-size:20px;");
    const description = element("div", "API keys are optional and matched to provider base URLs. If your server doesn't require one, enter its URL directly in the provider node. API keys are not saved in workflows.", muted);
    const storage = element("div", "Checking API key storage...", muted);
    const diagnostics = element("details", "", muted);
    const diagnosticsTitle = element("summary", "Storage details");
    const diagnosticsBody = element("div", "", "white-space:pre-wrap;overflow-wrap:anywhere;");
    diagnostics.append(diagnosticsTitle, diagnosticsBody);
    const notices = element("div", "", "font-size:12px;color:#fbbf24;white-space:pre-wrap;");
    const error = element("div", "", "font-size:13px;color:#f87171;");
    error.setAttribute("role", "alert");

    const providers = element("section", "", sectionStyle);
    const providerTitle = element("h3", "Model provider API keys", "margin:0;font-size:16px;");
    const list = element("div", "", "display:flex;flex-direction:column;gap:8px;");
    const nameInput = input("text", "Provider name (optional)", "Provider name (optional)");
    nameInput.maxLength = 100;
    const urlInput = input("text", "https://api.example.com/v1", "Provider base URL");
    urlInput.value = baseUrl;
    const providerFields = element("div", "", "display:flex;flex-wrap:wrap;gap:8px;");
    nameInput.style.cssText += "flex:1 1 180px;min-width:0;";
    urlInput.style.cssText += "flex:1 1 180px;min-width:0;";
    providerFields.append(nameInput, urlInput);
    const keyInput = input("password", "Enter provider API key", "Provider API key");
    const save = button("Save provider API key");
    const formActions = element("div");
    formActions.append(save);
    providers.append(providerTitle, list, providerFields, keyInput, formActions);

    const search = element("details", "", "border:1px solid var(--border-color,#444);border-radius:10px;padding:16px;");
    const searchTitle = element("summary", "Web search providers", "cursor:pointer;font-weight:600;font-size:16px;");
    const fallbackHelp = element("p", "Web search works without an API key using a fallback provider.", muted);
    const searchHelp = element("p", "Add a Tavily API key to use Tavily for web search.", muted);
    const searchStatus = element("div", "", muted);
    const searchInput = input("password", "Enter Tavily API key", "Tavily API key");
    const searchSave = button("Save Tavily API key");
    const searchDelete = button("Remove Tavily API key");
    searchDelete.hidden = true;
    searchStatus.setAttribute("aria-live", "polite");
    const searchActions = element("div", "", "display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;");
    searchActions.append(searchSave, searchDelete);
    const searxTitle = element("h3", "SearXNG", "margin:18px 0 8px;font-size:15px;");
    const searxHelp = element("p", "Enter the URL of a SearXNG server with JSON search enabled.", muted);
    const searxStatus = element("div", "", muted);
    const searxInput = input("url", "http://127.0.0.1:8888", "SearXNG server URL");
    const searxSave = button("Save SearXNG URL");
    const searxDelete = button("Remove SearXNG URL");
    searxDelete.hidden = true;
    searxStatus.setAttribute("aria-live", "polite");
    const searxActions = element("div", "", "display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;");
    searxActions.append(searxSave, searxDelete);
    search.append(searchTitle, fallbackHelp, searchHelp, searchStatus, searchInput, searchActions,
                  searxTitle, searxHelp, searxStatus, searxInput, searxActions);

    const refresh = async () => {
        try {
            const status = await fetchVaultStatus();
            const stores = {
                "win-credential-manager": "Windows Credential Manager",
                "win-dpapi-file": "encrypted Windows file",
                "macos-keychain": "macOS Keychain",
                "linux-secret-service": "Linux Secret Service",
                "file": "unencrypted local file",
            };
            storage.textContent = "API key storage: " + (stores[status.backend] || "unknown");
            if (status.backend === "file") storage.textContent += " — Warning: API keys are not encrypted.";
            diagnosticsBody.textContent = [
                "Storage backend: " + (status.backend || "unknown"),
                status.fallback_reason ? "Preferred storage unavailable: " + status.fallback_reason : "",
            ].filter(Boolean).join("\n");
            notices.textContent = (status.notices || []).join("\n");
            const tavilySaved = !!status.search?.set;
            searchStatus.textContent = tavilySaved ? "Tavily API key saved" : "No Tavily API key saved.";
            searchStatus.style.cssText = tavilySaved
                ? "font-size:12px;opacity:.8;line-height:1.5;"
                : muted;
            searchInput.placeholder = tavilySaved ? "Enter a new Tavily API key to replace it" : "Enter Tavily API key";
            searchSave.textContent = tavilySaved ? "Replace Tavily API key" : "Save Tavily API key";
            searchDelete.hidden = !tavilySaved;
            searchDelete.disabled = !status.search?.set;
            searxInput.value = status.search?.searxng_url || "";
            searxStatus.textContent = status.search?.searxng_url ? "SearXNG URL saved."
                : status.search?.searxng_env ? "Using SearXNG URL from the environment."
                : "No SearXNG URL configured.";
            searxStatus.style.cssText = status.search?.searxng_url
                ? "font-size:12px;opacity:.8;line-height:1.5;"
                : muted;
            searxSave.textContent = status.search?.searxng_url ? "Update SearXNG URL" : "Save SearXNG URL";
            searxDelete.hidden = !status.search?.searxng_url;
            searxDelete.disabled = !status.search?.searxng_url;
            list.replaceChildren();
            for (const entry of status.llm || []) {
                const row = element("div", "", "display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;padding:10px 12px;border:1px solid var(--border-color,#444);border-radius:7px;");
                const identity = element("div", "", "min-width:0;flex:1;");
                if (entry.name) identity.append(element("div", entry.name, "font-weight:600;font-size:13px;overflow-wrap:anywhere;"));
                identity.append(element("div", entry.base_url, "font-size:13px;overflow-wrap:anywhere;"));
                const actions = element("div", "", "display:flex;gap:8px;");
                const replace = button("Replace");
                replace.setAttribute("aria-label", "Replace API key for " + entry.base_url);
                replace.onclick = () => { nameInput.value = entry.name || ""; urlInput.value = entry.base_url; keyInput.focus(); };
                const remove = button("Remove");
                remove.setAttribute("aria-label", "Remove API key for " + entry.base_url);
                remove.onclick = async () => {
                    error.textContent = "";
                    try {
                        await postVaultSecret({ base_url: entry.base_url, api_key: "" });
                        await refresh();
                    } catch (exc) { error.textContent = exc.message; }
                };
                actions.append(replace, remove);
                row.append(identity, actions);
                list.append(row);
            }
            if (!(status.llm || []).length) {
                list.append(element("div", "No provider API keys saved.", muted));
            }
        } catch (exc) {
            storage.textContent = "API key storage status unavailable.";
            error.textContent = exc.message;
        }
    };
    save.onclick = async () => {
        const url = urlInput.value.trim();
        if (!url || !keyInput.value) {
            error.textContent = "Enter a provider base URL and API key.";
            return;
        }
        error.textContent = "";
        try {
            await postVaultSecret({ base_url: url, api_key: keyInput.value, name: nameInput.value.trim() });
            keyInput.value = "";
            await refresh();
        } catch (exc) { error.textContent = exc.message; }
    };
    searchSave.onclick = async () => {
        if (!searchInput.value) {
            error.textContent = "Enter a Tavily API key.";
            return;
        }
        error.textContent = "";
        try {
            await postSearchSecret({ api_key: searchInput.value });
            searchInput.value = "";
            await refresh();
        } catch (exc) { error.textContent = exc.message; }
    };
    searchDelete.onclick = async () => {
        error.textContent = "";
        try {
            await postSearchSecret({ api_key: "" });
            searchInput.value = "";
            await refresh();
        } catch (exc) { error.textContent = exc.message; }
    };
    searxSave.onclick = async () => {
        if (!searxInput.value.trim()) {
            error.textContent = "Enter a SearXNG server URL.";
            return;
        }
        error.textContent = "";
        try {
            await postSearchSecret({ searxng_url: searxInput.value.trim() });
            await refresh();
        } catch (exc) { error.textContent = exc.message; }
    };
    searxDelete.onclick = async () => {
        error.textContent = "";
        try {
            await postSearchSecret({ searxng_url: "" });
            await refresh();
        } catch (exc) { error.textContent = exc.message; }
    };
    wrap.append(heading, description, storage, diagnostics, notices, error, providers, search);
    refresh();
    return wrap;
}

function openApiKeys(baseUrl) {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("aria-label", "Manage API Keys");
    dialog.style.cssText = "color:var(--fg-color,#ddd);background:var(--comfy-menu-bg,#222);border:1px solid #666;border-radius:12px;padding:0;width:min(720px,92vw);max-height:90vh;overflow:auto;";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close";
    close.style.cssText = "margin:0 20px 20px;padding:7px 14px;border-radius:6px;cursor:pointer;";
    close.onclick = () => dialog.close();
    dialog.append(renderApiKeys(baseUrl), close);
    dialog.addEventListener("click", (event) => {
        const bounds = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom)) {
            dialog.close();
        }
    });
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    document.body.append(dialog);
    dialog.showModal();
}

app.registerExtension({
    name: "LLMClient.Vault",
    bottomPanelTabs: [
        { id: "LLMClient.Vault", title: "LLM API Keys", type: "custom",
          render: (el) => el.replaceChildren(renderApiKeys()) },
    ],
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
