"""MCP Llms-txt server for docs."""

import os
from urllib.parse import urlparse

import httpx
from markdownify import markdownify
from fastmcp import FastMCP
from typing_extensions import NotRequired, TypedDict

from mcpdoc.utils import save_config_file


class DocSource(TypedDict):
    """A source of documentation for a library or a package."""

    name: NotRequired[str]
    """Name of the documentation source (optional)."""

    llms_txt: str
    """URL to the llms.txt file or documentation source."""

    description: NotRequired[str]
    """Description of the documentation source (optional)."""


def extract_domain(url: str) -> str:
    """Extract domain from URL.

    Args:
        url: Full URL

    Returns:
        Domain with scheme and trailing slash (e.g., https://example.com/)
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _is_http_or_https(url: str) -> bool:
    """Check if the URL is an HTTP or HTTPS URL."""
    return url.startswith(("http:", "https:"))



def _normalize_path(path: str) -> str:
    """Accept paths in file:/// or relative format and map to absolute paths."""
    return (
        os.path.abspath(path[7:])
        if path.startswith("file://")
        else os.path.abspath(path)
    )


def create_server(
    doc_sources: list[DocSource],
    *,
    follow_redirects: bool = False,
    timeout: float = 10,
    settings: dict | None = None,
    allowed_domains: list[str] | None = None,
    max_tool_name_length: int = 60,
    json_config_path: str,
) -> FastMCP:
    """Create the server and generate documentation retrieval tools.

    Args:
        doc_sources: List of documentation sources to make available
        follow_redirects: Whether to follow HTTP redirects when fetching docs
        timeout: HTTP request timeout in seconds
        settings: Additional settings to pass to FastMCP
        allowed_domains: Additional domains to allow fetching from.
            Use ['*'] to allow all domains
            The domain hosting the llms.txt file is always appended to the list
            of allowed domains.
        max_tool_name_length: Maximum length for tool names (default 60). Use 0 for no limit.
        json_config_path: Path to the JSON config file used to load doc sources.

    Returns:
        A FastMCP server instance configured with documentation tools
    """
    print("Creating server with settings:", settings)
    settings = settings or {"tools_changed": True}
    server = FastMCP(
        name="llms-txt",
        instructions=(
            "Use the tools to fetch docs for a given library or package."
        ),
        **settings,
    )
    httpx_client = httpx.AsyncClient(follow_redirects=follow_redirects, timeout=timeout)

    tool_names = set()
    doc_sources_registry: list[DocSource] = []

    def make_tool_from_doc_source(doc_source: DocSource):
        url = doc_source["llms_txt"]
        is_remote_source = _is_http_or_https(url)

        # Let's verify that all local sources exist
        if not is_remote_source:
            path = doc_source["llms_txt"]
            abs_path = _normalize_path(path)
            if not os.path.exists(abs_path):
                raise FileNotFoundError(f"Local file not found: {abs_path}")

        def make_tool_name(name: str) -> str:
            """Normalize and create a unique tool name for a doc source, with length restriction."""
            base = f"fetch_docs_{name.replace(' ', '_').replace('.', '_').replace('/', '_')}"
            if max_tool_name_length and max_tool_name_length > 0:
                return base[:max_tool_name_length]
            return base


        url_or_path = doc_source["llms_txt"]
        if _is_http_or_https(url_or_path):
            name = doc_source.get("name", extract_domain(url_or_path))
            tool_name = make_tool_name(name)
            description = f"Fetch and return documentation content for: {name}"
            is_url = True
            fixed_path = url_or_path
        else:
            path = _normalize_path(url_or_path)
            name = doc_source.get("name", path)
            tool_name = make_tool_name(name)
            description = f"Fetch and return documentation content for {name}"
            is_url = False
            fixed_path = path

        if tool_name in tool_names:
            raise ValueError(f"Duplicate tool name detected: {tool_name}. Please ensure all doc sources have unique names.")
        tool_names.add(tool_name)

        def make_tool(fixed_path, is_url):
            if is_url:
                async def tool_fn():
                    # Check allowed domains
                    if "*" not in allowed_domains and not any(fixed_path.startswith(domain) for domain in allowed_domains):
                        return (
                            "Error: URL not allowed. Must start with one of the following domains: "
                            + ", ".join(allowed_domains)
                        )
                    try:
                        response = await httpx_client.get(fixed_path, timeout=timeout)
                        response.raise_for_status()
                        return markdownify(response.text)
                    except (httpx.HTTPStatusError, httpx.RequestError) as e:
                        return f"Encountered an HTTP error: {str(e)}"
                return tool_fn
            else:
                def tool_fn():
                    abs_path = fixed_path
                    # if abs_path not in allowed_local_files:
                    #     return (
                    #         f"Local file not allowed: {abs_path}. Allowed files: {allowed_local_files}"
                    #     )
                    try:
                        with open(abs_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        return markdownify(content)
                    except Exception as e:
                        return f"Error reading local file: {str(e)}"
                return tool_fn

        tool_fn = make_tool(fixed_path, is_url)
        server.tool(name=tool_name, description=description)(tool_fn)
        doc_sources_registry.append(doc_source)

    for entry in doc_sources:
        make_tool_from_doc_source(entry)

    def add_doc_source(name: str, url: str, description: str | None = None) -> str:
        doc_source = {"name": name, "llms_txt": url}
        if description:
            doc_source["description"] = description
        make_tool_from_doc_source(doc_source)
        save_config_file(json_config_path, doc_sources_registry)
        # Should automatically emit a notification that the tools list has changed
        return f"Added doc source: {name}"

    server.tool(name="add_doc_source", description="Add a new doc source by name and url")(add_doc_source)

    def remove_doc_source(name: str) -> str:
        # if name starts with fetch_docs_, remove it
        if name.startswith("fetch_docs_"):
            name = name[len("fetch_docs_"):]
        nonlocal doc_sources_registry
        doc_sources_registry = [entry for entry in doc_sources_registry if entry["name"] != name]
        server.remove_tool(f"fetch_docs_{name}")
        save_config_file(json_config_path, doc_sources_registry)
        return f"Removed doc source: {name}"
    
    server.tool(name="remove_doc_source", description="""Remove a doc source by name. \
                You can find a list of doc sources in the list_doc_sources tool, or based on the names of the tools beginning 'fetch_docs_'""")(remove_doc_source)

    def list_doc_sources() -> list[DocSource]:
        return doc_sources_registry
    server.tool(name="list_doc_sources", description="List all doc sources")(list_doc_sources)

    return server
