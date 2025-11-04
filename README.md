# Incorta MCP Plugin

**Version:** 1.6.1

An Incorta Copilot plugin that integrates the Model Context Protocol (MCP) to enable AI-powered data analysis with interactive HTML dashboards.

## Features

- **MCP Integration**: Connects to Incorta MCP Server for data querying and analysis
- **Smart Prompt Caching**: Implements Anthropic's best practices for 90% cost savings
- **Session-Based Chat**: Maintains conversation history across multiple interactions
- **HTML Dashboards**: Generates interactive visualizations using Chart.js and Tailwind CSS
- **Dual-Task Architecture**: Separates markdown analysis from HTML rendering

## Installation

### Upload Plugin to Incorta

```bash
./sdk/upload.sh incorta_mcp_plugin
```

**Important**: After uploading, restart Copilot with `/restart` in the chat window.

## Configuration

### 1. CMC Plugin Configuration

Add to `Cluster Configurations > IncortaCopilot > Plugins Configs`:

```json
{
  "IncortaMCPOperator": {
    "enabled": true,
    "clients": ["MATERIALIZED_VIEW", "BUSINESS_NOTEBOOK", "DASHBOARDS", "DASHBOARDS_V2"],
    "operator_name": "Incorta MCP Operator",
    "description": "Insert the MCP Capabilities to copilot using Incorta MCP",
    "operator_tag": "/incorta_claude",
    "operator_tag_description": "Use Incorta MCP to answer the user query",
    "operator_predefined_tasks": [
      {
        "id": 1,
        "operator": "IncortaMCPOperator",
        "operator_renderer": "MarkdownRenderer",
        "short_description": "Analyzing data..",
        "status": "TODO",
        "depends_on_output_of": [],
        "result": ""
      },
      {
        "id": 2,
        "operator": "IncortaMCPOperator",
        "operator_renderer": "HtmlRenderer",
        "short_description": "Processing results",
        "status": "TODO",
        "depends_on_output_of": [1],
        "result": ""
      },
      {
        "id": 3,
        "operator": "FinalResultText",
        "short_description": "Final Results and Insights",
        "status": "DONE",
        "depends_on_output_of": [1, 2],
        "result": "@task2 @task1"
      }
    ],
    "plugin_name": "incorta_mcp",
    "executor_args": {}
  }
}
```

**Important**: After updating configuration, restart Analytics service.

### 2. API Key Configuration

Set the Anthropic API key via environment variable:

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

Or configure it in CMC `executor_args`:

```json
{
  "IncortaMCPOperator": {
    "executor_args": {
      "anthropic_api_key": "your-api-key-here"
    }
  }
}
```

### 3. Enable Plugin Upload

In CMC `Advanced Configs`:

```json
{
  "general": {
    "allow_custom_plugins_upload": true,
    "allow_custom_plugins_execution": true
  }
}
```

### 4. Restart Copilot

- Go to Cloud Portal → Configuration
- Disable then re-enable `Enable Copilot`

## Usage

In the Copilot chat window:

```
/incorta_claude show me sales trends in a dashboard
```

The plugin will:
1. **Task 1**: Query data and generate analysis (with optional HTML dashboard code)
2. **Task 2**: Extract and render the HTML dashboard (if present)
3. **Task 3**: Display combined results

## Smart Caching

The plugin implements Anthropic's prompt caching for:
- **90% cost reduction** on cached system prompts
- **10x effective rate limit** throughput
- **Faster response times** with cache hits

### How It Works:

```python
# First request in session:
→ Writes system prompt to cache (1.25x cost)

# Subsequent requests (within 5 min):
→ Reads from cache (0.1x cost = 90% savings!)
→ Doesn't count towards rate limits!
```

## HTML Dashboard Features

- **Chart.js Integration**: Bar, line, pie, doughnut, radar charts
- **Tailwind CSS Styling**: Responsive, modern design
- **Incorta Branding**: Official color scheme and styling
- **Complete Validation**: Checks for proper HTML document structure
- **Truncation Detection**: Warns when dashboards are incomplete

## Architecture

### File Structure

```
incorta_mcp_plugin/
├── __init__.py           # Package initialization
├── manifest.json         # Plugin metadata and version
├── plugin.py            # Main executor class
└── README.md            # This file
```

### Key Components

1. **IncortaMCPExecutor**: Main execution class
   - Manages MCP client connection
   - Handles LangGraph agent interactions
   - Implements smart caching logic

2. **Session Management**: 
   - Global conversation history per session
   - Prevents cache invalidation
   - Enables multi-turn conversations

3. **HTML Extraction**:
   - Regex-based extraction from markdown
   - Document completeness validation
   - Truncation detection and logging

## Requirements

Python packages (auto-installed):
- `mcp`
- `anthropic`
- `langchain-core==0.3.75`
- `langchain==0.3.27`
- `langchain-anthropic==0.3.19`
- `langchain-mcp-adapters==0.1.9`
- `langgraph==0.2.60`
- `markdown`

## Authentication

Default credentials (can be overridden by context):

```python
{
  "incorta_url": "https://se-prod-demo.cloud4.incorta.com/incorta",
  "tenant": "demo",
  "user": "admin",
  "password": "Incorta_1234%"
}
```

## Changelog

### Version 1.6.1
- ✅ Fixed prompt caching (system prompt only added once)
- ✅ Increased max_tokens to 8192 for complete dashboards
- ✅ Enhanced HTML validation with truncation detection
- ✅ Updated to production demo credentials

### Version 1.6.0
- Initial release with MCP integration
- Session-based chat history
- HTML dashboard generation

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with `./sdk/upload.sh incorta_mcp_plugin`
5. Submit a pull request

## License

MIT License

## Authors

- Anas Ahmed (@AnasAhmadd25)

## Links

- [Incorta Documentation](https://docs.incorta.com)
- [Anthropic Prompt Caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching)
- [Model Context Protocol](https://modelcontextprotocol.io)

## Troubleshooting

### Plugin Not Appearing

Check logs for:
```
@log
```

Should show:
```json
{
  "Available Plugins": [
    {
      "plugin_name": "incorta_mcp",
      "version": "1.6.1"
    }
  ]
}
```

### HTML Dashboard Truncated

Check logs for:
```
"HTML appears truncated!"
```

Solution: Increase `max_tokens` or simplify dashboard.

### Cache Not Working

Check logs for:
```
"System prompt already in history, reusing cached version"
```

If not appearing, verify session management is working.

## Support

For issues or questions:
- Create an issue in this repository
- Contact the Incorta support team
