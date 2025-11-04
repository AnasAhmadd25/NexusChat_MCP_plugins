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

```

### 2. Enable Plugin Upload

In CMC `Advanced Configs`:

```json
{
  "general": {
    "allow_custom_plugins_upload": true,
    "allow_custom_plugins_execution": true
  }
}
```

### 3. Restart Copilot

- Go to Cloud Portal → Configuration
- Disable then re-enable `Enable Copilot`

## Usage

In the Copilot chat window:

```
/incorta_claude show me sales trends in a dashboard
```

The plugin will:
1. **Task 1**: Query data and generate analysis (with optional HTML dashboard code according to the context) -> save response with extracting the html
2. **Task 2**: recive and render the HTML dashboard (if present), as we can set for it a specified rendrer in frontend.
3. **Task 3**: Display combined results (final)


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
- ✅ added context management



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

