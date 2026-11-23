"""Local document ingestion and semantic retrieval for the agent."""
import math
import hashlib
import threading
from collections import OrderedDict
from pathlib import Path

TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".tsv", ".xml", ".html", ".yaml", ".yml", ".log"}
SUPPORTED = TEXT_EXTENSIONS | {".pdf", ".docx"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 500
MAX_CHUNKS = 10000
PRESET_MODELS = {
    "Small | MiniLM-L6-v2 | 23M params | ~91 MB": "sentence-transformers/all-MiniLM-L6-v2",
    "Medium | MPNet-base-v2 | 109M params | ~438 MB": "sentence-transformers/all-mpnet-base-v2",
    "Multilingual | MiniLM-L12-v2 | 118M params | ~485 MB": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
}
DIRECT_MODEL = "Local path / local model"
_index_cache = OrderedDict()
_index_lock = threading.Lock()
MAX_CACHED_INDEXES = 2
DOWNLOADED_PREFIX = "[Downloaded] "
DOWNLOADED_SUFFIX = " | Downloaded"


def model_root():
    import folder_paths
    return Path(folder_paths.models_dir) / "llm_embeddings"


def model_options():
    root = model_root()
    installed = []
    preset_names = {repo.split("/")[-1] for repo in PRESET_MODELS.values()}
    for directory in (root, root.parent / "embeddings"):
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if (path.is_dir() and (path / "config.json").is_file()
                        and not (directory == root and path.name in preset_names)):
                    installed.append("Installed: {}/{}".format(directory.name, path.name))
    presets = []
    for label, repo in PRESET_MODELS.items():
        model = root / repo.split("/")[-1]
        complete = all((model / name).is_file() for name in ("config.json", "modules.json",
                                                              "model.safetensors", "tokenizer_config.json"))
        presets.append(label + (DOWNLOADED_SUFFIX if complete else ""))
    return [DIRECT_MODEL] + presets + installed


def model_aliases():
    options = set(model_options())
    aliases = {}
    for label, repo in PRESET_MODELS.items():
        current = label + DOWNLOADED_SUFFIX if label + DOWNLOADED_SUFFIX in options else label
        aliases[label] = current
        aliases[DOWNLOADED_PREFIX + label] = current
        aliases[label + DOWNLOADED_SUFFIX] = current
        aliases["Installed: llm_embeddings/" + repo.split("/")[-1]] = current
    return aliases


def resolve_model(selection, direct_path):
    if selection == DIRECT_MODEL:
        return direct_path
    if selection.startswith(DOWNLOADED_PREFIX):
        selection = selection[len(DOWNLOADED_PREFIX):]
    if selection.endswith(DOWNLOADED_SUFFIX):
        selection = selection[:-len(DOWNLOADED_SUFFIX)]
    if selection in PRESET_MODELS:
        return str(model_root() / PRESET_MODELS[selection].split("/")[-1])
    if selection.startswith("Installed: "):
        name = selection[len("Installed: "):]
        parts = name.split("/")
        if len(parts) != 2 or parts[0] not in ("llm_embeddings", "embeddings") or not parts[1] \
                or Path(parts[1]).name != parts[1] or parts[1] in (".", ".."):
            raise ValueError("Invalid installed embedding model name.")
        return str(model_root().parent / parts[0] / parts[1])
    raise ValueError("Unknown embedding model selection.")


def resolve_document_path(path):
    if not path.startswith("input:"):
        return path
    import folder_paths
    root = Path(folder_paths.get_input_directory()).resolve()
    relative = path[len("input:"):]
    resolved = (root / relative).resolve()
    if not relative or not resolved.is_relative_to(root):
        raise ValueError("Invalid uploaded document path.")
    return str(resolved)


def document_fingerprint(path):
    try:
        files = _documents(resolve_document_path(path))
        result = []
        for file in files:
            if file.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Document exceeds 20 MB: {}".format(file))
            digest = hashlib.sha256()
            with file.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            result.append((str(file.resolve()), digest.hexdigest()))
        return tuple(result)
    except (OSError, ValueError):
        return float("nan")


def model_fingerprint(model_path):
    if not model_path:
        return float("nan")
    model = Path(model_path).expanduser().resolve()
    if not model.is_dir():
        return float("nan")
    try:
        files = tuple((str(p.relative_to(model)), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in sorted(model.rglob("*")) if p.is_file())
        return (str(model), files)
    except OSError:
        return float("nan")


def index_fingerprint(document_path, model_path):
    documents = document_fingerprint(document_path) if document_path else float("nan")
    model = model_fingerprint(model_path)
    if isinstance(documents, float) or isinstance(model, float):
        return float("nan")
    return (documents, model)


def load_index(document_path, model_path):
    key = index_fingerprint(document_path, model_path)
    if isinstance(key, float):
        return DocumentIndex(resolve_document_path(document_path), model_path)
    with _index_lock:
        if key in _index_cache:
            _index_cache.move_to_end(key)
            return _index_cache[key]
        index = DocumentIndex(resolve_document_path(document_path), model_path)
        if index_fingerprint(document_path, model_path) != key:
            raise ValueError("Documents or embedding model changed during indexing. Run again.")
        _index_cache[key] = index
        while len(_index_cache) > MAX_CACHED_INDEXES:
            _index_cache.popitem(last=False)
        return index


def _documents(path):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Provide a document file or directory path.")
    path = Path(path).expanduser()
    if not path.exists():
        raise ValueError("Document path does not exist on the ComfyUI server: {}. "
                         "For files on another computer, use Choose folder to upload.".format(path))
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError("Unsupported document type: {}".format(path.suffix))
        return [path]
    if not path.is_dir():
        raise ValueError("Document path must be a file or directory.")
    files = sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in SUPPORTED)
    if len(files) > MAX_FILES:
        raise ValueError("Document directory exceeds the {} file limit.".format(MAX_FILES))
    if not files:
        raise ValueError("No supported documents found in {}.".format(path))
    return files


def _pages(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Document exceeds 20 MB: {}".format(path))
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF is not supported: {}".format(path))
        return [("page {}".format(i), page.extract_text() or "")
                for i, page in enumerate(reader.pages, 1)]
    if suffix == ".docx":
        from docx import Document
        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            paragraphs.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        return [("", "\n".join(paragraphs))]
    return [("", path.read_text(encoding="utf-8-sig"))]


def _chunks(text, size=800, overlap=150):
    text = text.strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            yield part
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


class DocumentIndex:
    def __init__(self, path, model_path, model=None):
        if not model_path and model is None:
            raise ValueError("Provide a local Sentence Transformers model path.")
        self.chunks = []
        for file in _documents(path):
            for location, text in _pages(file):
                for chunk in _chunks(text):
                    self.chunks.append((str(file), location, chunk))
                    if len(self.chunks) > MAX_CHUNKS:
                        raise ValueError("Document collection exceeds the {} chunk limit.".format(MAX_CHUNKS))
        if not self.chunks:
            raise ValueError("No extractable text found. Scanned PDFs require OCR before indexing.")
        if model is None:
            if not Path(model_path).expanduser().is_dir():
                raise ValueError("Embedding model must be a local directory: {}".format(model_path))
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(str(Path(model_path).expanduser()), device="cpu",
                                        local_files_only=True, trust_remote_code=False)
        self.model = model
        self.vectors = model.encode([c[2] for c in self.chunks], normalize_embeddings=True,
                                    show_progress_bar=False)

    def search(self, query, top_k=5):
        if not isinstance(query, str) or not query.strip():
            return "Provide a non-empty search query."
        vector = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        scored = []
        for index, embedding in enumerate(self.vectors):
            score = sum(float(a) * float(b) for a, b in zip(vector, embedding))
            if math.isfinite(score):
                scored.append((score, index))
        scored.sort(reverse=True)
        results = []
        for score, index in scored[:top_k]:
            source, location, text = self.chunks[index]
            results.append("[{}{} | similarity {:.3f}]\n{}".format(
                source, ", " + location if location else "", score, text))
        return "\n\n".join(results) or "No matching document passages."
