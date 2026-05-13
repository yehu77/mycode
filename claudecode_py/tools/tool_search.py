from __future__ import annotations

from .base import BaseTool


class ToolSearchTool(BaseTool):
    name = "tool_search"
    description = "Search deferred tools and activate the tool you want to use."
    read_only = True
    concurrency_safe = True
    search_terms = ("search", "deferred", "tool selection", "activate tool")
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": 'Search query. Use "select:<tool_name>" to activate a specific deferred tool.',
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matches to return.",
            },
        },
        "required": ["query"],
    }

    def execute(self, tool_input: dict, ctx):
        query = str(tool_input["query"]).strip()
        max_results = int(tool_input.get("max_results", 5))
        return ctx.session.search_deferred_tools(query, max_results=max_results)

