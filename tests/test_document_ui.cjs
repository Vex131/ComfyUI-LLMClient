const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("free models migrates the old RAM mode", () => {
    const extensions = [];
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api: {}, console });
    const mode = { name: "mode", value: "free_vram" };
    const node = {
        comfyClass: "LLMFreeComfyModels",
        widgets: [mode],
        configure(info) { mode.value = info.value; },
    };
    extensions.find(e => e.name === "LLMClient.FreeModels").nodeCreated(node);
    node.configure({ value: "free_vram_and_ram_experimental" });
    assert.equal(mode.value, "free_vram_and_ram");
    node.configure({ value: "free_vram" });
    assert.equal(mode.value, "free_vram");
});

test("folder upload and advertised reasoning controls", async () => {
    const extensions = [];
    const listeners = {};
    const pickers = [];
    const window = {
        addEventListener: (name, fn) => { listeners[name] = fn; },
        removeEventListener: name => { delete listeners[name]; },
        dispatchEvent: () => {},
    };
    const document = { createElement: () => {
        const input = { setAttribute(name) { this[name] = true; }, click() { this.clicked = true; } };
        pickers.push(input);
        return input;
    }};
    const api = { fetchApi: async () => ({ ok: true, json: async () => ({ modes: ["low", "medium", "high"] }) }) };
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, window, document, console, setTimeout });
    function makeNode(comfyClass, widgets) {
        return { comfyClass, widgets, setDirtyCanvas() {},
            addWidget(type, name, value, callback, options = {}) {
                const widget = { type, name, value, callback, options };
                this.widgets.push(widget);
                return widget;
            },
        };
    }
    const loader = makeNode("LLMLoadDocuments", [{ name: "document_path", value: "" }]);
    extensions.find(e => e.name === "LLMClient.LoadDocuments").nodeCreated(loader);
    loader.widgets.find(w => w.name === "upload_folder").callback();
    assert.equal(pickers[1].webkitdirectory, true);
    assert.equal(pickers[1].clicked, true);

    const provider = makeNode("LLMProvider", [
        { name: "base_url", value: "http://localhost" }, { name: "model", value: "m" },
    ]);
    const agent = makeNode("LLMAgent", [{ name: "reasoning_effort", value: "medium" }]);
    agent.inputs = [{ name: "model", link: 1 }];
    agent.graph = { links: { 1: { origin_id: 2 } }, getNodeById: () => provider };
    extensions.find(e => e.name === "LLMClient.ReasoningModes").nodeCreated(agent);
    await new Promise(resolve => setTimeout(resolve, 10));
    const selector = agent.widgets.find(w => w.name === "provider_reasoning_modes");
    assert.ok(selector);
    selector.callback("high");
    assert.equal(agent.widgets[0].value, "high");
});

test("existing downloaded-model selections migrate to the single menu entry", async () => {
    const extensions = [];
    const oldValue = "Installed: llm_embeddings/all-MiniLM-L6-v2";
    const newValue = "Small | MiniLM-L6-v2 | 23M params | ~91 MB | Downloaded";
    const api = { fetchApi: async () => ({
        ok: true, json: async () => ({ aliases: { [oldValue]: newValue } }),
    }) };
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, console, setTimeout });
    const model = { name: "embedding_model", value: oldValue };
    const node = { comfyClass: "LLMRagConfig", widgets: [model], setDirtyCanvas() {},
        addWidget(type, name, value, callback) {
            const widget = { type, name, value, callback };
            this.widgets.push(widget);
            return widget;
        },
    };
    extensions.find(e => e.name === "LLMClient.RagConfig").nodeCreated(node);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(model.value, newValue);
});

test("folder upload sends a basename and keeps the folder in subfolder", async () => {
    const extensions = [];
    const pickers = [];
    const submitted = [];
    class FormData {
        constructor() { this.fields = {}; }
        append(name, value, filename) { this.fields[name] = { value, filename }; }
    }
    const document = { createElement: () => {
        const input = { setAttribute() {}, click() {} };
        pickers.push(input);
        return input;
    }};
    const api = { fetchApi: async (path, request) => {
        submitted.push(request.body.fields);
        return { ok: true };
    }};
    const app = { registerExtension: extension => extensions.push(extension) };
    const crypto = { getRandomValues: arr => { arr.fill(1); return arr; } };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, document, crypto, FormData, console });
    const path = { name: "document_path", value: "" };
    const node = { comfyClass: "LLMLoadDocuments", widgets: [path], setDirtyCanvas() {},
        addWidget(type, name, value, callback) {
            const widget = { type, name, value, callback };
            this.widgets.push(widget);
            return widget;
        },
    };
    extensions.find(e => e.name === "LLMClient.LoadDocuments").nodeCreated(node);
    pickers[1].files = [{ name: "New folder/1.txt", webkitRelativePath: "New folder/1.txt", size: 2 }];
    pickers[1].onchange();
    await new Promise(setImmediate);
    assert.equal(submitted[0].image.filename, "1.txt");
    assert.equal(submitted[0].subfolder.value, "llm_documents/1-1-1-1/New folder");
    assert.equal(path.value, "input:llm_documents/1-1-1-1");
});

test("sessions are generated per node and the menu hides when memory is off", async () => {
    const extensions = [];
    let count = 0;
    const crypto = { getRandomValues: arr => { arr.fill(++count); return arr; } };
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    const api = { fetchApi: async () => ({ ok: true, json: async () => ({ sessions: [] }) }) };
    vm.runInNewContext(source, { app, api, crypto, console, setTimeout });
    const graph = { _nodes: [] };
    function node() {
        const instance = { comfyClass: "LLMChat", graph, setDirtyCanvas() {},
            widgets: [{ name: "memory", value: false }, { name: "sessions", value: "" }],
            addWidget(type, name, value, callback, options = {}) {
                const widget = { type, name, value, callback, options, computeSize: () => [100, 20] };
                this.widgets.push(widget);
                return widget;
            },
            addCustomWidget(widget) { this.widgets.push(widget); return widget; },
        };
        graph._nodes.push(instance);
        extensions.find(e => e.name === "LLMClient.Sessions").nodeCreated(instance);
        return instance;
    }
    const first = node();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(first.widgets.find(w => w.name === "session_menu").type, "hidden");
    first.widgets[0].value = true;
    first.widgets[0].callback();
    assert.match(first.widgets[1].value, /^[0-9a-f]{32}:[0-9a-f]{32}$/);
    const menu = first.widgets.find(w => w.name === "session_menu");
    const spacer = first.widgets.find(w => w.name === "session_layout_space");
    assert.equal(menu.type, "combo");
    assert.equal(menu.computeSize()[1], 20);
    assert.equal(spacer.computeSize()[1], -4);
    const second = node();
    second.widgets[1].value = first.widgets[1].value;
    second.widgets[0].value = true;
    second.widgets[0].callback();
    assert.notEqual(second.widgets[1].value.split(":")[0], first.widgets[1].value.split(":")[0]);
    first.widgets[0].value = false;
    first.widgets[0].callback();
    assert.equal(menu.type, "hidden");
    assert.equal(menu.computeSize()[1], 20);
    assert.equal(spacer.computeSize()[1], 20);
    extensions.find(e => e.name === "LLMClient.Sessions").nodeCreated(first);
    assert.equal(first.widgets.filter(w => w.name === "session_menu").length, 1);
});

test("new sessions remain unnamed until saved for chat and agent", async () => {
    const extensions = [];
    let count = 0;
    let saved = [];
    const crypto = { getRandomValues: arr => { arr.fill(++count); return arr; } };
    const listeners = new Map();
    const api = {
        fetchApi: async () => ({ ok: true, json: async () => ({ sessions: saved }) }),
        addEventListener: (event, fn) => listeners.set(event, fn),
        removeEventListener: (event, fn) => { if (listeners.get(event) === fn) listeners.delete(event); },
    };
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, crypto, console, setTimeout });
    for (const comfyClass of ["LLMChat", "LLMAgent"]) {
        saved = [];
        const node = { comfyClass, widgets: [{ name: "memory", value: true }, { name: "sessions", value: "" }],
            graph: { _nodes: [] }, setDirtyCanvas() {},
            addWidget(type, name, value, callback, options = {}) {
                const widget = { type, name, value, callback, options };
                this.widgets.push(widget);
                return widget;
            },
            addCustomWidget(widget) { this.widgets.push(widget); return widget; },
        };
        node.graph._nodes.push(node);
        extensions.find(e => e.name === "LLMClient.Sessions").nodeCreated(node);
        await new Promise(resolve => setTimeout(resolve, 10));
        const menu = node.widgets.find(w => w.name === "session_menu");
        const session = node.widgets.find(w => w.name === "sessions");
        assert.deepEqual(Array.from(menu.options.values), ["New Session"]);
        assert.equal(menu.value, "New Session");
        const original = session.value;
        menu.callback("New Session");
        await new Promise(resolve => setTimeout(resolve, 10));
        assert.notEqual(session.value, original);
        assert.deepEqual(Array.from(menu.options.values), ["New Session"]);
        saved = [{ id: session.value, created_at: 1700000000, updated_at: 200 }];
        listeners.get("execution_success")();
        await new Promise(resolve => setTimeout(resolve, 10));
        assert.equal(menu.value, "2023-11-14T22:13:20.000Z");
        assert.equal(session.value, saved[0].id);
        assert.deepEqual(Array.from(menu.options.values), ["New Session", menu.value]);
        node.onRemoved();
        assert.equal(listeners.size, 0);
    }
});

test("session menu lists and switches saved sessions", async () => {
    const extensions = [];
    const owner = "a".repeat(32), current = owner + ":" + "b".repeat(32);
    const prior = owner + ":" + "c".repeat(32);
    class Element {
        constructor() { this.children = []; this.value = ""; }
        append(...children) { this.children.push(...children); }
        showModal() {}
        close() {}
        remove() {}
    }
    const document = { body: new Element(), createElement: () => new Element() };
    const app = { registerExtension: extension => extensions.push(extension) };
    const api = { fetchApi: async () => ({ ok: true, json: async () => ({
        sessions: [
            { id: prior, created_at: 1700000001, updated_at: 100 },
            { id: current, created_at: 1700000000, updated_at: 200 },
        ],
    }) }) };
    const crypto = { getRandomValues: arr => { arr.fill(1); return arr; } };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, document, crypto, console, setTimeout });
    const memory = { name: "memory", value: true }, session = { name: "sessions", value: current };
    const node = { comfyClass: "LLMChat", widgets: [memory, session], graph: { _nodes: [] },
        setDirtyCanvas() {}, addWidget(type, name, value, callback, options = {}) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        },
        addCustomWidget(widget) { this.widgets.push(widget); return widget; },
    };
    node.graph._nodes.push(node);
    extensions.find(e => e.name === "LLMClient.Sessions").nodeCreated(node);
    await new Promise(resolve => setTimeout(resolve, 10));
    const select = node.widgets.find(w => w.name === "session_menu");
    assert.equal(select.type, "combo");
    node.onConfigure({});
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(session.value, current);
    assert.equal(node.widgets.filter(w => w.name === "session_menu").length, 1);
    assert.equal(select.options.values[0], "New Session");
    assert.equal(select.options.values[1], "2023-11-14T22:13:20.000Z");
    assert.equal(select.options.values[2], "2023-11-14T22:13:21.000Z");
    select.callback(select.options.values[2]);
    assert.equal(session.value, prior);
    select.callback("New Session");
    assert.equal(session.value.split(":")[0], owner);
    assert.notEqual(session.value, prior);
});

test("model config reorders controls and migrates old saved values", () => {
    const extensions = [];
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api: {}, console });
    const widgets = ["temperature", "top_p", "top_k", "max_tokens", "timeout_seconds", "max_context"]
        .map(name => ({ name, value: -1 }));
    const node = { comfyClass: "LLMModelConfig", widgets, properties: {} };
    extensions.find(e => e.name === "LLMClient.ModelConfigLayout").nodeCreated(node);
    assert.ok(widgets.indexOf(widgets.find(w => w.name === "max_context"))
              < widgets.indexOf(widgets.find(w => w.name === "timeout_seconds")));
    node.onConfigure({ widgets_values: [-1, -1, -1, -1, 900, 8192], properties: {} });
    assert.equal(widgets.find(w => w.name === "timeout_seconds").value, 900);
    assert.equal(widgets.find(w => w.name === "max_context").value, 8192);
    node.onConfigure({ widgets_values: [-1, -1, -1, -1, 180], properties: {} });
    assert.equal(widgets.find(w => w.name === "timeout_seconds").value, 180);
    assert.equal(widgets.find(w => w.name === "max_context").value, -1);
});

test("model config keeps saved timeout when moving it to the bottom", () => {
    const extensions = [];
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api: {}, console });
    const widgets = ["temperature", "top_p", "top_k", "timeout_seconds", "max_output_tokens",
                     "max_context", "max_tokens"].map(name => ({ name, value: -1 }));
    const node = { comfyClass: "LLMModelConfig", widgets, properties: {},
        onConfigure(info) { widgets.forEach((widget, index) => { widget.value = info.widgets_values[index]; }); } };
    extensions.find(e => e.name === "LLMClient.ModelConfigLayout").nodeCreated(node);
    assert.equal(widgets.at(-1).name, "timeout_seconds");
    assert.equal(widgets.find(w => w.name === "max_tokens").type, "hidden");
    node.onConfigure({ widgets_values: [-1, -1, -1, 8192, 900, 200, 123],
                       properties: { llmclient_model_config_layout: 2 } });
    assert.equal(widgets.find(w => w.name === "max_context").value, 8192);
    assert.equal(widgets.find(w => w.name === "timeout_seconds").value, 900);
    assert.equal(widgets.find(w => w.name === "max_output_tokens").value, 200);
    assert.equal(widgets.find(w => w.name === "max_tokens").value, 123);
    assert.equal(node.properties.llmclient_model_config_layout, 3);
});

test("new model config leaves output limit at -1", () => {
    const extensions = [];
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api: {}, console });
    const widgets = ["temperature", "top_p", "top_k", "timeout_seconds", "max_output_tokens",
                     "max_context", "max_tokens"].map(name => ({ name, value: name === "timeout_seconds" ? 300 : -1 }));
    const node = { comfyClass: "LLMModelConfig", widgets, properties: {} };
    extensions.find(e => e.name === "LLMClient.ModelConfigLayout").nodeCreated(node);
    assert.equal(widgets.find(w => w.name === "max_output_tokens").value, -1);
    assert.equal(widgets.find(w => w.name === "timeout_seconds").value, 300);
});

test("session menu confirms deletion and replaces only the active session", async () => {
    const extensions = [];
    const owner = "a".repeat(32), active = owner + ":" + "b".repeat(32);
    const other = owner + ":" + "c".repeat(32);
    let entries = [
        { id: active, created_at: 1700000000, updated_at: 20 },
        { id: other, created_at: 1699999999, updated_at: 10 },
    ];
    let confirmed = false;
    const deleted = [];
    class Element {
        constructor() { this.children = []; this.style = {}; }
        append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
        setAttribute() {}
        contains(target) { return target === this || this.children.some(child => child.contains(target)); }
        querySelector() { return this.children[0]?.children[0]; }
        focus() {}
        remove() {
            if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
        }
    }
    const document = { body: new Element(), createElement: () => new Element(),
        addEventListener() {}, removeEventListener() {} };
    const api = { fetchApi: async (path, request) => {
        if (path.endsWith("/delete")) {
            const target = JSON.parse(request.body).target;
            deleted.push(target);
            entries = entries.filter(item => item.id !== target);
            return { ok: true, json: async () => ({ ok: true }) };
        }
        return { ok: true, json: async () => ({ sessions: entries }) };
    }};
    const window = { innerWidth: 1000, innerHeight: 800 };
    const crypto = { getRandomValues: arr => { arr.fill(1); return arr; } };
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, document, window, crypto, console, setTimeout,
                                confirm: () => confirmed, alert: message => { throw Error(message); } });
    const memory = { name: "memory", value: true }, session = { name: "sessions", value: active };
    const node = { comfyClass: "LLMChat", widgets: [memory, session], graph: { _nodes: [] },
        setDirtyCanvas() {}, addWidget(type, name, value, callback, options = {}) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        },
        addCustomWidget(widget) { this.widgets.push(widget); return widget; },
    };
    node.graph._nodes.push(node);
    extensions.find(e => e.name === "LLMClient.Sessions").nodeCreated(node);
    const selector = node.widgets.find(w => w.name === "session_menu");
    const pointer = { eDown: { clientX: 50, clientY: 50 } };
    assert.equal(selector.onPointerDown(pointer), true);
    await pointer.onClick();
    let menu = document.body.children[0];
    assert.equal(menu.children[0].placeholder, "Filter sessions");
    assert.equal(menu.children[1].children[0].textContent, "New Session");
    assert.equal(menu.children[2].children[0].textContent, "2023-11-14T22:13:20.000Z");
    menu.children[0].value = "22:13:19";
    menu.children[0].oninput();
    assert.equal(menu.children[2].style.display, "none");
    assert.equal(menu.children[3].style.display, "flex");
    menu.children[0].value = "";
    menu.children[0].oninput();
    const event = { stopPropagation() {} };
    await menu.children[2].children[1].onclick(event);
    assert.equal(deleted.length, 0);
    confirmed = true;
    await menu.children[3].children[1].onclick(event);
    assert.equal(deleted[0], other);
    assert.equal(session.value, active);
    await pointer.onClick();
    menu = document.body.children[0];
    await menu.children[2].children[1].onclick(event);
    assert.equal(deleted[1], active);
    assert.notEqual(session.value, active);
    assert.equal(session.value.split(":")[0], owner);
});
