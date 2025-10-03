#!/usr/bin/env python3
import os
import requests
from pydantic import BaseModel, Field, ValidationError
import json
from typing import Tuple, Union  # Added for compatibility with older Python versions

class TaskModel(BaseModel):
    """Structured task with abstract, detailed description, and verification criteria"""
    abstract: str = Field(..., description="Brief one-line summary of the task")
    description: str = Field(..., description="Detailed step-by-step description of what needs to be done")
    verification: str = Field(..., description="Criteria to verify task completion and expected deliverables")

class TaskTranslator:
    """Translates user requests into structured tasks using LLM"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.moonshot.ai/v1", model: str = "moonshot-v1-8k"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.llm_url = f"{base_url}/chat/completions"
    
    def is_already_structured(self, user_input: str) -> Tuple[bool, Union[TaskModel, None]]:
        """Check if user input is already a valid TaskModel JSON"""
        try:
            # Try to parse as JSON
            data = json.loads(user_input.strip())
            
            # Validate against TaskModel
            task = TaskModel(**data)
            return True, task
        except (json.JSONDecodeError, ValidationError):
            return False, None
    
    def translate_task(self, user_request: str) -> TaskModel:
        """
        Translate a user request into a structured task.
        If the request is already structured (contains abstract, description, verification),
        return it as-is. Otherwise, use LLM to expand it.
        """
        # Check if already structured
        is_structured, task = self.is_already_structured(user_request)
        if is_structured:
            return task
        
        # Create system prompt for task translation
        system_prompt = self._get_translation_system_prompt()
        
        # Create user prompt
        user_prompt = f"""Turn the following sentence into a TaskModel object. Do NOT execute the task, just re-phrase it.

Sentence: {user_request}"""
        
        # Call LLM
        response = self._call_llm(system_prompt, user_prompt)
        
        # Parse and validate response
        try:
            # Try to extract JSON from response (in case LLM adds extra text)
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            task = TaskModel(**data)
            return task
        except (json.JSONDecodeError, ValidationError) as e:
            raise ValueError(f"LLM returned invalid task structure: {e}\nResponse: {response}")
    
    def _get_translation_system_prompt(self) -> str:
        """Get the system prompt for task translation"""
        schema = TaskModel.model_json_schema()
        
        return f"""You are a task translator for penetration testing and security assessment work.

Your ONLY job is to convert a user's brief request into a structured JSON object that matches this schema:

{json.dumps(schema, indent=2)}

CRITICAL RULES:
1. Return ONLY valid JSON that matches the schema above
2. Do NOT execute the task - just re-phrase and expand it
3. Do NOT include explanations, markdown, or code blocks
4. The JSON should be the entire response

Guidelines for expansion:
- **abstract**: A concise one-line summary (e.g., "Passive reconnaissance of domain example.com")
- **description**: Detailed step-by-step plan including:
  * Specific tools to use (nmap, gobuster, nikto, searchsploit, etc.)
  * Enumeration techniques
  * Data to gather
  * Report generation steps
  * Be creative and thorough - add relevant steps beyond the basic request
- **verification**: Clear success criteria including:
  * Expected outputs/deliverables
  * File formats or locations
  * Minimum data points to collect
  * Quality standards

Example input: "scan website x"
Example output:
{{
  "abstract": "Comprehensive web application security assessment of x",
  "description": "Perform a thorough security assessment of the target website x. Steps: 1) Run nmap port scan to identify web services and versions, 2) Use gobuster/dirb for directory enumeration to find hidden paths, 3) Execute nikto web vulnerability scanner, 4) Check for common vulnerabilities (SQL injection, XSS, CSRF), 5) Analyze HTTP headers and security configurations, 6) Screenshot interesting findings, 7) Compile all results into a structured report in /app/work/report.txt with severity ratings.",
  "verification": "A comprehensive report file at /app/work/report.txt containing: discovered directories/files, identified vulnerabilities with severity ratings, service versions, security header analysis, and at least 3 actionable findings or recommendations."
}}

Remember: Return ONLY the JSON object, nothing else."""
    
    def _extract_json(self, response: str) -> str:
        """Extract JSON from response, handling cases where LLM adds extra text"""
        response = response.strip()
        
        # If response starts with {, assume it's pure JSON
        if response.startswith('{'):
            # Find the matching closing brace
            brace_count = 0
            for i, char in enumerate(response):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return response[:i+1]
        
        # Try to find JSON in markdown code blocks
        if '```json' in response:
            start = response.find('```json') + 7
            end = response.find('```', start)
            if end != -1:
                return response[start:end].strip()
        
        if '```' in response:
            start = response.find('```') + 3
            end = response.find('```', start)
            if end != -1:
                return response[start:end].strip()
        
        # Return as-is and let JSON parser handle it
        return response
    
    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM API"""
        payload = {
            "model": self.model,
            "temperature": 0.3,  # Slightly higher for creativity in task expansion
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(self.llm_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to call LLM API: {e}")


def create_translator() -> TaskTranslator:
    """Factory function to create a TaskTranslator with environment variables"""
    api_key = os.getenv("MOONSHOT_API_KEY")
    if not api_key:
        raise ValueError("MOONSHOT_API_KEY environment variable is required")
    
    base_url = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1")
    model = os.getenv("LLM_MODEL", "moonshot-v1-8k")
    
    return TaskTranslator(api_key=api_key, base_url=base_url, model=model)