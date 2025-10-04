#!/usr/bin/env python3
"""
task_manager.py - Enhanced with better logging for multi-level branching
"""
import os
import uuid
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
    Global task manager with support for multi-level hierarchical task execution
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
        
        # Task storage
        self.tasks: Dict[str, dict] = {}
        self.nodes: Dict[str, dict] = {}
        self.nodes_lock = Lock()
        self.trms: Dict[str, TaskRelationManager] = {}
        self.trms_lock = Lock()
        
        # Thread pool for background execution
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        os.makedirs(work_dir, exist_ok=True)
    
    def create_task(self, task: TaskModel) -> str:
        """Create new task and spawn background worker"""
        task_id = str(uuid.uuid4())[:8]
        
        print(f"[TaskManager] ========== Creating task {task_id} ==========")
        print(f"[TaskManager] Abstract: {task.abstract}")
        
        self.tasks[task_id] = {
            'task_id': task_id,
            'status': TaskStatus.PENDING,
            'task_model': task,
            'created_at': datetime.now(),
            'completed_at': None,
            'result': None,
            'error': None,
            'graph_file': os.path.join(self.work_dir, f"{task_id}.mermaid"),
            'terminal_output': [],
            'llm_responses': []
        }
        
        print(f"[TaskManager] Spawning background worker for {task_id}...")
        self.executor.submit(self._run_background_task, task_id)
        
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[dict]:
        """Get current status of a task"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return None
        
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
        """List all tasks and nodes"""
        result = []
        
        for task_id, info in self.tasks.items():
            result.append({
                'type': 'root',
                'task_id': task_id,
                'status': info['status'].value if isinstance(info['status'], TaskStatus) else info['status'],
                'abstract': info['task_model'].abstract,
                'created_at': info['created_at'].isoformat()
            })
            
            with self.trms_lock:
                if task_id in self.trms:
                    trm = self.trms[task_id]
                    with trm.lock:
                        for node_id, node_data in trm.nodes.items():
                            result.append({
                                'type': 'node',
                                'task_id': task_id,
                                'node_id': node_id,
                                'status': node_data.get('status', 'unknown'),
                                'abstract': node_data.get('abstract', 'N/A'),
                                'parent_id': node_data.get('parent_id'),
                                'created_at': datetime.now().isoformat()
                            })
        
        return result
    
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
        """Register a node for tracking"""
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
    
    def register_trm(self, task_id: str, trm: TaskRelationManager):
        """Register TRM instance"""
        with self.trms_lock:
            self.trms[task_id] = trm
    
    def update_node_status(self, node_id: str, status: str, error: str = None):
        """Update node status"""
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['status'] = status
                if error:
                    self.nodes[node_id]['error'] = error
                if status in ['completed', 'failed', 'cancelled', 'impossible']:
                    self.nodes[node_id]['completed_at'] = datetime.now()
    
    def cancel_node(self, node_id: str) -> bool:
        """Cancel a specific node"""
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['cancelled'] = True
                self.nodes[node_id]['status'] = 'cancelled'
                self.nodes[node_id]['completed_at'] = datetime.now()
                return True
        return False
    
    def remove_node(self, node_id: str) -> bool:
        """Remove a node from task tree"""
        task_id = None
        with self.trms_lock:
            for tid, trm in self.trms.items():
                with trm.lock:
                    if node_id in trm.nodes:
                        task_id = tid
                        break
        
        if not task_id:
            return False
        
        self.cancel_node(node_id)
        
        with self.trms_lock:
            if task_id in self.trms:
                trm = self.trms[task_id]
                trm.remove_node(node_id)
        
        with self.nodes_lock:
            if node_id in self.nodes:
                del self.nodes[node_id]
        
        return True
    
    def is_node_cancelled(self, node_id: str) -> bool:
        """Check if node is cancelled"""
        with self.nodes_lock:
            if node_id in self.nodes:
                return self.nodes[node_id].get('cancelled', False)
        return False
    
    def get_node_output_callback(self, node_id: str):
        """Get output callback for a node"""
        def callback(output_type, content):
            with self.nodes_lock:
                if node_id in self.nodes:
                    if output_type == 'terminal':
                        self.nodes[node_id]['terminal_output'].append(content)
                    elif output_type == 'llm':
                        self.nodes[node_id]['llm_responses'].append(content)
        return callback
    
    def get_node_details(self, node_id: str) -> Optional[dict]:
        """Get detailed node information"""
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
        """Get Mermaid graph for a task"""
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
        Background worker that executes the task tree
        """
        task_info = self.tasks[task_id]
        
        try:
            print(f"[{task_id}] ========== BACKGROUND WORKER STARTED ==========")
            
            task_info['status'] = TaskStatus.PLANNING
            
            # Create TRM
            trm = TaskRelationManager(task_info['graph_file'])
            self.register_trm(task_id, trm)
            
            # Generate root node ID
            root_node_id = trm.generate_node_id()
            print(f"[{task_id}] Generated root node ID: {root_node_id}")
            
            # Add root to graph
            trm.add_root_node(
                root_node_id,
                task_info['task_model'].abstract,
                task_info['task_model'].description
            )
            
            # Create MCP client with output callback
            def output_callback(output_type, content):
                if output_type == 'terminal':
                    task_info['terminal_output'].append(content)
                elif output_type == 'llm':
                    task_info['llm_responses'].append(content)
            
            mcp_client = MCPAgent(
                container_name=self.container_name,
                llm_url=self.llm_url,
                llm_key=self.llm_key,
                model=self.model,
                log_callback=lambda msg: self._log_message(task_id, msg),
                output_callback=output_callback,
                install_log_callback=lambda tool: self._log_install(tool)
            )
            
            # Set node_id in task model
            task_dict = task_info['task_model'].model_dump()
            task_dict['node_id'] = root_node_id
            task_info['task_model'] = TaskModel(**task_dict)
            
            print(f"[{task_id}] Creating root TaskNode...")
            
            # Create root TaskNode
            root_node = TaskNode(
                task_model=task_info['task_model'],
                trm=trm,
                mcp_client=mcp_client,
                graph_name=task_id,
                llm_url=self.llm_url,
                llm_key=self.llm_key,
                model=self.model,
                task_manager=self,
                depth=0  # Root is at depth 0
            )
            
            print(f"[{task_id}] ========== CALLING root_node.execute() ==========")
            
            # Execute task tree (may branch recursively)
            result = root_node.execute()
            
            print(f"[{task_id}] ========== TASK COMPLETED ==========")
            
            task_info['status'] = TaskStatus.COMPLETED
            task_info['result'] = result.result
            task_info['completed_at'] = datetime.now()
            
        except TaskImpossibleException as e:
            print(f"[{task_id}] ========== TASK IMPOSSIBLE ==========")
            print(f"[{task_id}] Reason: {e}")
            task_info['status'] = TaskStatus.IMPOSSIBLE
            task_info['error'] = str(e)
            task_info['completed_at'] = datetime.now()
            
        except Exception as e:
            print(f"[{task_id}] ========== TASK FAILED ==========")
            print(f"[{task_id}] Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            task_info['status'] = TaskStatus.FAILED
            task_info['error'] = f"{type(e).__name__}: {str(e)}"
            task_info['completed_at'] = datetime.now()
        
        if task_info['status'] == TaskStatus.CANCELLED:
            print(f"[{task_id}] ========== TASK CANCELLED ==========")
    
    def _log_message(self, task_id: str, message: str):
        """Log debug messages"""
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