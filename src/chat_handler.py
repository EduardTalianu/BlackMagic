#!/usr/bin/env python3
"""
chat_handler.py - Direct chat execution with conversational intelligence
"""
import json
import requests
from typing import Tuple
from .mcp_agent import MCPAgent


# Kept for backwards compatibility (no longer used)
class ChatComplexity:
    """Deprecated - kept for import compatibility"""
    def __init__(self, is_simple: bool, reasoning: str, suggested_approach: str = ""):
        self.is_simple = is_simple
        self.reasoning = reasoning
        self.suggested_approach = suggested_approach


class ChatHandler:
    """
    Handles direct chat-style execution.
    Distinguishes between conversation and commands.
    """
    
    def __init__(self, llm_url: str, llm_key: str, model: str, mcp_client: MCPAgent):
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.mcp_client = mcp_client
        self.stream_callback = None  # For real-time updates
    
    def execute_simple(self, user_request: str, stream_callback=None) -> Tuple[bool, str]:
        """
        Execute request with intelligent routing.
        Conversations get direct responses, tasks get command execution.
        
        Args:
            user_request: The user's message
            stream_callback: Optional callback for real-time updates (for SSE)
        """
        self.stream_callback = stream_callback
        
        # First, classify the request
        request_type = self._classify_request(user_request)
        
        if request_type == "conversation":
            # Handle as pure conversation - no commands
            return self._handle_conversation(user_request)
        else:
            # Handle as command execution
            return self._handle_execution(user_request)
    
    def _classify_request(self, user_request: str) -> str:
        """
        Classify if request needs command execution or is just conversation.
        Returns: "conversation" or "execution"
        """
        system_prompt = """You classify user requests for a penetration testing system.

Return ONLY one word: "conversation" or "execution"

CONVERSATION: Greetings, questions about the system, general chat, help requests
Examples: "hello", "what can you do?", "how does this work?", "thanks"

EXECUTION: Actual pentesting tasks requiring bash commands
Examples: "scan port 80", "enumerate subdomains", "check SSL cert"
"""
        
        try:
            response = self._call_llm(system_prompt, f'Classify: "{user_request}"', temperature=0)
            classification = response.strip().lower()
            
            # Validate response
            if "conversation" in classification:
                return "conversation"
            elif "execution" in classification:
                return "execution"
            else:
                # Default to conversation for ambiguous cases
                return "conversation"
                
        except Exception as e:
            print(f"[ChatHandler] Classification failed: {e}, defaulting to conversation")
            return "conversation"
    
    def _handle_conversation(self, user_request: str) -> Tuple[bool, str]:
        """Handle conversational requests without command execution"""
        system_prompt = """You are a friendly penetration testing assistant.

Respond naturally to the user's message. Be concise and helpful.

If they ask what you can do, mention:
- Run security scans (nmap, nikto, etc.)
- Enumerate subdomains and services  
- Check vulnerabilities
- Execute pentesting tools in Kali Linux

Keep responses short and conversational."""

        try:
            response = self._call_llm(system_prompt, user_request, temperature=0.7)
            
            # If streaming, send the response as an event
            if self.stream_callback:
                self.stream_callback("conversation", response)
            
            return True, response
            
        except Exception as e:
            error_msg = f"Error: {e}"
            if self.stream_callback:
                self.stream_callback("error", error_msg)
            return False, error_msg
    
    def _handle_execution(self, user_request: str) -> Tuple[bool, str]:
        """Handle command execution requests with real-time streaming"""
        system_prompt = """You are a penetration testing assistant with direct access to a Kali Linux container.

Execute the user's request using bash commands.

CRITICAL RULES:
1. Return ONLY executable bash commands - no explanations, no comments
2. When you see command output, analyze it and either:
   - Return next command to execute
   - Return "DONE: brief summary" when task is complete
3. If task cannot be done, return "IMPOSSIBLE: reason"
4. Do NOT return "DONE:" as a bash command
5. Do NOT explain what you're doing, just execute

EXAMPLES:
User: "check if port 80 is open on example.com"
You: nmap -p 80 example.com
[sees output]
You: DONE: Port 80 is open on example.com

User: "get HTTP headers"  
You: curl -I https://example.com
[sees output]
You: DONE: Retrieved HTTP headers successfully
"""

        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_request}
        ]
        
        output_parts = []
        max_iterations = 20
        
        try:
            for iteration in range(max_iterations):
                # Get next command from LLM
                response = self._call_llm_conversation(conversation)
                conversation.append({"role": "assistant", "content": response})
                
                # Parse response
                cmd = response.strip()
                
                # Check if done or impossible
                if cmd.startswith("DONE:"):
                    summary = cmd[5:].strip()
                    final_msg = f"✅ {summary}"
                    if self.stream_callback:
                        self.stream_callback("complete", final_msg)
                    return True, "\n".join(output_parts) + f"\n\n{final_msg}"
                
                if cmd.startswith("IMPOSSIBLE:"):
                    reason = cmd[11:].strip()
                    final_msg = f"❌ Cannot complete: {reason}"
                    if self.stream_callback:
                        self.stream_callback("error", final_msg)
                    return False, "\n".join(output_parts) + f"\n\n{final_msg}"
                
                # Clean command (remove explanatory text)
                cmd = self._clean_command(cmd)
                
                # Stream: about to execute command
                if self.stream_callback:
                    self.stream_callback("command", cmd)
                
                # Execute command
                result = self.mcp_client.execute_single_command(cmd)
                
                # Stream: command output
                if self.stream_callback:
                    self.stream_callback("output", result)
                
                # Format output for return value
                terminal_output = f"$ {cmd}\n{result}"
                output_parts.append(terminal_output)
                
                # Add to conversation
                conversation.append({"role": "user", "content": f"Command output:\n{result}"})
            
            # Hit iteration limit
            final_msg = "⚠️ Reached iteration limit"
            if self.stream_callback:
                self.stream_callback("warning", final_msg)
            return False, "\n".join(output_parts) + f"\n\n{final_msg}"
            
        except Exception as e:
            error_msg = f"❌ Error: {e}"
            if self.stream_callback:
                self.stream_callback("error", error_msg)
            return False, "\n".join(output_parts) + f"\n\n{error_msg}"
    
    def _clean_command(self, cmd: str) -> str:
        """
        Clean command text to remove explanations and comments.
        Extract actual executable command.
        """
        # Remove markdown code blocks
        if "```" in cmd:
            lines = cmd.split("\n")
            in_code = False
            code_lines = []
            for line in lines:
                if line.strip().startswith("```"):
                    in_code = not in_code
                    continue
                if in_code:
                    code_lines.append(line)
            if code_lines:
                cmd = "\n".join(code_lines)
        
        # Remove comment-only lines
        lines = cmd.split("\n")
        non_comment_lines = [
            line for line in lines 
            if line.strip() and not line.strip().startswith("#")
        ]
        
        if non_comment_lines:
            # Return first non-comment line (single command)
            return non_comment_lines[0].strip()
        
        return cmd.strip()
    
    def _call_llm(self, system_prompt: str, user_prompt: str, temperature: float = 0) -> str:
        """Single LLM call"""
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }
        
        headers = {
            "Authorization": f"Bearer {self.llm_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(self.llm_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    
    def _call_llm_conversation(self, messages: list) -> str:
        """LLM call with conversation history"""
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": messages
        }
        
        headers = {
            "Authorization": f"Bearer {self.llm_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(self.llm_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()