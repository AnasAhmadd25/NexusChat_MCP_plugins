from opencopilot.controller.operators_executor import OperatorExecutor
from opencopilot.utils import logger
import asyncio
from typing import Optional
from dotenv import load_dotenv
import subprocess
import sys
import os
import json

load_dotenv()

# Global conversation history storage (keyed by session_id)
_conversation_history = {}


class IncortaMCPExecutor(OperatorExecutor):
    def execute(self, task_context, operator):
        """
        Main execution method called by Incorta Copilot.
        Installs dependencies and runs the async MCP client.
        """
        install_or_upgrade_packages()

        try:
            result = asyncio.run(self._async_execute(task_context, operator))
        except Exception as e:
            logger.error(f"Error in execute: {str(e)}")
            result = f"Error: {str(e)}"
        
        self.finalize(task_context, result)
    
    async def _async_execute(self, task_context, operator):
        """
        Async execution that initializes MCP client and runs the agent.
        
        This operator can be called multiple times with different operator_renderer values:
        - First call (Task 1): operator_renderer = "MarkdownRenderer" → Generate full analysis
        - Second call (Task 2): operator_renderer = "HtmlRenderer" → Extract/generate HTML dashboard
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langgraph.prebuilt import create_react_agent

        try:
            # Check which task we are (based on task_context.task_index)
            current_task_id = task_context.tasks[task_context.task_index]["id"]
            logger.info(f"Executing task {current_task_id}")
            
            # Task 2 should check if Task 1 has already done the work
            if current_task_id == 2:
                logger.info("=== TASK 2 EXECUTION START ===")
                # This is the HtmlRenderer task - check if we can reuse Task 1's result
                first_task = next((t for t in task_context.tasks if t["id"] == 1 and t.get("status") == "DONE"), None)
                
                if first_task:
                    logger.info(f"Task 1 found with status: {first_task.get('status')}")
                    logger.info(f"Task 1 has result: {first_task.get('result') is not None}")
                else:
                    logger.warning("Task 1 not found or not DONE yet!")
                
                if first_task and first_task.get("result"):
                    logger.info("Task 2: Reusing Task 1's result to extract HTML")
                    # Extract HTML from Task 1's markdown result
                    markdown_result = first_task["result"]
                    logger.info(f"Task 1 result length: {len(str(markdown_result))} characters")
                    html_dashboard = self.extract_html_dashboard(markdown_result)
                    
                    if html_dashboard:
                        logger.info(f"HTML dashboard extracted, length: {len(html_dashboard)} characters")
                        # Return HtmlOutput format as specified in docs Section 5
                        # For final task result (not intermediate output)
                        return {
                            "content": html_dashboard,
                            "aspect_ratio": "16/9",
                            "title": "Interactive Dashboard",
                            "html_type": "dashboard"
                            # Note: NO "type" field for final task results
                        }
                    else:
                        # no html, then return none 
                        logger.info("Task 2: No HTML dashboard found in Task 1's result")
                        return None
                else:
                    logger.error("Task 2: Cannot access Task 1 result - falling through to re-execute!")
                    # Don't re-execute, return none
                    return None
            
            # operator or task  1: do the main work (query MCP, generate response, even the report)
            logger.info(f"Task {current_task_id}: Starting MCP agent execution")
            
            # Update progress for user visibility
            task_context.update_short_description_and_progress("Initializing MCP connection...")
            
            # Get user session information
            user_info = self._get_user_session_info(task_context)
            logger.info(f"User session info: {user_info}")
            
            # Get linked_schema from operator metadata
            linked_schema = self._get_linked_schema(task_context, operator)
            logger.info(f"Using linked_schema: {linked_schema}")
            
            # Get uploaded files if any
            uploaded_files = task_context.get_selected_uploaded_file_paths()
            if uploaded_files:
                logger.info(f"Found {len(uploaded_files)} uploaded file(s): {uploaded_files}")
                # Read file contents and add to context
                file_contents = self._read_uploaded_files(uploaded_files)
            else:
                logger.info("No uploaded files found")
                file_contents = None
            
            # --- private mcp ------
            headers = {}
            if user_info.get("incorta_url"):
                headers["env-url"] = user_info["incorta_url"]
            if user_info.get("tenant"):
                headers["tenant"] = user_info["tenant"]
            if user_info.get("username"):
                headers["user"] = user_info["username"]  
            if user_info.get("password"):
                headers["password"] = user_info["password"]  
            
            logger.info(f"MCP headers configured: {list(headers.keys())}")
            
            # Initialize MCP client with user's credentials
            client = MultiServerMCPClient(
                {
                    "Incorta MCP Server": {
                        "url": user_info.get("mcp_server_url", "https://alone-recall-wait-era.trycloudflare.com/mcp/"),
                        "headers": headers,
                        "transport": "streamable_http",
                    }
                }
            )

            # Get tools from MCP server
            task_context.update_short_description_and_progress("Retrieving available tools...")
            tools = await client.get_tools()
            logger.info(f"Retrieved {len(tools)} tools from MCP server")

            # Initialize LLM
            task_context.update_short_description_and_progress("Initializing AI agent...")
            # API key should be provided via environment variable ANTHROPIC_API_KEY
            # or through CMC configuration
            llm = self.create_llm(
                "anthropic",
                model="claude-sonnet-4-20250514"  # Updated to Sonnet 4.5 (latest stable)
            )

            # Create agent with tools
            agent = create_react_agent(model=llm, tools=tools)

            # Get or create conversation history for this session
            session_id = user_info.get('session_id', 'default')
            if session_id not in _conversation_history:
                _conversation_history[session_id] = []
                logger.info(f"Created new conversation history for session {session_id}")
            else:
                logger.info(f"Reusing conversation history for session {session_id} ({len(_conversation_history[session_id])} messages)")
            
            # Use the session's conversation history
            messages = _conversation_history[session_id].copy()
            
            # only add system prompt if it's not already in history
            # This prevents cache invalidation and enables proper prompt caching
            has_system_prompt = any(msg.get("role") == "system" for msg in messages)
            
            if not has_system_prompt:
                logger.info("Adding system prompt (first message in conversation)")
                # Add main system prompt with dashboard generation instructions
                # This will be cached using prompt caching to improve rate limits
                system_prompt = """You are an expert Incorta data analyst assistant. Your primary role is to help users interact with their Incorta data using the Model Context Protocol (MCP) tools.

**YOUR CORE CAPABILITIES:**

You have access to powerful Incorta MCP tools that allow you to:
- **Query Data**: Use the query tool to fetch data, generate insights, and perform analysis on user data
- **Explore Schemas**: List and examine Business Schemas and Physical Schemas
- **Analyze Metrics**: Calculate aggregations, trends, and key performance indicators
- **Answer Questions**: Provide insights and explanations about the user's data
- **Navigate Data Models**: Understand relationships between tables and schemas

**IMPORTANT SCHEMA SELECTION RULES:**
1. ALWAYS prefer Business Schemas over Physical Schemas when available
2. Business Schemas contain pre-modeled, business-ready data with optimized relationships
3. Only use Physical Schemas if no suitable Business Schema exists
4. When listing schemas, prioritize and recommend Business Schemas first

**VISUALIZATION CAPABILITY (OPTIONAL FEATURE):**

As an additional feature, you can present your analysis results as interactive HTML dashboards when appropriate.

**When to use visualizations:**

**VISUALIZATION CAPABILITY (OPTIONAL FEATURE):**

As an additional feature, you can present your analysis results as interactive HTML dashboards when appropriate.

**When to use visualizations:**
- User explicitly requests a "dashboard", "chart", "graph", or "visualization"
- You're presenting multi-metric analysis that would be clearer visually
- You're showing trends over time or distributions
- You're comparing multiple categories or ranking top/bottom items
- The data insights would significantly benefit from visual representation and text results are long or complex

**When to use standard text/markdown responses:**
- Simple data lookups or single value queries
- Listing schemas, tables, or column definitions
- Explanatory answers about data models
- Error messages or clarifications
- Small result sets that are clear as tables
- User asks for specific information without needing visualization

**TYPICAL WORKFLOW:**

1. **Understand the Request**: Analyze what the user is asking for
2. **Use MCP Tools**: Query the data using the appropriate Incorta MCP tools if needed
3. **Analyze Results**: Process and understand the data returned
4. **Choose Output Format**:
   - For most queries → Provide clear markdown response with tables/text
   - For complex analysis → Consider if visualization adds value
5. **Present Results**: Return either markdown text or HTML dashboard based on the context

**HTML DASHBOARD STRUCTURE:**

When creating dashboards, wrap your HTML in a ```html code block with this exact structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Incorta Dashboard</title>
    <link href="%PUBLIC_URL%/static/css/tailwind.min.css" rel="stylesheet">
    <script src="%PUBLIC_URL%/static/js/chart.js"></script>
    <style>
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f5f5f5;
            margin: 0;
            padding: 20px;
        }
        .dashboard-header {
            background: linear-gradient(135deg, #3e45a2 0%, #001529 100%);
            color: white;
            padding: 24px;
            border-radius: 8px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .dashboard-title {
            font-size: 28px;
            font-weight: 700;
            margin: 0;
        }
        .dashboard-subtitle {
            font-size: 14px;
            opacity: 0.9;
            margin-top: 8px;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }
        .metric-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
            border-left: 4px solid #3e45a2;
        }
        .metric-label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        .metric-value {
            font-size: 32px;
            font-weight: 700;
            color: #001529;
        }
        .chart-container {
            background: white;
            padding: 24px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
            margin-bottom: 20px;
        }
        .chart-title {
            font-size: 18px;
            font-weight: 600;
            color: #001529;
            margin-bottom: 16px;
        }
        canvas {
            max-height: 400px;
        }
    </style>
</head>
<body>
    <div class="dashboard-header">
        <h1 class="dashboard-title">📊 Your Dashboard Title</h1>
        <p class="dashboard-subtitle">Powered by Incorta Analytics</p>
    </div>
    
    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-label">Metric Name</div>
            <div class="metric-value">$1.2M</div>
        </div>
        <!-- Add more metric cards as needed -->
    </div>
    
    <div class="chart-container">
        <h2 class="chart-title">Chart Title</h2>
        <canvas id="myChart"></canvas>
    </div>
    
    <script>
        // Chart.js configuration
        const ctx = document.getElementById('myChart').getContext('2d');
        const chart = new Chart(ctx, {
            type: 'bar', // bar, line, pie, doughnut, radar, polarArea
            data: {
                labels: ['Label 1', 'Label 2', 'Label 3'],
                datasets: [{
                    label: 'Dataset Label',
                    data: [12, 19, 3],
                    backgroundColor: [
                        'rgba(62, 69, 162, 0.7)',  // Incorta primary
                        'rgba(0, 21, 41, 0.7)',     // Incorta secondary
                        'rgba(62, 69, 162, 0.4)'    // Incorta light
                    ],
                    borderColor: [
                        'rgba(62, 69, 162, 1)',
                        'rgba(0, 21, 41, 1)',
                        'rgba(62, 69, 162, 1)'
                    ],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    title: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    </script>
</body>
</html>
```

**INCORTA BRANDING COLORS:**
- Primary: #3e45a2
- Secondary: #001529
- Background: #f5f5f5
- Text: #333333
- Light accent: #7e85c4

**CHART.JS CHART TYPES:**
- `bar` - For comparisons across categories
- `line` - For trends over time
- `pie` or `doughnut` - For proportions/percentages
- `radar` - For multi-dimensional comparisons
- `polarArea` - For cyclical data
- `scatter` - For correlation analysis
- Multiple charts - Create multiple canvas elements for different visualizations

**CRITICAL REQUIREMENTS:**
- Use `%PUBLIC_URL%/static/css/tailwind.min.css` for Tailwind
- Use `%PUBLIC_URL%/static/js/chart.js` for Chart.js
- DO NOT use external CDNs (unpkg, jsdelivr, etc.)
- Embed all custom CSS in `<style>` tags
- Embed all custom JavaScript in `<script>` tags
- Must include complete HTML document structure (<html>, <head>, <body>)
- Wrap the entire HTML in a ```html code block

**REMEMBER:**
Your primary job is to help users access and understand their Incorta data using MCP tools. Dashboards are a helpful presentation tool, but most interactions will be standard Q&A about data, running queries, and providing insights in text/markdown format."""
                
                # Add system message with cache control for prompt caching
                #  reduces rate limit usage significantly for repeated prompts
                messages.append({
                    "role": "system", 
                    "content": [
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"}  # Cache this system prompt
                        }
                    ]
                })
            else:
                logger.info(f"System prompt already in history, reusing cached version (enables cache hits)")
            
            # If files are uploaded, add their content to the initial context
            if file_contents:
                file_context = f"""You have access to the following uploaded files:

{file_contents}

Use this information along with the Incorta MCP tools to answer the user's question."""
                messages.append({"role": "system", "content": file_context})
            
            task_context.update_short_description_and_progress("Analyzing your query...")
            final_response = await self.handle_user_message(
                agent,
                messages,
                task_context.user_query_str,
                task_context
            )

            # Save updated conversation history back to global storage
            _conversation_history[session_id] = messages
            logger.info(f"Saved conversation history ({len(messages)} messages)")

            task_context.update_short_description_and_progress("Analysis complete")
            
            # For now, return simple markdown text
            # The framework will automatically handle it
            # TODO: Add HTML dashboard support later with proper framework pattern
            return final_response
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Error in _async_execute: {str(e)}\n{error_details}")
            return f"Error initializing MCP client: {str(e)}\n\nDetails:\n{error_details}"
    
    def _get_linked_schema(self, task_context, operator):
        """
        Retrieves the linked_schema from the operator metadata.
        
        Args:
            task_context: The task context object
            operator: The operator name
            
        Returns:
            The linked schema name or None if not found
        """
        try:
            from service_data.user_operators.operators import ext_op_functions
            
            op_name = task_context.tasks[task_context.task_index]["operator"]
            linked_schema = ext_op_functions.get(op_name, {}).get("linked_schema", None)
            
            return linked_schema
        except ImportError:
            logger.warning("Could not import ext_op_functions, linked_schema not available")
            return None
        except Exception as e:
            logger.error(f"Error retrieving linked_schema: {str(e)}")
            return None
    
    def _get_user_session_info(self, task_context):
        """
        Extract user session information from task_context.
        
        Args:
            task_context: The task context object
            
        Returns:
            Dictionary containing user session info
        """
        user_info = {}
        
        try:
            # Get user session ID
            if hasattr(task_context, 'session_id'):
                user_info['session_id'] = task_context.session_id
            
            # Get user information
            if hasattr(task_context, 'user_context'):
                user_context = task_context.user_context
                logger.info(f"DEBUG: user_context keys available: {list(user_context.keys()) if isinstance(user_context, dict) else 'not a dict'}")
                logger.info(f"DEBUG: user_context content: {user_context}")
                user_info['username'] = user_context.get('user', 'admin')
                user_info['tenant'] = user_context.get('tenant', 'demo')
                # Try to get password from user_context
                user_info['password'] = user_context.get('password', 'Incorta_1234%')
            else:
                # Set defaults if user_context doesn't exist
                user_info['username'] = 'admin'
                user_info['tenant'] = 'demo'
                user_info['password'] = 'Incorta_1234%'
            
            # Get Incorta server URL from context
            if hasattr(task_context, 'server_context'):
                server_context = task_context.server_context
                logger.info(f"DEBUG: server_context keys available: {list(server_context.keys()) if isinstance(server_context, dict) else 'not a dict'}")
                logger.info(f"DEBUG: server_context content: {server_context}")
                user_info['incorta_url'] = server_context.get('server_url', 'https://se-prod-demo.cloud4.incorta.com/incorta')
            else:
                user_info['incorta_url'] = 'https://se-prod-demo.cloud4.incorta.com/incorta'
            
            # Get MCP server URL from executor_args or use default
            executor_args = self._get_executor_args(task_context, None)
            user_info['mcp_server_url'] = executor_args.get('mcp_server_url', 'https://alone-recall-wait-era.trycloudflare.com/mcp/')
            
            logger.info(f"Extracted user session info: session_id={user_info.get('session_id')}, username={user_info.get('username')}, tenant={user_info.get('tenant')}, has_password={user_info.get('password') is not None}")
            
        except Exception as e:
            logger.error(f"Error extracting user session info: {str(e)}")
            # Return defaults if extraction fails
            user_info = {
                'username': 'admin',
                'tenant': 'demo',
                'password': 'Incorta_1234%',
                'incorta_url': 'https://se-prod-demo.cloud4.incorta.com/incorta',
                'mcp_server_url': 'https://alone-recall-wait-era.trycloudflare.com/mcp/'
            }
        
        return user_info
    
    def _get_executor_args(self, task_context, operator):
        """
        Get executor_args from the operator configuration.
        
        Args:
            task_context: The task context object
            operator: The operator name
            
        Returns:
            Dictionary of executor arguments
        """
        try:
            from service_data.user_operators.operators import ext_op_functions
            
            op_name = task_context.tasks[task_context.task_index]["operator"]
            executor_args = ext_op_functions.get(op_name, {}).get("executor_args", {})
            
            return executor_args
        except Exception as e:
            logger.error(f"Error retrieving executor_args: {str(e)}")
            return {}
    
    def _read_uploaded_files(self, file_paths):
        """
        Read contents of uploaded files.
        
        Args:
            file_paths: List of file paths to read
            
        Returns:
            String containing formatted file contents
        """
        MAX_FILE_SIZE = 1_000_000  # 1 MB limit per file
        MAX_PREVIEW_LINES = 100  # Show first 100 lines for large files
        
        contents = []
        for file_path in file_paths:
            try:
                # Get filename from path
                filename = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                
                # Try to read as text
                with open(file_path, 'r', encoding='utf-8') as f:
                    if file_size > MAX_FILE_SIZE:
                        # File is too large, read only preview
                        lines = []
                        for i, line in enumerate(f):
                            if i >= MAX_PREVIEW_LINES:
                                break
                            lines.append(line)
                        content = ''.join(lines)
                        contents.append(
                            f"File: {filename} ({file_size:,} bytes - showing first {MAX_PREVIEW_LINES} lines)\n"
                            f"{'='*50}\n{content}\n"
                            f"[... file continues for {file_size:,} total bytes ...]\n"
                            f"{'='*50}\n"
                        )
                        logger.warning(f"File {filename} is large ({file_size:,} bytes), showing preview only")
                    else:
                        # File is small enough, read all
                        content = f.read()
                        contents.append(f"File: {filename}\n{'='*50}\n{content}\n{'='*50}\n")
                        logger.info(f"Successfully read file: {filename} ({len(content)} characters)")
                        
            except UnicodeDecodeError:
                # If it's a binary file, just note that
                logger.warning(f"File {filename} appears to be binary, skipping content reading")
                contents.append(f"File: {filename}\n{'='*50}\n[Binary file - content not displayed]\n{'='*50}\n")
            except Exception as e:
                logger.error(f"Error reading file {file_path}: {str(e)}")
                contents.append(f"File: {filename}\n{'='*50}\n[Error reading file: {str(e)}]\n{'='*50}\n")
        
        return "\n".join(contents) if contents else None
       
    def create_llm(self, provider: str, **kwargs):
        """Factory function to create different LLM instances"""
        from langchain_openai import ChatOpenAI
        from langchain_anthropic import ChatAnthropic
        from langchain_community.chat_models import ChatOllama
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_mistralai import ChatMistralAI
        
        if provider.lower() == "openai":
            return ChatOpenAI(
                base_url="https://api.together.xyz/v1",
                model=kwargs.get("model", "gpt-4"),
                api_key=kwargs.get("api_key"),
                temperature=kwargs.get("temperature", 0.7)
            )
        
        elif provider.lower() == "anthropic":
            return ChatAnthropic(
                model=kwargs.get("model", "claude-sonnet-4-20250514"),
                api_key=kwargs.get("api_key"),
                temperature=kwargs.get("temperature", 0.7),
                # Enable prompt caching support
                default_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
                max_tokens=kwargs.get("max_tokens", 8192)  # increased as generating html repotrts need a lot of tokens
            )
        
        elif provider.lower() == "ollama":
            return ChatOllama(
                model=kwargs.get("model", "llama2"),
                base_url=kwargs.get("base_url", "http://localhost:11434"),
                temperature=kwargs.get("temperature", 0.7)
            )
        
        elif provider.lower() == "google":
            return ChatGoogleGenerativeAI(
                model=kwargs.get("model", "gemini-2.5-flash-lite-preview-06-17"),
                google_api_key=kwargs.get("api_key"),
                temperature=kwargs.get("temperature", 0.7)
            )
        
        elif provider.lower() == "mistral":
            return ChatMistralAI(
                model=kwargs.get("model", "mistral-medium"),
                mistral_api_key=kwargs.get("api_key"),
                temperature=kwargs.get("temperature", 0.7)
            )
        
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        
    async def handle_user_message(self, agent, messages, user_input, task_context):
        """Handle a single user message and stream the response"""
        messages.append({"role": "user", "content": user_input})
        
        response_content = ""
        async for chunk in agent.astream({"messages": messages}):
            if "agent" in chunk:
                # Handle string content
                if isinstance(chunk["agent"]["messages"][0].content, str):
                    content = chunk["agent"]["messages"][0].content
                    new_content = content[len(response_content):]
                    response_content = content
                    
                # Handle list content (tool calls)
                if isinstance(chunk["agent"]["messages"][0].content, list):
                    for part in chunk["agent"]["messages"][0].content:
                        if "text" in part:
                            content = part["text"]
                            new_content = content[len(response_content):]
                            task_context.append_intermediate_output(f"{new_content}")
                            response_content = content
                        if "name" in part and part["name"]:
                            tool_name = part["name"]
                            task_context.append_intermediate_output(f"\n**Calling tool:** `{tool_name}`")
                            # Format tool args nicely
                            tool_args = part.get('input', {})
                            if tool_args:
                                args_preview = self._format_tool_args(tool_args)
                                task_context.append_intermediate_output(f"**Arguments:** {args_preview}")

            if "tools" in chunk:
                try:
                    tool_result = chunk["tools"]["messages"][0].content
                    # Format tool result nicely
                    formatted_result = self._format_tool_result(tool_result)
                    task_context.append_intermediate_output(f"\n**Tool Result:**\n{formatted_result}\n")
                except Exception as e:
                    task_context.append_intermediate_output(f"\n**Error:** {str(e)}\n")
                    logger.error(f"Tool result processing error: {e}")
        
        if response_content:
            messages.append({"role": "assistant", "content": response_content})
        
        # Return raw markdown - FinalResultText expects markdown, not HTML
        return response_content
    
    def _markdown_to_html(self, markdown_text):
        """Convert Markdown text to HTML"""
        try:
            import markdown
            html = markdown.markdown(markdown_text, extensions=['extra', 'nl2br', 'sane_lists'])
            return html
        except ImportError:
            logger.warning("markdown package not available, returning plain text")
            return markdown_text
        except Exception as e:
            logger.error(f"Error converting markdown to HTML: {e}")
            return markdown_text
    
    def _format_tool_args(self, args):
        """Format tool arguments for display"""
        if isinstance(args, dict):
            if len(str(args)) > 100:
                # Truncate long args
                return f"`{list(args.keys())}`"
            return f"`{args}`"
        return f"`{args}`"
    
    def _format_tool_result(self, result):
        """Format tool results for better readability"""
        try:
            # Try to parse as JSON for pretty formatting
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    # Format as code block
                    return f"```json\n{json.dumps(parsed, indent=2)}\n```"
                except:
                    # Not JSON, check if it's long text
                    if len(result) > 500:
                        # Truncate very long results
                        return f"```\n{result[:500]}...\n[Result truncated - {len(result)} total characters]\n```"
                    return f"```\n{result}\n```"
            else:
                # Already an object, format it
                return f"```json\n{json.dumps(result, indent=2)}\n```"
        except Exception as e:
            logger.error(f"Error formatting result: {e}")
            return str(result)
    
    def extract_html_dashboard(self, text: str) -> str:
        """
        Extract HTML dashboard code from markdown code blocks.
        Returns complete HTML document string or empty string if not found.
        
        This looks for HTML code wrapped in ```html blocks in the LLM response.
        If found, it validates that it's a complete HTML document (has <html>, <head>, <body>).
        """
        import re
        
        # Pattern to match ```html code blocks
        html_pattern = r'```html\s*\n(.*?)\n```'
        matches = re.findall(html_pattern, text, re.DOTALL | re.IGNORECASE)
        
        if not matches:
            logger.info("No HTML code blocks found in response")
            return ""
        
        # Take the first HTML block found
        html_content = matches[0].strip()
        logger.info(f"Found HTML block with {len(html_content)} characters")
        
        # Check if it ends properly (has closing tags)
        has_closing_html = '</html>' in html_content.lower()
        has_closing_body = '</body>' in html_content.lower()
        has_closing_script = '</script>' in html_content.lower()
        
        if not has_closing_html or not has_closing_body:
            logger.warning(f"HTML appears truncated! has_closing_html={has_closing_html}, has_closing_body={has_closing_body}, has_closing_script={has_closing_script}")
            logger.warning(f"Last 200 characters: {html_content[-200:]}")
            return ""
        
        # Validate it's a complete HTML document
        # Must have <html>, <head>, and <body> tags to be considered complete
        if not all(tag in html_content.lower() for tag in ['<html', '<head', '<body']):
            logger.info("HTML block found but not a complete document, skipping")
            return ""
        
        logger.info("Complete HTML dashboard found and validated")
        return html_content
    
    def remove_html_blocks(self, text: str) -> str:
        """
        here we will remove the html code from the markdown text, to not show it in the response of operator 1... but save it to pass to operator 2
        """
        import re
        
    
        html_pattern = r'```html\s*\n.*?\n```'
        # Remove all HTML code blocks
        cleaned_text = re.sub(html_pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
        cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
        
        return cleaned_text.strip()


def get_installed_version(package_name: str) -> Optional[str]:
    """Return the installed version of a package, or None if not installed."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split("Version:")[1].strip()
        return None
    except Exception:
        return None


def install_or_upgrade_packages():
    """Install or upgrade required packages for the MCP plugin."""
    packages = [
        "mcp",
        "anthropic",
        "langchain-core==0.3.75",
        "langchain==0.3.27",
        "langchain-anthropic==0.3.19",
        "langchain-mistralai==0.2.11",
        "langchain-openai==0.3.32",
        "langchain-community==0.3.29",
        "langchain-google-genai==2.1.10",
        "langgraph==0.2.60",
        "langchain-mcp-adapters==0.1.9",
        "markdown",
    ]

    for package in packages:
        # Split out version if pinned
        if "==" in package:
            package_name, required_version = package.split("==")
        else:
            package_name, required_version = package, None

        installed_version = get_installed_version(package_name)

        # Determine whether to skip or upgrade
        if installed_version:
            if required_version and installed_version == required_version:
                logger.info(
                    f"{package_name} already at required version ({installed_version}), skipping..."
                )
                continue
            else:
                logger.info(
                    f"{package_name} is installed (version {installed_version}), upgrading to {required_version or 'latest'}..."
                )
        else:
            logger.info(f"{package_name} not installed, installing {required_version or 'latest'}...")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", package],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode == 0:
                logger.info(f"✅ Successfully installed/upgraded {package}")
            else:
                logger.error(f"❌ Failed to install/upgrade {package}")
                logger.error(f"--- stdout ---\n{result.stdout}")
                logger.error(f"--- stderr ---\n{result.stderr}")

        except Exception as e:
            logger.error(f"💥 Exception while installing/upgrading {package}: {e}")

    # Optional: Show final versionsa
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        logger.info("--- Installed package versions ---\n" + result.stdout)
    except Exception as e:
        logger.error(f"Failed to list installed packages: {e}")
