import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

function getWidget(node, name) {
    return node?.widgets?.find((w) => w.name === name);
}

// Separate *_input sockets override their matching text widgets. Other string
// widgets use ComfyUI's normal convert-to-input behavior instead.
app.registerExtension({
    name: "LLMClient.ConnectedText",
    nodeCreated(node) {
        if (!node.comfyClass?.startsWith("LLM")) return;
        const fields = (node.inputs || []).flatMap(input => {
            if (!input.name.endsWith("_input")) return [];
            const widget = getWidget(node, input.name.slice(0, -6));
            if (!widget || !widget.element) return [];
            return [{ input: input.name, widget, hidden: false, saved: null }];
        });
        if (!fields.length) return;

        const refresh = (changedInput, connected) => {
            let changed = false;
            let restoredHeight = 0;
            for (const field of fields) {
                const input = node.inputs?.find(item => item.name === field.input);
                const hide = changedInput === field.input ? connected : input?.link != null;
                if (hide === field.hidden) continue;
                const widget = field.widget;
                if (hide) {
                    field.saved = {
                        type: widget.type, computeSize: widget.computeSize,
                        display: widget.element.style.display, height: node.size?.[1],
                    };
                    widget.type = "hidden";
                    widget.computeSize = () => [0, -4];
                    widget.element.style.display = "none";
                } else if (field.saved) {
                    widget.type = field.saved.type;
                    widget.computeSize = field.saved.computeSize;
                    widget.element.style.display = field.saved.display;
                    restoredHeight = Math.max(restoredHeight, field.saved.height || 0);
                    field.saved = null;
                }
                field.hidden = hide;
                changed = true;
            }
            if (changed && node.size && node.computeSize && node.setSize) {
                const size = node.computeSize();
                node.setSize([Math.max(node.size[0], size[0]), Math.max(size[1], restoredHeight)]);
            }
            if (changed) node.setDirtyCanvas?.(true, true);
        };
        const previousConnection = node.onConnectionsChange;
        node.onConnectionsChange = function (type, slot, connected, ...args) {
            const result = previousConnection?.call(this, type, slot, connected, ...args);
            if (type === 1) refresh(node.inputs?.[slot]?.name, connected);
            return result;
        };
        const previousConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            const result = previousConfigure?.apply(this, args);
            setTimeout(refresh, 0);
            return result;
        };
        refresh();
    },
});

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
    window.dispatchEvent(new Event("llmclient-provider-changed"));
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "LLMClient.AgentWebAccess",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMAgentConfig") return;
        const web = getWidget(node, "web_tool");
        const local = getWidget(node, "allow_local_web");
        if (!web || !local) return;
        const originalType = local.type;
        const originalSize = local.computeSize;
        const refresh = () => {
            local.type = web.value ? originalType : "hidden";
            local.computeSize = web.value ? originalSize : () => [0, -4];
            node.setDirtyCanvas?.(true, true);
        };
        const callback = web.callback;
        web.callback = function (...args) {
            const result = callback?.apply(this, args);
            refresh();
            return result;
        };
        const configured = node.onConfigure;
        node.onConfigure = function (...args) {
            const result = configured?.apply(this, args);
            refresh();
            return result;
        };
        refresh();
    },
});

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
        for (const name of ["base_url", "model"]) {
            const widget = getWidget(node, name);
            if (!widget) continue;
            const original = widget.callback;
            widget.callback = function (...args) {
                const result = original?.apply(this, args);
                window.dispatchEvent(new Event("llmclient-provider-changed"));
                return result;
            };
        }
    },
});

app.registerExtension({
    name: "LLMClient.ReasoningModes",
    nodeCreated(node) {
        if (!["LLMChat", "LLMAgent"].includes(node.comfyClass)) return;
        let selector;
        let sequence = 0;
        const reasoningField = getWidget(node, "reasoning_effort");
        if (reasoningField) {
            const oldCallback = reasoningField.callback;
            reasoningField.callback = function (...args) {
                const result = oldCallback?.apply(this, args);
                if (selector?.options?.values?.includes(reasoningField.value)) {
                    selector.value = reasoningField.value;
                }
                return result;
            };
        }
        const refresh = async () => {
            const current = ++sequence;
            const input = node.inputs?.find(item => item.name === "model");
            const link = input?.link != null ? node.graph?.links?.[input.link] : null;
            const provider = link && node.graph?.getNodeById(link.origin_id);
            const base_url = getWidget(provider, "base_url")?.value;
            const model = getWidget(provider, "model")?.value;
            let modes = [];
            if (base_url && model) {
                try {
                    const response = await api.fetchApi("/llmclient/reasoning-options", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ base_url, model }),
                    });
                    if (response.ok) modes = (await response.json()).modes || [];
                } catch (error) {
                    console.warn("Unable to load reasoning modes:", error);
                }
            }
            if (current !== sequence) return;
            const field = getWidget(node, "reasoning_effort");
            if (!field) return;
            if (!modes.length) {
                if (selector) {
                    const index = node.widgets.indexOf(selector);
                    if (index !== -1) node.widgets.splice(index, 1);
                    selector = undefined;
                }
            } else {
                const selected = modes.includes(field.value) ? field.value :
                    (modes.includes("medium") ? "medium" : modes[0]);
                field.value = selected;
                if (!selector) {
                    selector = node.addWidget("combo", "provider_reasoning_modes", selected, value => {
                        field.value = value;
                        field.callback?.(value);
                    }, { values: modes });
                    selector.serialize = false;
                } else {
                    selector.options.values = modes;
                    selector.value = selected;
                }
            }
            node.setDirtyCanvas(true, true);
        };
        const original = node.onConnectionsChange;
        node.onConnectionsChange = function (...args) {
            const result = original?.apply(this, args);
            refresh();
            return result;
        };
        const previousConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            const result = previousConfigure?.apply(this, args);
            setTimeout(refresh, 0);
            return result;
        };
        window.addEventListener("llmclient-provider-changed", refresh);
        const previousRemoved = node.onRemoved;
        node.onRemoved = function (...args) {
            window.removeEventListener("llmclient-provider-changed", refresh);
            return previousRemoved?.apply(this, args);
        };
        setTimeout(refresh, 0);
    },
});

app.registerExtension({
    name: "LLMClient.ModelConfigLayout",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMModelConfig") return;
        const widgets = node.widgets || [];
        const legacyOutput = getWidget(node, "max_tokens");
        if (legacyOutput && getWidget(node, "max_output_tokens")) {
            legacyOutput.type = "hidden";
            legacyOutput.computeSize = () => [0, -4];
        }
        const context = getWidget(node, "max_context");
        const timeout = getWidget(node, "timeout_seconds");
        const from = widgets.indexOf(context), to = widgets.indexOf(timeout);
        if (from >= 0 && to >= 0 && from > to) {
            widgets.splice(from, 1);
            widgets.splice(to, 0, context);
        }
        const timeoutIndex = widgets.indexOf(timeout);
        if (timeoutIndex >= 0 && timeoutIndex !== widgets.length - 1) {
            widgets.splice(timeoutIndex, 1);
            widgets.push(timeout);
        }
        const configured = node.onConfigure;
        node.onConfigure = function (info, ...args) {
            const result = configured?.call(this, info, ...args);
            const layout = info?.properties?.llmclient_model_config_layout;
            if (!layout && Array.isArray(info?.widgets_values)) {
                const named = info.widgets_values_named;
                const oldTimeout = named?.timeout_seconds ?? info.widgets_values[4];
                const oldContext = named?.max_context ?? info.widgets_values[5] ?? -1;
                if (timeout && typeof oldTimeout === "number") timeout.value = oldTimeout;
                if (context && typeof oldContext === "number") context.value = oldContext;
            } else if (layout === 2 && Array.isArray(info?.widgets_values)) {
                const named = info.widgets_values_named;
                const oldContext = named?.max_context ?? info.widgets_values[3];
                const oldTimeout = named?.timeout_seconds ?? info.widgets_values[4];
                const oldOutput = named?.max_output_tokens ?? info.widgets_values[5];
                const oldLegacyOutput = named?.max_tokens ?? info.widgets_values[6];
                const output = getWidget(node, "max_output_tokens");
                if (context && typeof oldContext === "number") context.value = oldContext;
                if (timeout && typeof oldTimeout === "number") timeout.value = oldTimeout;
                if (output && typeof oldOutput === "number") output.value = oldOutput;
                if (legacyOutput && typeof oldLegacyOutput === "number") legacyOutput.value = oldLegacyOutput;
            }
            node.properties ||= {};
            node.properties.llmclient_model_config_layout = 3;
            return result;
        };
        node.properties ||= {};
        node.properties.llmclient_model_config_layout = 3;
    },
});

app.registerExtension({
    name: "LLMClient.Sessions",
    nodeCreated(node) {
        if (!["LLMChat", "LLMAgent"].includes(node.comfyClass)) return;
        if (node._llmSessionWidgetInstalled) return;
        const memory = getWidget(node, "memory");
        const session = getWidget(node, "sessions");
        if (!memory || !session) return;
        node._llmSessionWidgetInstalled = true;
        const randomId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)),
                                          value => value.toString(16).padStart(2, "0")).join("");
        const valid = value => /^[0-9a-f]{32}:[0-9a-f]{32}$/.test(value || "");
        session.type = "hidden";
        session.computeSize = () => [0, -4];
        const ensureSession = () => {
            if (!valid(session.value)) session.value = randomId() + ":" + randomId();
            const owner = session.value.split(":")[0];
            const nodes = node.graph?._nodes || [];
            const index = nodes.indexOf(node);
            const duplicate = index >= 0 && nodes.slice(0, index).some(other =>
                ["LLMChat", "LLMAgent"].includes(other.comfyClass)
                && getWidget(other, "sessions")?.value?.startsWith(owner + ":"));
            if (duplicate) session.value = randomId() + ":" + randomId();
        };
        const newSession = "New Session";
        let entries = [];
        let names = new Map();
        let refreshVersion = 0;
        let menuVersion = 0;
        let closeMenu = () => {};
        const label = value => names.get(value) || newSession;
        const updateNames = () => {
            const counts = new Map();
            names = new Map();
            for (const item of entries) {
                if (!item.saved) continue;
                const stamp = new Date(item.created_at * 1000).toISOString();
                counts.set(stamp, (counts.get(stamp) || 0) + 1);
            }
            for (const item of entries) {
                if (!item.saved) continue;
                const stamp = new Date(item.created_at * 1000).toISOString();
                names.set(item.id, counts.get(stamp) > 1 ? `${stamp} | ${item.id.split(":")[1]}` : stamp);
            }
        };
        const setOptions = () => {
            updateNames();
            selector.options.values = [newSession, ...entries.filter(item => item.saved).map(item => label(item.id))];
            selector.value = label(session.value);
        };
        const localEntry = id => ({ id, saved: false, updated_at: Number.MAX_SAFE_INTEGER });
        const selector = node.addWidget("combo", "session_menu", "", value => {
            if (!memory.value) return;
            if (value === newSession) {
                session.value = session.value.split(":")[0] + ":" + randomId();
                entries = entries.filter(item => item.saved);
                entries.unshift(localEntry(session.value));
                setOptions();
            } else {
                const selected = entries.find(item => label(item.id) === value);
                if (!selected) {
                    selector.value = label(session.value);
                    return;
                }
                session.value = selected.id;
            }
            session.callback?.(session.value);
            selector.value = label(session.value);
            refresh();
            node.setDirtyCanvas(true, true);
        }, { values: [], serialize: false });
        selector.computeSize = () => [0, 20];
        selector.type = memory.value ? "combo" : "hidden";
        node.addCustomWidget({
            name: "session_layout_space",
            type: "custom",
            value: null,
            options: { serialize: false },
            computeSize: () => [0, memory.value ? -4 : 20],
            draw() {},
        });
        const refresh = async () => {
            if (!memory.value || !valid(session.value)) return;
            const selected = session.value;
            const version = ++refreshVersion;
            try {
                const response = await api.fetchApi("/llmclient/sessions", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ kind: node.comfyClass === "LLMChat" ? "chat" : "agent",
                                           session: selected }),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
                if (version !== refreshVersion || session.value !== selected) return;
                entries = (data.sessions || []).filter(item => valid(item.id)
                    && Number.isFinite(item.created_at) && item.created_at > 0 && item.created_at < 253402300800
                    && Number.isFinite(item.updated_at)).map(item => ({ ...item, saved: true }));
                if (!entries.some(item => item.id === selected)) {
                    entries.unshift(localEntry(selected));
                }
                entries.sort((a, b) => b.updated_at - a.updated_at || b.id.localeCompare(a.id));
                setOptions();
                node.setDirtyCanvas(true, true);
            } catch (error) {
                console.warn("Unable to load sessions:", error);
                if (version === refreshVersion && session.value === selected) {
                    if (!entries.some(item => item.id === selected)) {
                        entries = [localEntry(selected)];
                    }
                    setOptions();
                }
            }
        };
        const openMenu = async event => {
            if (!memory.value) return;
            const version = ++menuVersion;
            closeMenu();
            await refresh();
            if (version !== menuVersion || !memory.value || !node.graph) return;
            const menu = document.createElement("div");
            menu.className = "litecontextmenu llmclient-session-menu";
            Object.assign(menu.style, {
                position: "fixed", zIndex: "10000", minWidth: "300px",
                maxWidth: "calc(100vw - 16px)", maxHeight: "min(400px, calc(100vh - 16px))",
                overflowY: "auto", left: `${Math.max(8, Math.min(event.clientX, window.innerWidth - 310))}px`,
                top: `${Math.max(8, Math.min(event.clientY, window.innerHeight - 200))}px`,
                background: "var(--comfy-menu-bg, #242424)", color: "var(--fg-color, #eee)",
                border: "1px solid var(--border-color, #555)", borderRadius: "6px",
                boxShadow: "0 6px 20px #0008", padding: "4px",
            });
            menu.setAttribute("role", "menu");
            const close = () => {
                document.removeEventListener("pointerdown", outside, true);
                document.removeEventListener("keydown", keydown, true);
                menu.remove();
                if (closeMenu === close) closeMenu = () => {};
            };
            const outside = e => { if (!menu.contains(e.target)) close(); };
            const keydown = e => { if (e.key === "Escape") close(); };
            closeMenu = close;
            const filter = document.createElement("input");
            filter.type = "search";
            filter.placeholder = "Filter sessions";
            filter.setAttribute("aria-label", "Filter sessions");
            Object.assign(filter.style, { width: "100%", boxSizing: "border-box", marginBottom: "4px",
                                           background: "var(--comfy-input-bg, #222)", color: "inherit" });
            const filterRows = [];
            filter.oninput = () => {
                const query = filter.value.trim().toLowerCase();
                for (const item of filterRows) {
                    item.row.style.display = item.text.toLowerCase().includes(query) ? "flex" : "none";
                }
            };
            menu.append(filter);
            const addRow = (text, callback, entry) => {
                const row = document.createElement("div");
                row.className = "litemenu-entry";
                Object.assign(row.style, { display: "flex", alignItems: "center", gap: "8px" });
                row.onmouseenter = () => { row.style.background = "var(--comfy-input-bg, #333)"; };
                row.onmouseleave = () => { row.style.background = ""; };
                const choose = document.createElement("button");
                choose.type = "button";
                choose.textContent = text;
                Object.assign(choose.style, { flex: "1", textAlign: "left", background: "transparent",
                                               color: "inherit", border: "0", cursor: "pointer" });
                choose.onclick = () => { close(); callback(); };
                row.append(choose);
                if (entry?.saved) {
                    const remove = document.createElement("button");
                    remove.type = "button";
                    remove.textContent = "×";
                    remove.title = "Delete session";
                    remove.setAttribute("aria-label", `Delete session ${text}`);
                    Object.assign(remove.style, { background: "transparent", color: "inherit",
                                                   border: "0", cursor: "pointer", fontSize: "18px" });
                    remove.onclick = async e => {
                        e.stopPropagation();
                        if (!confirm(`Permanently delete session ${text}? This cannot be undone.`)) return;
                        remove.disabled = true;
                        try {
                            const response = await api.fetchApi("/llmclient/sessions/delete", {
                                method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ kind: node.comfyClass === "LLMChat" ? "chat" : "agent",
                                                       session: session.value, target: entry.id }),
                            });
                            const data = await response.json();
                            if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
                            close();
                            entries = entries.filter(item => item.id !== entry.id);
                            if (session.value === entry.id) selector.callback(newSession);
                            else { setOptions(); node.setDirtyCanvas(true, true); }
                        } catch (error) {
                            remove.disabled = false;
                            alert("Unable to delete session: " + error.message);
                        }
                    };
                    row.append(remove);
                }
                menu.append(row);
                if (entry) filterRows.push({ row, text });
            };
            addRow(newSession, () => selector.callback(newSession));
            for (const entry of entries.filter(item => item.saved)) {
                const name = label(entry.id);
                addRow(name, () => selector.callback(name), entry);
            }
            document.body.append(menu);
            document.addEventListener("pointerdown", outside, true);
            document.addEventListener("keydown", keydown, true);
            filter.focus();
        };
        selector.onPointerDown = pointer => {
            if (!memory.value) return false;
            pointer.onClick = () => openMenu(pointer.eDown);
            return true;
        };
        const update = () => {
            if (memory.value) {
                ensureSession();
                if (!entries.some(item => item.id === session.value)) {
                    entries = [localEntry(session.value)];
                }
                setOptions();
                refresh();
            } else {
                ++refreshVersion;
                ++menuVersion;
                closeMenu();
            }
            selector.type = memory.value ? "combo" : "hidden";
            node.setDirtyCanvas(true, true);
        };
        const previous = memory.callback;
        memory.callback = function (...args) {
            const result = previous?.apply(this, args);
            update();
            return result;
        };
        const configured = node.onConfigure;
        node.onConfigure = function (...args) {
            const result = configured?.apply(this, args);
            setTimeout(update, 0);
            return result;
        };
        const executionEvents = ["execution_success", "execution_error", "execution_interrupted"];
        const refreshAfterExecution = () => { if (memory.value) refresh(); };
        for (const event of executionEvents) api.addEventListener?.(event, refreshAfterExecution);
        const removed = node.onRemoved;
        node.onRemoved = function (...args) {
            ++refreshVersion;
            ++menuVersion;
            closeMenu();
            for (const event of executionEvents) api.removeEventListener?.(event, refreshAfterExecution);
            return removed?.apply(this, args);
        };
        setTimeout(update, 0);
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

app.registerExtension({
    name: "LLMClient.LoadDocuments",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMLoadDocuments") return;
        const input = document.createElement("input");
        input.type = "file";
        input.multiple = true;
        input.accept = ".pdf,.docx,.txt,.md,.json,.csv,.tsv,.xml,.html,.yaml,.yml,.log";
        const folderInput = document.createElement("input");
        folderInput.type = "file";
        folderInput.multiple = true;
        folderInput.setAttribute("webkitdirectory", "");
        const upload = async (picker) => {
            const supported = /\.(pdf|docx|txt|md|json|csv|tsv|xml|html|yaml|yml|log)$/i;
            const files = Array.from(picker.files || []).filter(file => supported.test(file.name));
            if (!files.length) return;
            if (files.length > 500 || files.some((file) => file.size > 20 * 1024 * 1024)) {
                alert("Choose at most 500 documents, each no larger than 20 MB.");
                return;
            }
            try {
                const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join("-");
                const folder = `llm_documents/${id}`;
                for (const file of files) {
                    const relative = file.webkitRelativePath || file.name;
                    const segments = relative.split("/");
                    if (segments.some(part => !part || part === "." || part === ".." || part.includes("\\"))) {
                        throw new Error("Invalid folder entry.");
                    }
                    const filename = segments.pop();
                    const form = new FormData();
                    form.append("image", file, filename);
                    form.append("type", "input");
                    form.append("subfolder", [folder, ...segments].join("/"));
                    const response = await api.fetchApi("/upload/image", { method: "POST", body: form });
                    if (!response.ok) throw new Error("HTTP " + response.status);
                }
                const widget = getWidget(node, "document_path");
                if (!widget) throw new Error("Document path input not found.");
                widget.value = `input:${folder}`;
                widget.callback?.(widget.value);
                node.setDirtyCanvas(true, true);
            } catch (error) {
                alert("Document upload failed: " + error.message);
            } finally {
                picker.value = "";
            }
        };
        input.onchange = () => upload(input);
        folderInput.onchange = () => upload(folderInput);
        node.addWidget("button", "upload_document", "Choose document to upload", () => input.click());
        node.addWidget("button", "upload_folder", "Choose folder to upload", () => folderInput.click());
    },
});

app.registerExtension({
    name: "LLMClient.RagConfig",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMRagConfig") return;
        const restoreSelection = async () => {
            const widget = getWidget(node, "embedding_model");
            if (!widget || typeof widget.value !== "string") return;
            const selected = widget.value;
            try {
                const response = await api.fetchApi("/llmclient/embedding-models");
                if (!response.ok) return;
                const data = await response.json();
                const current = data.aliases?.[selected];
                if (current && current !== selected && widget.value === selected) {
                    widget.value = current;
                    widget.callback?.(current);
                    node.setDirtyCanvas(true, true);
                }
            } catch (error) {
                console.warn("Unable to refresh embedding model selection:", error);
            }
        };
        const previousConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            const result = previousConfigure?.apply(this, args);
            setTimeout(restoreSelection, 0);
            return result;
        };
        setTimeout(restoreSelection, 0);
        const button = node.addWidget("button", "download_embedding", "Download selected embedding model", async () => {
            const value = getWidget(node, "embedding_model")?.value;
            const model = typeof value === "string" ? value : value?.embedding_model;
            if (!model || model === "Local path / local model" || model.startsWith("Installed: ")) {
                alert("Select a downloadable embedding model preset first.");
                return;
            }
            if (!confirm(`Download ${model} to ComfyUI's models/llm_embeddings folder?`)) return;
            const downloadId = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join("-");
            const onProgress = event => {
                const data = event.detail;
                if (data?.download_id !== downloadId) return;
                const mb = (data.downloaded / 1000000).toFixed(1);
                const total = data.total ? ` / ${(data.total / 1000000).toFixed(1)} MB` : " MB";
                setButtonLabel(button, `Downloading: ${mb}${total}`);
                node.setDirtyCanvas(true, true);
            };
            api.addEventListener("llmclient-download-progress", onProgress);
            setButtonLabel(button, "Starting download...");
            node.setDirtyCanvas(true, true);
            try {
                const response = await api.fetchApi("/llmclient/embedding-models/download", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ model, client_id: api.clientId, download_id: downloadId }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "HTTP " + response.status);
                alert("Embedding model downloaded to " + result.path + ". Refresh the browser to see its status in the menu.");
            } catch (error) {
                alert("Embedding model download failed: " + error.message);
            } finally {
                api.removeEventListener("llmclient-download-progress", onProgress);
                setButtonLabel(button, "Download selected embedding model");
                node.setDirtyCanvas(true, true);
            }
        });
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

function applyExtractLabels(node, text = getWidget(node, "patterns")?.value) {
    if (!node.outputs) {
        return;
    }
    const labels = parsePatternLabels(text);
    // Keep linked legacy slots intact; removing a linked output destroys its wires.
    let count = Math.min(labels.length, 12);
    node.outputs.forEach((output, i) => {
        if (output.links?.length) count = Math.max(count, i + 1);
    });
    while (node.outputs.length > count) node.removeOutput(node.outputs.length - 1);
    while (node.outputs.length < count) {
        node.addOutput(`field_${node.outputs.length + 1}`, "STRING");
    }
    node.outputs.forEach((output, i) => {
        output.name = labels[i] || `field_${i + 1}`;
        output.label = output.name;
    });
    if (node.size && node.computeSize) {
        const size = node.computeSize();
        node.setSize([Math.max(node.size[0], size[0]), size[1]]);
    }
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "LLMClient.FreeModels",
    nodeCreated(node) {
        if (node.comfyClass !== "LLMFreeComfyModels") return;
        const configure = node.configure.bind(node);
        node.configure = function (info) {
            const result = configure(info);
            const mode = getWidget(node, "mode");
            if (mode?.value === "free_vram_and_ram_experimental") mode.value = "free_vram_and_ram";
            return result;
        };
    },
});

app.registerExtension({
    name: "LLMClient.Vision",
    nodeCreated(node) {
        if (!["LLMChat", "LLMAgent"].includes(node.comfyClass)) return;
        const configure = node.configure.bind(node);
        node.configure = function (info) {
            const result = configure(info);
            const vision = getWidget(node, "vision");
            if (vision?.value === "yes") vision.value = "enabled";
            if (vision?.value === "no") vision.value = "disable";
            return result;
        };
    },
});

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
                applyExtractLabels(node, value);
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
