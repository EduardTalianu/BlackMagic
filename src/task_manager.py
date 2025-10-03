#!/usr/bin/env python3
"""
task_manager.py - Global task manager and background execution orchestrator
"""
import os
import uuid
import asyncio
from datetime import datetime
from threading import Thread
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .task_models import TaskModel, TaskStatus, TaskModelOut, TaskStatusResponse
from .task_node import TaskNode, TaskImpossibleException
from .task_relation_manager import TaskRelationManager
from .mcp_agent import MCPAgent


class TaskManager:
    """
    Global task manager that:
    - Generates task IDs
    - Spawns background workers
    - Tracks task status at node level
    - Provides status query endpoints
    - Enables parallel node execution
    """
    
    def __init__(
        self,
        container_name: str,
        llm_url: str,
        llm_key: str,
        model: str,
        work_dir: str
    ):
        self.container_name = container_name
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.work_dir = work_dir
        
        # Task storage: task_id -> task_info
        self.tasks: Dict[str, dict] = {}
        
        # Node storage: node_id -> node_info (for all nodes across all tasks)
        self.nodes: Dict[str, dict] = {}
        self.nodes_lock = Lock()
        
        # Thread pool for background execution (increased for parallel nodes)
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # Ensure work directory exists
        os.makedirs(work_dir, exist_ok=True)
    
    def create_task(self, task: TaskModel) -> str:
        """
        Create a new task and spawn background worker.
        Returns task_id immediately.
        """
        # Generate unique task ID
        task_id = str(uuid.uuid4())[:8]
        
        print(f"[TaskManager] Creating task {task_id}")
        print(f"[TaskManager] Task abstract: {task.abstract}")
        
        # Initialize task record
        self.tasks[task_id] = {
            'task_id': task_id,
            'status': TaskStatus.PENDING,
            'task_model': task,
            'created_at': datetime.now(),
            'completed_at': None,
            'result': None,
            'error': None,
            'graph_file': os.path.join(self.work_dir, f"{task_id}.mermaid"),
            'terminal_output': [],  # List of command outputs
            'llm_responses': []     # List of LLM decisions
        }
        
        print(f"[TaskManager] Task stored. Total tasks now: {len(self.tasks)}")
        
        # Spawn background worker
        self.executor.submit(self._run_background_task, task_id)
        
        print(f"[TaskManager] Background worker spawned for {task_id}")
        
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[dict]:
        """Get current status of a task with all outputs"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return None
        
        # Read current graph if available
        graph_content = None
        if os.path.exists(task_info['graph_file']):
            with open(task_info['graph_file'], 'r') as f:
                graph_content = f.read()
        
        return {
            'task_id': task_id,
            'status': task_info['status'].value if isinstance(task_info['status'], TaskStatus) else task_info['status'],
            'abstract': task_info['task_model'].abstract,
            'description': task_info['task_model'].description,
            'verification': task_info['task_model'].verification,
            'result': task_info.get('result'),
            'graph': graph_content,
            'created_at': task_info['created_at'].isoformat(),
            'completed_at': task_info.get('completed_at').isoformat() if task_info.get('completed_at') else None,
            'error': task_info.get('error'),
            'terminal_output': task_info.get('terminal_output', []),
            'llm_responses': task_info.get('llm_responses', [])
        }
    
    def list_all_tasks(self) -> list:
        """List all tasks with their current status"""
        return [
            {
                'task_id': task_id,
                'status': info['status'].value,
                'abstract': info['task_model'].abstract,
                'created_at': info['created_at'].isoformat()
            }
            for task_id, info in self.tasks.items()
        ]
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return False
        
        if task_info['status'] in [TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.WORKING]:
            task_info['status'] = TaskStatus.CANCELLED
            task_info['completed_at'] = datetime.now()
            return True
        
        return False
    
    def register_node(self, task_id: str, node_id: str, node_info: dict):
        """Register a node for tracking and control"""
        with self.nodes_lock:
            self.nodes[node_id] = {
                'task_id': task_id,
                'node_id': node_id,
                'status': node_info.get('status', 'pending'),
                'abstract': node_info.get('abstract', ''),
                'parent_id': node_info.get('parent_id'),
                'terminal_output': [],
                'llm_responses': [],
                'created_at': datetime.now(),
                'completed_at': None,
                'error': None,
                'cancelled': False
            }
            print(f"[{task_id}] Registered node {node_id}: {node_info.get('abstract', '')[:50]}")
    
    def update_node_status(self, node_id: str, status: str, error: str = None):
        """Update status of a specific node"""
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['status'] = status
                if error:
                    self.nodes[node_id]['error'] = error
                if status in ['completed', 'failed', 'cancelled', 'impossible']:
                    self.nodes[node_id]['completed_at'] = datetime.now()
                print(f"[NODE {node_id}] Status updated: {status}")
    
    def cancel_node(self, node_id: str) -> bool:
        """Cancel a specific node"""
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['cancelled'] = True
                self.nodes[node_id]['status'] = 'cancelled'
                self.nodes[node_id]['completed_at'] = datetime.now()
                print(f"[NODE {node_id}] Cancelled")
                return True
        return False
    
    def is_node_cancelled(self, node_id: str) -> bool:
        """Check if a node has been cancelled"""
        with self.nodes_lock:
            if node_id in self.nodes:
                return self.nodes[node_id].get('cancelled', False)
        return False
    
    def get_node_output_callback(self, node_id: str):
        """Get output callback function for a specific node"""
        def callback(output_type, content):
            with self.nodes_lock:
                if node_id in self.nodes:
                    if output_type == 'terminal':
                        self.nodes[node_id]['terminal_output'].append(content)
                    elif output_type == 'llm':
                        self.nodes[node_id]['llm_responses'].append(content)
        return callback
    
    def get_all_nodes_for_task(self, task_id: str) -> list:
        """Get all nodes belonging to a task"""
        with self.nodes_lock:
            return [
                {
                    'node_id': node_id,
                    'status': node_info['status'],
                    'abstract': node_info['abstract'],
                    'parent_id': node_info['parent_id'],
                    'created_at': node_info['created_at'].isoformat(),
                    'completed_at': node_info['completed_at'].isoformat() if node_info['completed_at'] else None,
                    'error': node_info.get('error')
                }
                for node_id, node_info in self.nodes.items()
                if node_info['task_id'] == task_id
            ]
    
    def get_node_details(self, node_id: str) -> Optional[dict]:
        """Get detailed information about a specific node"""
        with self.nodes_lock:
            if node_id in self.nodes:
                node = self.nodes[node_id]
                return {
                    'node_id': node_id,
                    'task_id': node['task_id'],
                    'status': node['status'],
                    'abstract': node['abstract'],
                    'parent_id': node['parent_id'],
                    'terminal_output': node['terminal_output'],
                    'llm_responses': node['llm_responses'],
                    'created_at': node['created_at'].isoformat(),
                    'completed_at': node['completed_at'].isoformat() if node['completed_at'] else None,
                    'error': node.get('error')
                }
        return None
    
    def get_task_graph(self, task_id: str) -> Optional[str]:
        """Get the Mermaid graph for a task"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return None
        
        graph_file = task_info['graph_file']
        if os.path.exists(graph_file):
            with open(graph_file, 'r') as f:
                return f.read()
        
        return None
        """Get the Mermaid graph for a task"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return None
        
        graph_file = task_info['graph_file']
        if os.path.exists(graph_file):
            with open(graph_file, 'r') as f:
                return f.read()
        
        return None
    
    def _run_background_task(self, task_id: str):
        """
        Background worker that executes the task.
        Runs in thread pool to avoid blocking.
        """
        task_info = self.tasks[task_id]
        
        try:
            # Update status
            task_info['status'] = TaskStatus.PLANNING
            
            print(f"[{task_id}] Starting background task execution")
            
            # Create task relation manager (graph)
            trm = TaskRelationManager(task_info['graph_file'])
            
            # Generate root node ID
            root_node_id = trm.generate_node_id()
            
            print(f"[{task_id}] Generated root node ID: {root_node_id}")
            
            # Add root node to graph
            trm.add_root_node(
                root_node_id,
                task_info['task_model'].abstract,
                task_info['task_model'].description
            )
            
            print(f"[{task_id}] Added root node to graph")
            
            # Create MCP client
            def output_callback(output_type, content):
                """Callback to store outputs in task_info"""
                if output_type == 'terminal':
                    task_info['terminal_output'].append(content)
                    print(f"[{task_id}] Stored terminal output: {len(content)} chars, total: {len(task_info['terminal_output'])} entries")
                elif output_type == 'llm':
                    task_info['llm_responses'].append(content)
                    print(f"[{task_id}] Stored LLM response: {content[:100]}..., total: {len(task_info['llm_responses'])} entries")
            
            mcp_client = MCPAgent(
                container_name=self.container_name,
                llm_url=self.llm_url,
                llm_key=self.llm_key,
                model=self.model,
                log_callback=lambda msg: self._log_message(task_id, msg),
                output_callback=output_callback,
                install_log_callback=lambda tool: self._log_install(tool)
            )
            
            print(f"[{task_id}] Created MCP client")
            
            # Create a new TaskModel instance with node_id set
            # Pydantic v2 doesn't allow setattr for fields, so we recreate the model
            task_dict = task_info['task_model'].model_dump()
            task_dict['node_id'] = root_node_id
            task_info['task_model'] = TaskModel(**task_dict)
            
            print(f"[{task_id}] Set node_id in task model")
            print(f"[{task_id}] Task model: abstract={task_info['task_model'].abstract}, node_id={task_info['task_model'].node_id}")
            
            # Create root TaskNode
            root_node = TaskNode(
                task_model=task_info['task_model'],
                trm=trm,
                mcp_client=mcp_client,
                graph_name=task_id,
                llm_url=self.llm_url,
                llm_key=self.llm_key,
                model=self.model,
                task_manager=self  # Pass self reference
            )
            
            print(f"[{task_id}] Created root TaskNode")
            
            # Execute the task tree
            print(f"[{task_id}] Starting task execution...")
            result = root_node.execute()
            
            print(f"[{task_id}] Task execution completed successfully")
            
            # Task completed successfully
            task_info['status'] = TaskStatus.COMPLETED
            task_info['result'] = result.result
            task_info['completed_at'] = datetime.now()
            
        except TaskImpossibleException as e:
            print(f"[{task_id}] Task impossible: {e}")
            task_info['status'] = TaskStatus.IMPOSSIBLE
            task_info['error'] = str(e)
            task_info['completed_at'] = datetime.now()
            
        except Exception as e:
            print(f"[{task_id}] Task failed with exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            task_info['status'] = TaskStatus.FAILED
            task_info['error'] = f"{type(e).__name__}: {str(e)}"
            task_info['completed_at'] = datetime.now()
        
        # Check for cancellation at safe points
        if task_info['status'] == TaskStatus.CANCELLED:
            print(f"[{task_id}] Task was cancelled")
            return
    
    def _log_message(self, task_id: str, message: str):
        """Log messages for debugging (optional)"""
        # Could write to a task-specific log file
        log_file = os.path.join(self.work_dir, f"{task_id}.log")
        with open(log_file, 'a') as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")
    
    def _log_install(self, tool_name: str):
        """Log tool installations"""
        install_log = os.path.join(self.work_dir, "../logs/install.log")
        os.makedirs(os.path.dirname(install_log), exist_ok=True)
        with open(install_log, 'a') as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] Installed: {tool_name}\n")