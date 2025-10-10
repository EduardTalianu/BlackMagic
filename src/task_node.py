#!/usr/bin/env python3
"""
task_node.py - Staggered parallel execution (2 nodes every 3 minutes)
"""
import os
import json
import requests
import time
from datetime import datetime
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from threading import Semaphore
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
    Task node with staggered parallel execution.
    Starts 2 sub-nodes every 3 minutes, no worker limit except LLM rate limiting.
    """
    
    # Class-level resources
    _llm_semaphore = Semaphore(3)
    _executor = ThreadPoolExecutor(max_workers=100, thread_name_prefix="TaskNode")  # High limit
    
    # Timeout configuration (in seconds)
    BASE_LEAF_TIMEOUT = 300      # 5 minutes for leaf task execution
    TIMEOUT_PER_LEVEL = 300       # +5 minutes per parent level
    PARALLEL_DEPTH_LIMIT = 99     # Effectively unlimited parallel depth
    
    # Staggered execution settings
    STAGGER_BATCH_SIZE = 2        # Start 2 nodes per batch
    STAGGER_DELAY = 180           # 3 minutes between batches
    
    def __init__(
        self,
        task_model: TaskModel,
        trm: TaskRelationManager,
        mcp_client: MCPAgent,
        graph_name: str,
        llm_url: str,
        llm_key: str,
        model: str = "moonshot-v1-8k",
        task_manager=None,
        enable_parallel: bool = True,
        depth: int = 0
    ):
        self.task_pydantic_model = task_model
        self._trm = trm
        self.mcp_client = mcp_client
        self.graph_name = graph_name
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.task_manager = task_manager
        self.enable_parallel = enable_parallel
        self.depth = depth
        self.node_id = task_model.node_id if hasattr(task_model, 'node_id') else None
        self._replan_counter = 0
        self._max_replans = 2
        self._llm_failures = 0
        
        # Calculate timeout for this node based on depth
        self.timeout = self._calculate_timeout()
        
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
            print(f"[INIT] Node {self.node_id} at D{self.depth}, timeout={self.timeout}s")
    
    def _calculate_timeout(self) -> int:
        """Calculate timeout based on depth: base + (level × increment)"""
        timeout = self.BASE_LEAF_TIMEOUT + (self.TIMEOUT_PER_LEVEL * self.depth)
        return timeout
    
    def execute(self, rebranch_prompt: str = '') -> TaskModelOut:
        """Main execution with timeout awareness"""
        try:
            print(f"[{self.node_id}][D{self.depth}] ========== EXECUTE (timeout={self.timeout}s) ==========")
            print(f"[{self.node_id}][D{self.depth}] Task: {self.task_pydantic_model.abstract[:80]}")
            
            if self.task_manager and self.node_id:
                if self.task_manager.is_node_cancelled(self.node_id):
                    raise TaskImpossibleException("Node was cancelled")
            
            advice = self._collect_upper_chain_advice(rebranch_prompt)
            self._update_status(TaskStatus.PLANNING)
            
            print(f"[{self.node_id}][D{self.depth}] Checking if branching needed...")
            branch_req = self.check_branching_requirement(advice)
            
            num_tasks = len(branch_req.task_chain.tasks)
            print(f"[{self.node_id}][D{self.depth}] Decision: {num_tasks} task(s)")
            
            self._flush_graph()
            
            if num_tasks > 1:
                print(f"[{self.node_id}][D{self.depth}] >>> BRANCHING into {num_tasks} sub-tasks")
                return self.branch_and_execute(branch_req)
            else:
                print(f"[{self.node_id}][D{self.depth}] >>> DIRECT EXECUTION")
                return self.direct_execute(advice)
                
        except TaskImpossibleException as e:
            print(f"[{self.node_id}][D{self.depth}] Task impossible: {e}")
            self._update_status(TaskStatus.IMPOSSIBLE, str(e))
            self._flush_graph()
            raise
        except Exception as e:
            print(f"[{self.node_id}][D{self.depth}] Task failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            self._update_status(TaskStatus.FAILED, str(e))
            self._flush_graph()
            raise
    
    def _update_status(self, status: TaskStatus, error: str = None):
        """Update node status"""
        self.task_pydantic_model.task_status = status
        self._flush_graph()
        
        if self.task_manager and self.node_id:
            self.task_manager.update_node_status(
                self.node_id,
                status.value if isinstance(status, TaskStatus) else status,
                error
            )
    
    def check_branching_requirement(self, advice: str) -> BranchRequirement:
        """Ask LLM planner with circuit breaker"""
        if self._llm_failures >= 3:
            print(f"[{self.node_id}][D{self.depth}] Circuit breaker: executing directly")
            return BranchRequirement(
                needs_branching=False,
                reasoning="Circuit breaker triggered",
                task_chain=TaskChain(
                    strategy="Direct execution",
                    tasks=[SubTask(
                        abstract=self.task_pydantic_model.abstract,
                        description=self.task_pydantic_model.description,
                        verification=self.task_pydantic_model.verification,
                        rationale="Circuit breaker fallback"
                    )]
                )
            )
        
        system_prompt = self._get_planner_system_prompt()
        user_prompt = f"""Analyze this task and decide if it needs to be broken down:

Task: {self.task_pydantic_model.abstract}
Description: {self.task_pydantic_model.description}
Verification: {self.task_pydantic_model.verification}

Context: {advice}
Current depth: {self.depth}

Return JSON:
{{
    "needs_branching": true/false,
    "reasoning": "explanation",
    "task_chain": {{
        "strategy": "approach",
        "tasks": [
            {{
                "abstract": "brief summary",
                "description": "what to do",
                "verification": "how to verify",
                "rationale": "why needed"
            }}
        ]
    }}
}}

RULES:
- Atomic task → needs_branching=false, 1 task
- Complex → needs_branching=true, 2-5 independent sub-tasks
- At depth {self.depth}, prefer fewer sub-tasks (2-3 max)
- Each sub-task MUST be independently executable
- Maximum depth recommended: 3-4 levels
"""
        
        print(f"[{self.node_id}][D{self.depth}] Calling LLM planner...")
        
        try:
            response = self._call_llm(system_prompt, user_prompt, temperature=0.3, timeout=60)
            print(f"[{self.node_id}][D{self.depth}] Planner response: {len(response)} chars")
            
            self._llm_failures = 0
            
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            result = BranchRequirement(**data)
            
            print(f"[{self.node_id}][D{self.depth}] Parsed: {len(result.task_chain.tasks)} tasks")
            return result
            
        except Exception as e:
            print(f"[{self.node_id}][D{self.depth}] Planner failed: {e}")
            self._llm_failures += 1
            
            return BranchRequirement(
                needs_branching=False,
                reasoning=f"Planner failed: {str(e)}",
                task_chain=TaskChain(
                    strategy="Direct execution (fallback)",
                    tasks=[SubTask(
                        abstract=self.task_pydantic_model.abstract,
                        description=self.task_pydantic_model.description,
                        verification=self.task_pydantic_model.verification,
                        rationale="Fallback"
                    )]
                )
            )
    
    def direct_execute(self, advice: str) -> TaskModelOut:
        """Direct execution with timeout"""
        print(f"[{self.node_id}][D{self.depth}] ========== DIRECT EXECUTION ==========")
        self._update_status(TaskStatus.WORKING)
        
        for attempt in range(1, 4):
            try:
                print(f"[{self.node_id}][D{self.depth}] Attempt {attempt}/3 (timeout={self.timeout}s)")
                
                if self.task_manager and self.node_id:
                    if self.task_manager.is_node_cancelled(self.node_id):
                        raise TaskImpossibleException("Cancelled")
                
                print(f"[{self.node_id}][D{self.depth}] Running MCP agent...")
                raw_result = self.run_mcp_agent(advice)
                print(f"[{self.node_id}][D{self.depth}] MCP completed: {len(raw_result)} chars")
                
                if "DONE:" in raw_result:
                    print(f"[{self.node_id}][D{self.depth}] MCP marked as DONE")
                    result = self.digest_result_to_abstract(raw_result)
                    self._update_status(TaskStatus.COMPLETED)
                    print(f"[{self.node_id}][D{self.depth}] ✓ Status updated to COMPLETED")
                    return result
                
                print(f"[{self.node_id}][D{self.depth}] Verifying...")
                if self.check_task_result(raw_result):
                    print(f"[{self.node_id}][D{self.depth}] ✓ Verified!")
                    result = self.digest_result_to_abstract(raw_result)
                    self._update_status(TaskStatus.COMPLETED)
                    print(f"[{self.node_id}][D{self.depth}] ✓ Status updated to COMPLETED")
                    return result
                else:
                    print(f"[{self.node_id}][D{self.depth}] ✗ Verification failed")
                    raise TaskNeedTurningException(f"Verification failed (attempt {attempt})")
                    
            except TaskNeedTurningException as e:
                if attempt < 3:
                    advice += f"\n\nPrevious failed: {str(e)}\nTry different approach."
                    continue
                else:
                    self._update_status(TaskStatus.FAILED, str(e))
                    raise TaskImpossibleException(f"Failed after 3 attempts: {str(e)}")
        
        self._update_status(TaskStatus.FAILED, "Exhausted attempts")
        raise TaskImpossibleException("Exhausted attempts")
    
    def branch_and_execute(self, branch_req: BranchRequirement) -> TaskModelOut:
        """Branch and execute with staggered parallel execution"""
        try:
            num_subtasks = len(branch_req.task_chain.tasks)
            print(f"[{self.node_id}][D{self.depth}] ========== BRANCHING: {num_subtasks} sub-tasks ==========")
            
            # Create sub-nodes
            sub_nodes = []
            for i, sub_task in enumerate(branch_req.task_chain.tasks):
                print(f"[{self.node_id}][D{self.depth}] Creating sub-node {i+1}/{num_subtasks}: {sub_task.abstract[:60]}")
                
                sub_model = TaskModel(
                    abstract=sub_task.abstract,
                    description=sub_task.description,
                    verification=sub_task.verification
                )
                
                sub_mcp_client = self._create_isolated_mcp_client()
                
                sub_node = TaskNode(
                    task_model=sub_model,
                    trm=self._trm,
                    mcp_client=sub_mcp_client,
                    graph_name=self.graph_name,
                    llm_url=self.llm_url,
                    llm_key=self.llm_key,
                    model=self.model,
                    task_manager=self.task_manager,
                    enable_parallel=self.enable_parallel,
                    depth=self.depth + 1
                )
                sub_nodes.append(sub_node)
            
            # Register in graph
            print(f"[{self.node_id}][D{self.depth}] Registering {num_subtasks} sub-nodes...")
            self._trm.add_sub_tasks(self.node_id, sub_nodes)
            self._flush_graph()
            
            # Register with task manager and assign callbacks
            for i, node in enumerate(sub_nodes):
                if not node.node_id:
                    raise RuntimeError(f"Sub-node {i} has no node_id!")
                
                print(f"[{self.node_id}][D{self.depth}] Sub-node {i+1} ID: {node.node_id}, timeout: {node.timeout}s")
                
                if self.task_manager:
                    self.task_manager.register_node(
                        task_id=node.graph_name,
                        node_id=node.node_id,
                        node_info={
                            'abstract': node.task_pydantic_model.abstract,
                            'parent_id': self.node_id,
                            'status': 'pending'
                        }
                    )
                    
                    callback = self.task_manager.get_node_output_callback(node.node_id)
                    node.mcp_client.output_callback = callback
            
            # Use staggered parallel execution
            print(f"[{self.node_id}][D{self.depth}] Strategy: STAGGERED PARALLEL (2 every 3min)")
            results = self._execute_parallel_staggered(sub_nodes)
            
            print(f"[{self.node_id}][D{self.depth}] All {num_subtasks} sub-tasks completed!")
            
            self._update_status(TaskStatus.COMPLETED)
            return self._aggregate_results(results)
            
        except TaskImpossibleException as e:
            print(f"[{self.node_id}][D{self.depth}] Branch failed: {e}")
            if self._replan_counter < self._max_replans:
                self._replan_counter += 1
                print(f"[{self.node_id}][D{self.depth}] Replanning ({self._replan_counter}/{self._max_replans})...")
                self._trm.remove_node(self.node_id)
                rebranch_prompt = f"Previous failed: {str(e)}\nAttempt {self._replan_counter + 1}/{self._max_replans + 1}"
                return self.execute(rebranch_prompt)
            else:
                raise
    
    def _execute_parallel_staggered(self, sub_nodes: List['TaskNode']) -> List[TaskModelOut]:
        """
        Execute in parallel with staggered starts.
        Starts STAGGER_BATCH_SIZE nodes every STAGGER_DELAY seconds.
        No limit on total concurrent execution except LLM rate limiting.
        """
        print(f"[{self.node_id}][D{self.depth}] STAGGERED PARALLEL: {len(sub_nodes)} nodes")
        print(f"[{self.node_id}][D{self.depth}] Batch size: {self.STAGGER_BATCH_SIZE}, Delay: {self.STAGGER_DELAY}s")
        
        results = []
        failed_nodes = []
        future_to_node = {}
        
        # Calculate total timeout including stagger time
        max_child_timeout = max(node.timeout for node in sub_nodes)
        num_batches = (len(sub_nodes) + self.STAGGER_BATCH_SIZE - 1) // self.STAGGER_BATCH_SIZE
        stagger_time = (num_batches - 1) * self.STAGGER_DELAY
        total_timeout = max_child_timeout + stagger_time + 600  # 10 min buffer
        
        print(f"[{self.node_id}][D{self.depth}] Max child timeout: {max_child_timeout}s")
        print(f"[{self.node_id}][D{self.depth}] Stagger time: {stagger_time}s ({num_batches} batches)")
        print(f"[{self.node_id}][D{self.depth}] Total timeout: {total_timeout}s")
        
        # Submit in batches
        for batch_idx in range(0, len(sub_nodes), self.STAGGER_BATCH_SIZE):
            batch = sub_nodes[batch_idx:batch_idx + self.STAGGER_BATCH_SIZE]
            batch_num = batch_idx // self.STAGGER_BATCH_SIZE + 1
            
            # Wait before subsequent batches
            if batch_idx > 0:
                print(f"[{self.node_id}][D{self.depth}] ⏳ Waiting {self.STAGGER_DELAY}s before batch {batch_num}/{num_batches}...")
                time.sleep(self.STAGGER_DELAY)
            
            print(f"[{self.node_id}][D{self.depth}] 🚀 Starting batch {batch_num}/{num_batches}: {len(batch)} nodes")
            
            for node in batch:
                print(f"[{self.node_id}][D{self.depth}]    → Submitting {node.node_id}: {node.task_pydantic_model.abstract[:50]}")
                future = self._executor.submit(self._safe_execute_node, node)
                future_to_node[future] = node
        
        print(f"[{self.node_id}][D{self.depth}] ✅ All {len(sub_nodes)} nodes submitted")
        print(f"[{self.node_id}][D{self.depth}] 📊 Collecting results (timeout={total_timeout}s)...")
        
        # Collect results as they complete
        completed_count = 0
        try:
            for future in as_completed(future_to_node, timeout=total_timeout):
                node = future_to_node[future]
                try:
                    result = future.result(timeout=30)
                    results.append(result)
                    completed_count += 1
                    print(f"[STAGGER][D{self.depth}] ✓ {node.node_id} completed ({completed_count}/{len(sub_nodes)})")
                except TimeoutError:
                    print(f"[STAGGER][D{self.depth}] ✗ {node.node_id} result timeout")
                    failed_nodes.append((node, "Result retrieval timeout"))
                except Exception as e:
                    print(f"[STAGGER][D{self.depth}] ✗ {node.node_id} failed: {e}")
                    failed_nodes.append((node, str(e)))
        
        except TimeoutError:
            incomplete = [node for future, node in future_to_node.items() if not future.done()]
            print(f"[STAGGER][D{self.depth}] ⏰ TIMEOUT: {len(incomplete)}/{len(sub_nodes)} nodes unfinished")
            for node in incomplete:
                failed_nodes.append((node, "Execution timeout"))
        
        # Report final status
        print(f"[{self.node_id}][D{self.depth}] ========== STAGGERED EXECUTION COMPLETE ==========")
        print(f"[{self.node_id}][D{self.depth}] ✓ Completed: {len(results)}/{len(sub_nodes)}")
        print(f"[{self.node_id}][D{self.depth}] ✗ Failed: {len(failed_nodes)}/{len(sub_nodes)}")
        
        if failed_nodes:
            error_summary = "\n".join([f"- {n.task_pydantic_model.abstract[:60]}: {e}" for n, e in failed_nodes])
            raise TaskImpossibleException(
                f"Staggered parallel execution: {len(failed_nodes)}/{len(sub_nodes)} nodes failed:\n{error_summary}"
            )
        
        return results
    
    def _safe_execute_node(self, node: 'TaskNode') -> TaskModelOut:
        """Thread-safe wrapper for node execution"""
        try:
            print(f"[WORKER][D{node.depth}] 🏁 Starting {node.node_id} (timeout={node.timeout}s)")
            result = node.execute()
            print(f"[WORKER][D{node.depth}] ✅ Completed {node.node_id}")
            return result
        except Exception as e:
            print(f"[WORKER][D{node.depth}] ❌ Failed {node.node_id}: {e}")
            try:
                node._update_status(TaskStatus.FAILED, str(e))
            except:
                pass
            raise
    
    def _create_isolated_mcp_client(self) -> MCPAgent:
        """Create isolated MCP client for sub-node"""
        return MCPAgent(
            container_name=self.mcp_client.container_name,
            llm_url=self.llm_url,
            llm_key=self.llm_key,
            model=self.model,
            log_callback=None,
            output_callback=None,
            install_log_callback=self.mcp_client.install_log_callback
        )
    
    def _aggregate_results(self, results: List[TaskModelOut]) -> TaskModelOut:
        """Aggregate multiple task results"""
        if not results:
            raise TaskImpossibleException("No results to aggregate")
        if len(results) == 1:
            return results[0]
        
        summaries = [r.result for r in results if r.result]
        combined = "\n\n".join([f"Sub-task {i+1}: {s}" for i, s in enumerate(summaries)])
        
        return TaskModelOut(
            task_id=self.graph_name,
            abstract=self.task_pydantic_model.abstract,
            description=self.task_pydantic_model.description,
            verification=self.task_pydantic_model.verification,
            status=TaskStatus.COMPLETED,
            result=combined,
            graph=self._trm.get_graph_content(),
            created_at=datetime.now(),
            completed_at=datetime.now()
        )
    
    def run_mcp_agent(self, advice: str) -> str:
        """Run MCP agent with system prompt"""
        system_prompt = self._get_executor_system_prompt(advice)
        return self.mcp_client.execute_task(self.task_pydantic_model, system_prompt)
    
    def check_task_result(self, raw_result: str) -> bool:
        """Verify task completion"""
        system_prompt = """Task verification critic.

Return JSON:
{
    "criteria_met": true/false,
    "reasoning": "explanation"
}
"""
        user_prompt = f"""Task: {self.task_pydantic_model.abstract}

Verification: {self.task_pydantic_model.verification}

Output: {raw_result[:2000]}

Met?"""
        
        try:
            response = self._call_llm(system_prompt, user_prompt, temperature=0, timeout=30)
            data = json.loads(self._extract_json(response))
            return data.get('criteria_met', False)
        except:
            return False
    
    def digest_result_to_abstract(self, raw_result: str) -> TaskModelOut:
        """Summarize task result"""
        system_prompt = """Summarize accomplishment.

Return JSON:
{
    "summary": "2-3 sentences"
}
"""
        user_prompt = f"""Task: {self.task_pydantic_model.abstract}

Output: {raw_result[:2000]}

Summary?"""
        
        try:
            response = self._call_llm(system_prompt, user_prompt, temperature=0, timeout=30)
            data = json.loads(self._extract_json(response))
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
            created_at=datetime.now(),
            completed_at=datetime.now()
        )
    
    def _collect_upper_chain_advice(self, rebranch_prompt: str) -> str:
        """Collect advice from parent chain and credentials"""
        parts = []
        if rebranch_prompt:
            parts.append(f"REPLANNING: {rebranch_prompt}")
        if self.node_id:
            advice = self._trm.get_upper_chain_advice(self.node_id)
            if advice:
                parts.append(advice)
            
            cred_chain = self._trm.get_credential_chain(self.node_id)
            if cred_chain:
                parts.append("\n=== AVAILABLE CREDENTIALS ===")
                for cred in cred_chain:
                    parts.append(f"From {cred['direction']} ({cred['node_id']}): {cred['abstract']}")
        
        return "\n\n".join(parts)
    
    def _flush_graph(self) -> None:
        """Update graph visualization"""
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
        """System prompt for task planner"""
        return """Penetration testing task planner.

Analyze and decide decomposition.

Guidelines:
- Atomic → needs_branching=false, 1 task
- Complex → needs_branching=true, 2-5 independent sub-tasks
- Independent sub-tasks only
- Design for parallelization
- Simple decomposition at deep levels

Return valid JSON."""
    
    def _get_executor_system_prompt(self, advice: str) -> str:
        """System prompt for task executor"""
        return f"""You are an expert penetration tester executing a specific task in a Kali Linux container.

ENVIRONMENT:
- You have access to a Kali Linux container with standard pentesting tools
- Tools are auto-installed if missing (nmap, subfinder, amass, gobuster, etc.)
- Working directory: /app/work (save all output files here)
- You can execute any bash command

YOUR TASK:
Abstract: {self.task_pydantic_model.abstract}

Description: {self.task_pydantic_model.description}

Verification Criteria: {self.task_pydantic_model.verification}

CONTEXT FROM PREVIOUS WORK:
{advice if advice else "No previous context"}

EXECUTION RULES:
1. Execute ONE command at a time
2. Each response must be an EXECUTABLE command (not comments)
3. DO NOT output only bash comments like "# Let's check..."
4. After each command, you'll receive its output
5. Save results to files in /app/work/
6. When verification criteria are met, respond: "DONE: brief summary of what was accomplished"

CRITICAL - HANDLING MISSING RESOURCES:
If you encounter missing API keys, unavailable services, or blocked resources:
- DO NOT get stuck in a loop checking for them
- Try alternative approaches (different tools, public sources, workarounds)
- If truly impossible after 3 attempts, respond: "DONE: Unable to complete - reason"
- Example: If SecurityTrails API key missing, use crt.sh, Shodan, or other passive DNS sources

COMMAND FORMAT:
Your response should be a single executable command, for example:
  nmap -sV scanme.nmap.org
  subfinder -d example.com -o /app/work/subdomains.txt
  curl -s "https://crt.sh/?q=%.example.com&output=json" > /app/work/certs.json

AVOID:
- Multiple commands in one response (execute one at a time)
- Only comments without actual commands
- Checking for the same thing repeatedly
- Infinite loops looking for missing resources

BEGIN EXECUTION:
Execute commands step-by-step to complete the task. Respond with your first command now."""
    
    def _call_llm(self, system_prompt: str, user_prompt: str, temperature: float = 0, timeout: int = 60) -> str:
        """Call LLM with retry logic and rate limiting"""
        max_retries = 5
        base_delay = 2
        
        with self._llm_semaphore:
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
                    
                    response = requests.post(
                        self.llm_url, 
                        headers=headers, 
                        json=payload, 
                        timeout=timeout
                    )
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"]["content"].strip()
                    
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 429:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            print(f"[LLM] Rate limited (429). Waiting {delay}s... (attempt {attempt + 1}/{max_retries})")
                            time.sleep(delay)
                            continue
                        else:
                            raise RuntimeError(f"Rate limited after {max_retries} attempts")
                    else:
                        raise
                except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        time.sleep(delay)
                        continue
                    else:
                        raise RuntimeError(f"LLM failed after {max_retries} attempts: {e}")
            
            raise RuntimeError("Exhausted retries")
    
    def _extract_json(self, response: str) -> str:
        """Extract JSON from LLM response"""
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
        
        if '```' in response:
            start = response.find('```') + 3
            end = response.find('```', start)
            if end != -1:
                return response[start:end].strip()
        
        return response