#!/usr/bin/env python3
"""
task_node.py - TaskNode implementation for hierarchical task execution
"""
import os
import json
import requests
from datetime import datetime
from typing import Optional, List
from .task_models import (
    TaskModel, TaskStatus, TaskResult, TaskChain, 
    BranchRequirement, SubTask, TaskModelOut
)
from .task_relation_manager import TaskRelationManager
from .mcp_agent import MCPAgent


class TaskNeedTurningException(Exception):
    """Raised when task needs to be retried with different approach"""
    pass


class TaskImpossibleException(Exception):
    """Raised when task cannot be completed"""
    pass


class TaskNode:
    """
    A node in the task execution tree.
    Can execute directly or branch into sub-tasks.
    """
    
    def __init__(
        self,
        task_model: TaskModel,
        trm: TaskRelationManager,
        mcp_client: MCPAgent,
        graph_name: str,
        llm_url: str,
        llm_key: str,
        model: str = "moonshot-v1-8k",
        task_manager=None  # Reference to TaskManager for registration
    ):
        self.task_pydantic_model = task_model
        self._trm = trm
        self.mcp_client = mcp_client
        self.graph_name = graph_name
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.task_manager = task_manager
        self.node_id = task_model.node_id if hasattr(task_model, 'node_id') else None
        self._replan_counter = 0
        self._max_replans = 2
        
        # Register node with task manager if available
        if self.task_manager and self.node_id:
            self.task_manager.register_node(
                task_id=self.graph_name,
                node_id=self.node_id,
                node_info={
                    'abstract': self.task_pydantic_model.abstract,
                    'parent_id': getattr(self.task_pydantic_model, 'parent_id', None),
                    'status': 'pending'
                }
            )
    
    def execute(self, rebranch_prompt: str = '') -> TaskModelOut:
        """
        Main execution entry point.
        Decides whether to execute directly or branch into sub-tasks.
        """
        try:
            # Check for cancellation
            if self.task_manager and self.node_id:
                if self.task_manager.is_node_cancelled(self.node_id):
                    raise TaskImpossibleException("Node was cancelled")
            
            # 1. Collect context from parent/siblings
            advice = self._collect_upper_chain_advice(rebranch_prompt)
            
            # 2. Ask planner: do we need sub-tasks?
            self._update_status(TaskStatus.PLANNING)
            branch_req = self.check_branching_requirement(advice)
            
            # 3. Update status and flush graph
            self._update_status(TaskStatus.PLANNING)
            self._flush_graph()
            
            # 4. Execute based on decision
            if len(branch_req.task_chain.tasks) > 1:
                return self.branch_and_execute(branch_req)
            else:
                return self.direct_execute(advice)
                
        except TaskImpossibleException as e:
            self._update_status(TaskStatus.IMPOSSIBLE, str(e))
            self._flush_graph()
            raise
        except Exception as e:
            self._update_status(TaskStatus.FAILED, str(e))
            self._flush_graph()
            raise
    
    def _update_status(self, status: TaskStatus, error: str = None):
        """Update node status in both graph and task manager"""
        self.task_pydantic_model.task_status = status
        self._flush_graph()
        
        if self.task_manager and self.node_id:
            self.task_manager.update_node_status(
                self.node_id,
                status.value if isinstance(status, TaskStatus) else status,
                error
            )
    
    def check_branching_requirement(self, advice: str) -> BranchRequirement:
        """
        Ask LLM planner whether this task needs to be broken down.
        Returns a decision and potentially a chain of sub-tasks.
        """
        system_prompt = self._get_planner_system_prompt()
        user_prompt = f"""Analyze this task and decide if it needs to be broken down:

Task: {self.task_pydantic_model.abstract}
Description: {self.task_pydantic_model.description}
Verification: {self.task_pydantic_model.verification}

Context from previous work:
{advice}

Return a JSON object with this structure:
{{
    "needs_branching": true/false,
    "reasoning": "why you made this decision",
    "task_chain": {{
        "strategy": "overall approach",
        "tasks": [
            {{
                "abstract": "brief summary",
                "description": "what to do",
                "verification": "how to verify",
                "rationale": "why this step"
            }}
        ]
    }}
}}

Rules:
- If task is atomic (single command/action), set needs_branching=false and return 1 task
- If task is complex, set needs_branching=true and break into 2-5 atomic sub-tasks
- Each sub-task must be independently executable
- Order matters: tasks should build on each other
"""
        
        response = self._call_llm(system_prompt, user_prompt, temperature=0.3)
        
        try:
            # Extract JSON from response
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            return BranchRequirement(**data)
        except Exception as e:
            # Fallback: treat as single task
            return BranchRequirement(
                needs_branching=False,
                reasoning="Failed to parse planner response",
                task_chain=TaskChain(
                    strategy="Execute directly",
                    tasks=[SubTask(
                        abstract=self.task_pydantic_model.abstract,
                        description=self.task_pydantic_model.description,
                        verification=self.task_pydantic_model.verification,
                        rationale="Direct execution"
                    )]
                )
            )
    
    def direct_execute(self, advice: str) -> TaskModelOut:
        """
        Execute this task directly without branching.
        Tries up to 3 times with feedback.
        """
        self._update_status(TaskStatus.WORKING)
        
        for attempt in range(1, 4):
            try:
                # Check for cancellation
                if self.task_manager and self.node_id:
                    if self.task_manager.is_node_cancelled(self.node_id):
                        raise TaskImpossibleException("Node was cancelled")
                
                # Run the MCP agent to execute commands
                raw_result = self.run_mcp_agent(advice)
                
                # Ask critic LLM if task is complete
                if self.check_task_result(raw_result):
                    # Success! Digest the result
                    result = self.digest_result_to_abstract(raw_result)
                    self._update_status(TaskStatus.COMPLETED)
                    return result
                else:
                    # Not complete, try again with feedback
                    raise TaskNeedTurningException(
                        f"Attempt {attempt}: Verification criteria not met"
                    )
                    
            except TaskNeedTurningException as e:
                if attempt < 3:
                    advice += f"\n\nPrevious attempt failed: {str(e)}\nTry a different approach."
                    continue
                else:
                    raise TaskImpossibleException(
                        f"Failed after 3 attempts: {str(e)}"
                    )
        
        raise TaskImpossibleException("Exhausted all attempts")
    
    def branch_and_execute(self, branch_req: BranchRequirement) -> TaskModelOut:
        """
        Branch into sub-tasks and execute them sequentially.
        """
        try:
            # Create TaskNode for each sub-task
            sub_nodes = []
            for sub_task in branch_req.task_chain.tasks:
                sub_model = TaskModel(
                    abstract=sub_task.abstract,
                    description=sub_task.description,
                    verification=sub_task.verification
                )
                sub_node = TaskNode(
                    task_model=sub_model,
                    trm=self._trm,
                    mcp_client=self.mcp_client,
                    graph_name=self.graph_name,
                    llm_url=self.llm_url,
                    llm_key=self.llm_key,
                    model=self.model,
                    task_manager=self.task_manager  # Pass task_manager reference
                )
                sub_nodes.append(sub_node)
            
            # Register sub-tasks in graph
            self._trm.add_sub_tasks(self.node_id, sub_nodes)
            
            # Execute each sub-task sequentially
            last_result = None
            for node in sub_nodes:
                last_result = node.execute()
            
            # Mark parent as completed
            self._update_status(TaskStatus.COMPLETED)
            
            # Return the last result (or aggregate if needed)
            return last_result
            
        except TaskImpossibleException as e:
            # Try replanning if we haven't exceeded limit
            if self._replan_counter < self._max_replans:
                self._replan_counter += 1
                self._trm.remove_node(self.node_id)
                
                # Try again with additional context
                rebranch_prompt = f"Previous plan failed: {str(e)}\nAttempt {self._replan_counter + 1}/{self._max_replans + 1}"
                return self.execute(rebranch_prompt)
            else:
                raise
    
    def run_mcp_agent(self, advice: str) -> str:
        """
        Run the MCP agent to execute the task.
        Returns the raw execution output.
        """
        system_prompt = self._get_executor_system_prompt(advice)
        
        # Execute using MCP agent
        result = self.mcp_client.execute_task(
            task=self.task_pydantic_model,
            system_prompt=system_prompt
        )
        
        return result
    
    def check_task_result(self, raw_result: str) -> bool:
        """
        Ask critic LLM if the task verification criteria are met.
        """
        system_prompt = """You are a task verification critic.
Your job is to determine if a task's verification criteria have been met based on the execution output.

Return ONLY a JSON object:
{
    "criteria_met": true/false,
    "reasoning": "explanation of your decision"
}
"""
        
        user_prompt = f"""Task: {self.task_pydantic_model.abstract}

Verification Criteria:
{self.task_pydantic_model.verification}

Execution Output:
{raw_result}

Have the verification criteria been met?"""
        
        response = self._call_llm(system_prompt, user_prompt, temperature=0)
        
        try:
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            return data.get('criteria_met', False)
        except:
            # If we can't parse, assume not complete
            return False
    
    def digest_result_to_abstract(self, raw_result: str) -> TaskModelOut:
        """
        Convert raw execution output into a structured result.
        """
        system_prompt = """You are a task result summarizer.
Create a brief, clear summary of what was accomplished.

Return ONLY a JSON object:
{
    "summary": "2-3 sentence summary of what was done and found"
}
"""
        
        user_prompt = f"""Task: {self.task_pydantic_model.abstract}

Execution Output:
{raw_result}

Provide a concise summary of what was accomplished."""
        
        response = self._call_llm(system_prompt, user_prompt, temperature=0)
        
        try:
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            summary = data.get('summary', raw_result[:200])
        except:
            summary = raw_result[:200]
        
        return TaskModelOut(
            task_id=self.graph_name,
            abstract=self.task_pydantic_model.abstract,
            description=self.task_pydantic_model.description,
            verification=self.task_pydantic_model.verification,
            status=TaskStatus.COMPLETED,
            result=summary,
            graph=self._trm.get_graph_content(),
            created_at=datetime.now(),  # Set actual datetime
            completed_at=datetime.now()
        )
    
    def _collect_upper_chain_advice(self, rebranch_prompt: str) -> str:
        """Collect advice from parent and siblings"""
        advice_parts = []
        
        if rebranch_prompt:
            advice_parts.append(f"REPLANNING NOTE: {rebranch_prompt}")
        
        if self.node_id:
            chain_advice = self._trm.get_upper_chain_advice(self.node_id)
            if chain_advice:
                advice_parts.append(chain_advice)
        
        return "\n\n".join(advice_parts)
    
    def _flush_graph(self) -> None:
        """Update node status in graph"""
        if self.node_id:
            status_map = {
                TaskStatus.PENDING: 'pending',
                TaskStatus.PLANNING: 'planning',
                TaskStatus.WORKING: 'working',
                TaskStatus.COMPLETED: 'completed',
                TaskStatus.FAILED: 'failed',
                TaskStatus.CANCELLED: 'cancelled',
                TaskStatus.IMPOSSIBLE: 'impossible'
            }
            self._trm.update_node_status(
                self.node_id,
                status_map.get(self.task_pydantic_model.task_status, 'pending')
            )
    
    def _get_planner_system_prompt(self) -> str:
        """System prompt for the planner LLM"""
        return """You are a penetration testing task planner.

Your job is to analyze a security task and decide if it needs to be broken down into smaller steps.

Guidelines:
- Atomic tasks (single nmap scan, single tool run) don't need branching
- Complex tasks (full recon, complete assessment) should be broken into 2-5 steps
- Each sub-task must be independently executable
- Sub-tasks should build on each other logically
- Be specific about tools and techniques

Always return valid JSON matching the schema provided."""
    
    def _get_executor_system_prompt(self, advice: str) -> str:
        """System prompt for the executor"""
        return f"""You are an expert penetration tester executing a specific task.

TASK CONTEXT:
Abstract: {self.task_pydantic_model.abstract}
Description: {self.task_pydantic_model.description}
Verification: {self.task_pydantic_model.verification}

CONTEXT FROM PREVIOUS WORK:
{advice}

Execute this task step by step, one command at a time.
When all verification criteria are met, respond with 'DONE: summary'."""
    
    def _call_llm(self, system_prompt: str, user_prompt: str, temperature: float = 0) -> str:
        """Call the LLM API with retry logic for rate limiting"""
        import time
        
        max_retries = 5
        base_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
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
                
                response = requests.post(self.llm_url, headers=headers, json=payload, timeout=90)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"].strip()
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:  # Rate limit
                    if attempt < max_retries - 1:
                        # Exponential backoff: 2, 4, 8, 16, 32 seconds
                        delay = base_delay * (2 ** attempt)
                        print(f"[LLM] Rate limited (429). Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                    else:
                        raise RuntimeError(f"Failed after {max_retries} attempts due to rate limiting")
                else:
                    raise RuntimeError(f"LLM API error: {e}")
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[LLM] Request failed: {e}. Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    raise RuntimeError(f"Failed to call LLM API after {max_retries} attempts: {e}")
        
        raise RuntimeError("Exhausted all retry attempts")
    
    def _extract_json(self, response: str) -> str:
        """Extract JSON from LLM response"""
        response = response.strip()
        
        # If starts with {, find matching closing brace
        if response.startswith('{'):
            brace_count = 0
            for i, char in enumerate(response):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return response[:i+1]
        
        # Try markdown code blocks
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
        
        return response