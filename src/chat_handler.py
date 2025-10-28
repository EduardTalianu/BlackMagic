#!/usr/bin/env python3
"""
chat_handler.py - DEFINITIVE FIX for output display

This version ensures ALL command outputs are displayed, not just the first one.
"""

import json
import requests
from typing import List, Dict, Tuple, Optional, Callable
from abc import ABC, abstractmethod


# ============== TOOL INTERFACE ==============

class Tool(ABC):
    """Base tool interface"""
    
    @abstractmethod
    def handle(self, name: str, args: dict) -> str:
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        pass


# ============== TOOL IMPLEMENTATIONS ==============

class TerminalTool(Tool):
    """Execute terminal commands"""
    
    def __init__(self, flow_id: int, mcp_client):
        self.flow_id = flow_id
        self.mcp_client = mcp_client
    
    def handle(self, name: str, args: dict) -> str:
        command = args.get("command", "")
        message = args.get("message", "")
        
        print(f"\n[TERMINAL] Executing: {command}")
        if message:
            print(f"[TERMINAL] Context: {message}")
        
        result = self.mcp_client.execute_single_command(command)
        print(f"[TERMINAL] Output length: {len(result)} chars")
        
        # Show first 200 chars for debugging
        if result:
            preview = result[:200] + "..." if len(result) > 200 else result
            print(f"[TERMINAL] Preview: {preview}")
        
        return result
    
    def is_available(self) -> bool:
        return self.mcp_client is not None


class MockTool(Tool):
    """Mock tool for testing"""
    
    def handle(self, name: str, args: dict) -> str:
        return f"[MOCK] Would execute {name} with: {json.dumps(args, indent=2)}"
    
    def is_available(self) -> bool:
        return True


# ============== TOOL EXECUTOR ==============

class ToolExecutor:
    """Tool registry and executor"""
    
    def __init__(self, flow_id: int, mcp_client):
        self.flow_id = flow_id
        self.mcp_client = mcp_client
    
    def get_tool(self, func_name: str) -> Tool:
        if self.flow_id == 0:
            return MockTool()
        
        if func_name == "terminal":
            return TerminalTool(self.flow_id, self.mcp_client)
        
        raise ValueError(f"Unknown tool: {func_name}")
    
    def execute_function(self, func_name: str, args: dict) -> str:
        tool = self.get_tool(func_name)
        
        if not tool.is_available():
            raise RuntimeError(f"Tool {func_name} not available")
        
        return tool.handle(func_name, args)


# ============== TOOL DEFINITIONS ==============

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Execute a bash command in the Kali Linux container",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute"
                    },
                    "message": {
                        "type": "string",
                        "description": "Context or reason for this command"
                    }
                },
                "required": ["command", "message"]
            }
        }
    }
]


# ============== STREAMING CHAT HANDLER ==============

class StreamingChatHandler:
    """Function calling chat handler with FIXED output display"""
    
    def __init__(self, llm_url: str, llm_key: str, model: str, 
                 mcp_client, flow_id: int = 1):
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.flow_id = flow_id
        self.max_iterations = 20
        
        self.executor = ToolExecutor(flow_id, mcp_client)
        self.conversation_history: List[Dict] = []
        self.max_history_messages = 100
    
    def handle_message(
        self,
        conversation_display: List[Dict],
        new_user_message: str,
        stream_callback: Optional[Callable] = None
    ) -> Tuple[bool, str, List[Dict]]:
        """
        Handle message with PROPER output display for ALL commands.
        """
        messages = [
            {"role": "system", "content": self._get_system_prompt()}
        ]
        messages.extend(conversation_display)
        messages.append({"role": "user", "content": new_user_message})
        
        # CRITICAL: Store command/output pairs separately
        command_outputs = []  # List of (command, output) tuples
        
        try:
            for iteration in range(self.max_iterations):
                print(f"\n{'='*60}")
                print(f"[ITERATION {iteration + 1}/{self.max_iterations}]")
                print(f"{'='*60}")
                
                # Call LLM with tools
                response = self._call_llm_with_tools(messages)
                
                # Stream LLM thinking if callback provided
                if stream_callback and response.get("content"):
                    stream_callback("llm", response.get("content"))
                
                # Handle tool calls
                if response.get("tool_calls"):
                    # Add assistant message with tool calls
                    messages.append({
                        "role": "assistant",
                        "content": response.get("content"),
                        "tool_calls": response["tool_calls"]
                    })
                    
                    # Execute EACH tool call
                    for tool_call in response["tool_calls"]:
                        func_name = tool_call["function"]["name"]
                        func_args = json.loads(tool_call["function"]["arguments"])
                        tool_id = tool_call["id"]
                        
                        cmd = func_args.get("command", "")
                        
                        print(f"\n[TOOL CALL] Executing: {cmd}")
                        
                        # Stream command
                        if stream_callback:
                            stream_callback("command", cmd)
                        
                        # Execute and get result
                        result = self.executor.execute_function(func_name, func_args)
                        
                        print(f"[TOOL RESULT] Got {len(result)} chars of output")
                        
                        # CRITICAL: Stream output IMMEDIATELY
                        if stream_callback:
                            stream_callback("output", result)
                        
                        # Add tool response to messages (for LLM)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": func_name,
                            "content": result
                        })
                        
                        # CRITICAL: Store this command/output pair
                        command_outputs.append((cmd, result))
                        
                        print(f"[STORED] Command #{len(command_outputs)}: {cmd[:50]}...")
                
                else:
                    # No tool calls - check if done
                    content = response.get("content", "")
                    messages.append({
                        "role": "assistant",
                        "content": content
                    })
                    
                    if content.startswith("DONE:"):
                        summary = content[5:].strip()
                        
                        # Build COMPLETE response with ALL command outputs
                        final_response = self._build_final_response(command_outputs, f"✅ {summary}")
                        
                        if stream_callback:
                            stream_callback("complete", summary)
                        
                        print(f"\n[FINAL] Returning response with {len(command_outputs)} command outputs")
                        return True, final_response, messages[1:]
                    
                    elif content.startswith("IMPOSSIBLE:"):
                        reason = content[11:].strip()
                        
                        final_response = self._build_final_response(command_outputs, f"❌ {reason}")
                        
                        if stream_callback:
                            stream_callback("error", reason)
                        
                        return False, final_response, messages[1:]
                    
                    else:
                        # LLM sent a message - add to outputs
                        if content:
                            command_outputs.append(("MESSAGE", content))
                            if stream_callback:
                                stream_callback("message", content)
            
            # Hit max iterations
            final_response = self._build_final_response(command_outputs, "⚠️ Reached iteration limit")
            
            if stream_callback:
                stream_callback("warning", "Reached iteration limit")
            
            return False, final_response, messages[1:]
        
        except Exception as e:
            import traceback
            print(f"\n[ERROR] Exception in handle_message:")
            traceback.print_exc()
            
            error_msg = f"❌ Error: {str(e)}"
            final_response = self._build_final_response(command_outputs, error_msg)
            
            if stream_callback:
                stream_callback("error", str(e))
            
            return False, final_response, messages[1:]
    
    def _build_final_response(self, command_outputs: List[Tuple[str, str]], final_msg: str) -> str:
        """
        Build final response with ALL command outputs properly formatted.
        
        This is the CRITICAL function that was missing!
        """
        parts = []
        
        print(f"\n[BUILD_RESPONSE] Building response with {len(command_outputs)} items")
        
        for i, (cmd, output) in enumerate(command_outputs, 1):
            print(f"[BUILD_RESPONSE] Item {i}: cmd='{cmd[:50]}...', output={len(output)} chars")
            
            if cmd == "MESSAGE":
                # It's a message from LLM
                parts.append(f"💬 {output}")
            else:
                # It's a command and its output
                # Format: $ command\noutput\n
                if output:
                    parts.append(f"$ {cmd}\n{output}")
                else:
                    parts.append(f"$ {cmd}\n(no output)")
        
        # Join all parts with double newlines for readability
        full_response = "\n\n".join(parts)
        
        # Add final message
        if full_response:
            full_response += f"\n\n{final_msg}"
        else:
            full_response = final_msg
        
        print(f"[BUILD_RESPONSE] Final response: {len(full_response)} chars total")
        return full_response
    
    def _call_llm_with_tools(self, messages: List[Dict]) -> Dict:
        """Call LLM with function calling"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    self.llm_url,
                    headers={
                        "Authorization": f"Bearer {self.llm_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": TOOL_DEFINITIONS,
                        "tool_choice": "auto",
                        "temperature": 0
                    },
                    timeout=60
                )
                response.raise_for_status()
                
                result = response.json()
                message = result["choices"][0]["message"]
                
                return {
                    "content": message.get("content"),
                    "tool_calls": message.get("tool_calls", [])
                }
            
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    print(f"[WARNING] LLM call failed (attempt {attempt + 1}), retrying...")
                    continue
                raise RuntimeError(f"LLM API call failed: {e}")
    
    def _get_system_prompt(self) -> str:
        return """You are a penetration testing assistant with access to a Kali Linux container.

AVAILABLE TOOLS:
- terminal: Execute bash commands in the container

WORKFLOW:
1. Use the terminal tool to execute commands
2. Analyze the output from each command
3. Execute more commands as needed
4. When task is complete, respond with: "DONE: summary"
5. If task is impossible, respond with: "IMPOSSIBLE: reason"

IMPORTANT:
- Execute commands ONE AT A TIME
- Wait for output before deciding next command
- You will see the full output of each command
- Analyze outputs before continuing

EXAMPLES:

User: "scan 192.168.1.1"
You: [call terminal(command="nmap -sV 192.168.1.1", message="Port scan")]
[you see full nmap output]
You: "DONE: Scan completed. Found 3 open ports: 22 (SSH), 80 (HTTP), 443 (HTTPS)"

User: "is nmap still running?"
You: [call terminal(command="ps aux | grep nmap", message="Check nmap")]
[you see process list]
You: "DONE: Yes, nmap is running with PID 1234"

User: "analyze example.com for vulnerabilities"
You: [call terminal(command="curl -I https://example.com", message="Check headers")]
[you see headers]
You: [call terminal(command="nikto -h https://example.com", message="Vulnerability scan")]
[you see nikto output]
You: "DONE: Found 4 vulnerabilities: Missing CSP, Cookie without Secure flag, Old server version, Directory listing"

Always use the terminal tool."""


# ============== BACKWARDS COMPATIBLE WRAPPER ==============

class ChatHandler(StreamingChatHandler):
    """Backwards compatible wrapper"""
    
    def __init__(self, llm_url: str, llm_key: str, model: str, mcp_client):
        super().__init__(llm_url, llm_key, model, mcp_client, flow_id=1)
    
    def execute_simple(
        self,
        user_request: str,
        stream_callback: Optional[Callable] = None
    ) -> Tuple[bool, str]:
        """Execute request (backwards compatible)"""
        success, response, full_messages = self.handle_message(
            self.conversation_history,
            user_request,
            stream_callback
        )
        
        # Update conversation history
        self.conversation_history = full_messages
        
        # Trim if too long
        if len(self.conversation_history) > self.max_history_messages:
            self.conversation_history = \
                self.conversation_history[-self.max_history_messages:]
        
        return success, response
    
    def reset_conversation(self):
        """Clear conversation history"""
        self.conversation_history = []
        print("[ChatHandler] Conversation history cleared")