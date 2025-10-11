#!/usr/bin/env python3
"""
chat_handler.py - Direct chat execution for simple requests
"""
import json
import requests
from typing import Tuple, Optional
from .mcp_agent import MCPAgent


class ChatComplexity:
    """Represents task complexity assessment"""
    def __init__(self, is_simple: bool, reasoning: str, suggested_approach: str = ""):
        self.is_simple = is_simple
        self.reasoning = reasoning
        self.suggested_approach = suggested_approach


class ChatHandler:
    """
    Handles direct chat-style execution for simple requests.
    Routes complex requests to structured task system.
    """
    
    def __init__(self, llm_url: str, llm_key: str, model: str, mcp_client: MCPAgent):
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.mcp_client = mcp_client
    
    def assess_complexity(self, user_request: str) -> ChatComplexity:
        """
        Determine if request is simple (chat mode) or complex (structured task mode).
        
        Simple = Can be done in 1-3 commands, no branching needed
        Complex = Requires decomposition, multiple stages, branching logic
        """
        system_prompt = """You are a task complexity assessor for a penetration testing system.

Analyze user requests and determine if they can be handled as SIMPLE (direct execution) or COMPLEX (structured task tree).

SIMPLE requests (chat mode):
- Single tool execution (e.g., "scan this port", "get HTTP headers")
- Quick checks (e.g., "is port 80 open?", "what's the SSL cert?")
- File operations (e.g., "show me the report", "list scans")
- 1-3 sequential commands, no branching needed
- Can be completed in under 2 minutes

COMPLEX requests (structured task mode):
- Multi-stage operations requiring planning (e.g., "full pentest")
- Multiple parallel branches (e.g., "enumerate all services AND scan for vulns")
- Operations requiring verification and retry logic
- Anything with "full", "comprehensive", "complete assessment"
- Would benefit from tree visualization and progress tracking

Return JSON:
{
    "is_simple": true/false,
    "reasoning": "why this classification",
    "suggested_approach": "how to execute (only if complex)"
}"""

        user_prompt = f"""Analyze this request:

"{user_request}"

Is this SIMPLE (chat mode) or COMPLEX (structured task)?"""

        try:
            response = self._call_llm(system_prompt, user_prompt, temperature=0)
            data = json.loads(self._extract_json(response))
            
            return ChatComplexity(
                is_simple=data.get('is_simple', False),
                reasoning=data.get('reasoning', ''),
                suggested_approach=data.get('suggested_approach', '')
            )
        except Exception as e:
            # Default to simple on error (fail-safe to chat mode)
            return ChatComplexity(
                is_simple=True,
                reasoning=f"Classification failed, defaulting to chat mode: {e}",
                suggested_approach=""
            )
    
    def execute_simple(self, user_request: str) -> Tuple[bool, str]:
        """
        Execute simple request directly without task tree.
        
        Returns:
            (success, output) tuple
        """
        system_prompt = """You are a penetration testing assistant with direct access to a Kali Linux container.

Execute the user's request using bash commands. You can use any tool in Kali Linux.

EXECUTION RULES:
1. Keep it simple - use 1-3 commands maximum
2. Execute commands sequentially
3. Save results to /app/work/ if needed
4. Respond with "DONE: summary" when complete
5. If impossible, respond "IMPOSSIBLE: reason"

RESPONSE FORMAT:
Each response should be ONE executable bash command, like:
  curl -I https://example.com
  nmap -p 80 example.com
  cat /app/work/results.txt

After seeing output, decide next command or mark DONE.

CURRENT REQUEST: Execute this quickly and directly."""

        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User request: {user_request}\n\nExecute this now."}
        ]
        
        max_iterations = 5  # Much lower than MCP's 20
        output_parts = []
        
        try:
            for iteration in range(max_iterations):
                # Get next command from LLM
                cmd = self._call_llm_conversation(conversation)
                conversation.append({"role": "assistant", "content": cmd})
                
                output_parts.append(f"\n💬 AI: {cmd}\n")
                
                # Check if done or impossible
                if cmd.startswith("DONE:"):
                    summary = cmd[5:].strip()
                    return True, "\n".join(output_parts) + f"\n✅ Complete: {summary}"
                
                if cmd.startswith("IMPOSSIBLE:"):
                    reason = cmd[11:].strip()
                    return False, "\n".join(output_parts) + f"\n❌ Cannot complete: {reason}"
                
                # Execute command
                result = self.mcp_client.execute_single_command(cmd)
                output_parts.append(f"$ {cmd}\n{result}\n")
                
                # Add to conversation
                conversation.append({"role": "user", "content": f"Output:\n{result}"})
            
            # Hit iteration limit
            return False, "\n".join(output_parts) + "\n⚠️ Reached iteration limit. Consider using structured task mode."
            
        except Exception as e:
            return False, "\n".join(output_parts) + f"\n❌ Error: {e}"
    
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
    
    def _extract_json(self, response: str) -> str:
        """Extract JSON from response"""
        response = response.strip()
        
        if response.startswith('{'):
            brace_count = 0
            for i, char in enumerate(response):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return response[:i+1]
        
        if '```json' in response:
            start = response.find('```json') + 7
            end = response.find('```', start)
            if end != -1:
                return response[start:end].strip()
        
        return response