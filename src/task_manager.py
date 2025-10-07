#!/usr/bin/env python3
"""
task_manager.py - Task orchestration with 4-direction graph support and node logging
"""
import os
import uuid
from datetime import datetime
from threading import Thread, Lock
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor

from .task_models import TaskModel, TaskStatus, TaskModelOut, TaskStatusResponse
from .task_node import TaskNode, TaskImpossibleException
from .task_relation_manager import TaskRelationManager
from .mcp_agent import MCPAgent


class NodeLogger:
    """Thread-safe logger that writes to node-specific log files"""
    
    def __init__(self, log_dir: str, task_id: str, node_id: str):
        self.log_path = os.path.join(log_dir, 'nodes', task_id, f'{node_id}.log')
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self.lock = Lock()
        self._initialized = False
    
    def _ensure_initialized(self, node_metadata: dict):
        """Write header section on first call"""
        if self._initialized:
            return
        
        with self.lock:
            if self._initialized:  # Double-check
                return
            
            with open(self.log_path, 'w') as f:
                f.write("=" * 80 + "\n")
                f.write("NODE METADATA (curl-style JSON)\n")
                f.write("=" * 80 + "\n")
                import json
                f.write(json.dumps(node_metadata, indent=2) + "\n\n")
                f.write("=" * 80 + "\n")
                f.write("TERMINAL OUTPUT\n")
                f.write("=" * 80 + "\n")
            
            self._initialized = True
    
    def append_terminal(self, content: str):
        """Append terminal output with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            with open(self.log_path, 'a') as f:
                f.write(f"[{timestamp}] {content}\n")
    
    def append_llm(self, content: str):
        """Append LLM response with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            with open(self.log_path, 'a') as f:
                f.write(f"\n{'=' * 80}\n")
                f.write("LLM RESPONSES\n")
                f.write("=" * 80 + "\n")
                f.write(f"[{timestamp}]\n{content}\n\n")
    
    def get_content(self) -> str:
        """Read entire log file"""
        try:
            with open(self.log_path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return "No log file yet"


class TaskManager:
    """
    Global task manager with 4-direction graph support and comprehensive node tracking.
    
    Responsibilities:
    - Create and track tasks
    - Manage background execution threads
    - Coordinate between TaskNodes and TaskRelationManager
    - Provide node-level logging
    - Synchronize status between execution and visualization layers
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
        self.log_dir = os.path.join(os.path.dirname(work_dir), 'logs')
        
        # Task storage: task_id -> task info
        self.tasks: Dict[str, dict] = {}
        
        # Node storage: node_id -> node state (source of truth for status)
        self.nodes: Dict[str, dict] = {}
        self.nodes_lock = Lock()
        
        # TaskRelationManager instances: task_id -> TRM
        self.trms: Dict[str, TaskRelationManager] = {}
        self.trms_lock = Lock()
        
        # Node loggers: node_id -> NodeLogger
        self.loggers: Dict[str, NodeLogger] = {}
        self.loggers_lock = Lock()
        
        # Thread pool for background execution
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # Ensure directories exist
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(os.path.join(self.log_dir, 'nodes'), exist_ok=True)
    
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
            'root_node_id': None,
            'improvement_comments': None
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
        
        # Get root node output if available
        terminal_output = []
        llm_responses = []
        root_node_id = task_info.get('root_node_id')
        if root_node_id:
            with self.nodes_lock:
                if root_node_id in self.nodes:
                    terminal_output = self.nodes[root_node_id].get('terminal_output', [])
                    llm_responses = self.nodes[root_node_id].get('llm_responses', [])
        
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
            'terminal_output': terminal_output,
            'llm_responses': llm_responses,
            'root_node_id': root_node_id
        }
    
    def list_all_tasks(self) -> list:
        """
        List all tasks and nodes with correct status.
        
        Returns list of dicts containing both root tasks and their nodes.
        Nodes are retrieved from self.nodes (source of truth for status).
        """
        result = []
        
        # Add root tasks
        for task_id, info in self.tasks.items():
            result.append({
                'type': 'root',
                'task_id': task_id,
                'status': info['status'].value if isinstance(info['status'], TaskStatus) else info['status'],
                'abstract': info['task_model'].abstract,
                'created_at': info['created_at'].isoformat(),
                'root_node_id': info.get('root_node_id'),
                'is_restartable': info['status'] in [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.IMPOSSIBLE]
            })
            
            # Get nodes from self.nodes (source of truth for status)
            with self.nodes_lock:
                task_nodes = {nid: ndata for nid, ndata in self.nodes.items() 
                             if ndata.get('task_id') == task_id}
                
                for node_id, node_data in task_nodes.items():
                    result.append({
                        'type': 'node',
                        'task_id': task_id,
                        'node_id': node_id,
                        'status': node_data.get('status', 'unknown'),
                        'abstract': node_data.get('abstract', 'N/A'),
                        'parent_id': node_data.get('parent_id'),
                        'created_at': node_data.get('created_at', datetime.now()).isoformat(),
                        'terminal_outputs': len(node_data.get('terminal_output', [])),
                        'llm_responses': len(node_data.get('llm_responses', [])),
                        'is_restartable': node_data.get('status') in ['failed', 'cancelled', 'impossible']
                    })
        
        return result
    
    def get_task_nodes(self, task_id: str) -> List[dict]:
        """
        Get hierarchical list of all nodes for a task.
        
        Uses TRM for structure, self.nodes for actual status.
        """
        nodes_list = []
        
        # Build status map from self.nodes (source of truth)
        with self.nodes_lock:
            node_status_map = {nid: ndata for nid, ndata in self.nodes.items() 
                              if ndata.get('task_id') == task_id}
        
        with self.trms_lock:
            if task_id not in self.trms:
                return nodes_list
            
            trm = self.trms[task_id]
            
            # Access TRM nodes via backwards-compatible property
            # This returns synthetic nodes with parent_id and children from 4-direction graph
            trm_nodes = trm.nodes
            
            # Build tree structure recursively
            def add_node_recursive(node_id: str, depth: int = 0):
                if node_id not in trm_nodes:
                    return
                
                trm_node_data = trm_nodes[node_id]
                
                # Get actual status from self.nodes (source of truth)
                actual_status = 'unknown'
                if node_id in node_status_map:
                    actual_status = node_status_map[node_id].get('status', 'unknown')
                
                nodes_list.append({
                    'node_id': node_id,
                    'abstract': trm_node_data.get('abstract', 'N/A'),
                    'status': actual_status,
                    'depth': depth,
                    'parent_id': trm_node_data.get('parent_id'),
                    'children': trm_node_data.get('children', [])
                })
                
                # Add children recursively
                for child_id in trm_node_data.get('children', []):
                    add_node_recursive(child_id, depth + 1)
            
            # Find root node (node with no parent)
            root_node_id = None
            for nid, ndata in trm_nodes.items():
                if ndata.get('parent_id') is None:
                    root_node_id = nid
                    break
            
            if root_node_id:
                add_node_recursive(root_node_id)
        
        return nodes_list
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task and all its nodes"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return False
        
        # Only cancel if task is in a running state
        if task_info['status'] in [TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.WORKING]:
            task_info['status'] = TaskStatus.CANCELLED
            task_info['completed_at'] = datetime.now()
            
            # Cancel all nodes in this task
            with self.nodes_lock:
                for node_id, node_data in self.nodes.items():
                    if node_data.get('task_id') == task_id:
                        self.cancel_node(node_id)
            
            return True
        
        return False
    
    def mark_task_complete(self, task_id: str) -> bool:
        """Mark a task as completed manually"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return False
        
        task_info['status'] = TaskStatus.COMPLETED
        task_info['completed_at'] = datetime.now()
        task_info['result'] = "Manually marked as complete"
        
        # Mark all nodes as completed
        with self.nodes_lock:
            for node_id, node_data in self.nodes.items():
                if node_data.get('task_id') == task_id:
                    self.mark_node_complete(node_id)
        
        return True
    
    def restart_task(self, task_id: str, comments: str = "") -> Optional[str]:
        """Restart a task with optional improvement comments"""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return None
        
        # Create a new task with the same model but with comments
        original_task = task_info['task_model']
        
        # If comments provided, append them to the description
        new_description = original_task.description
        if comments:
            new_description += f"\n\nIMPROVEMENT NOTES:\n{comments}"
        
        new_task = TaskModel(
            abstract=original_task.abstract,
            description=new_description,
            verification=original_task.verification
        )
        
        # Create the new task
        new_task_id = self.create_task(new_task)
        
        # Store reference to improvement comments
        self.tasks[new_task_id]['improvement_comments'] = comments
        
        return new_task_id
    
    def register_node(self, task_id: str, node_id: str, node_info: dict):
        """
        Register a node and create its logger.
        
        This is called when a TaskNode is created and initializes:
        1. Node state in self.nodes (status, output buffers, etc.)
        2. NodeLogger for file-based logging
        3. Root node reference in task if applicable
        """
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
        
        # Create logger for this node
        with self.loggers_lock:
            logger = NodeLogger(self.log_dir, task_id, node_id)
            self.loggers[node_id] = logger
            
            # Initialize log file with metadata
            metadata = {
                'node_id': node_id,
                'task_id': task_id,
                'abstract': node_info.get('abstract', ''),
                'parent_id': node_info.get('parent_id'),
                'status': 'pending',
                'created_at': datetime.now().isoformat()
            }
            logger._ensure_initialized(metadata)
            
            print(f"[TaskManager] Created logger for {node_id} at: {logger.log_path}")
        
        # Store root_node_id in task if this is root (no parent)
        if node_info.get('parent_id') is None:
            if task_id in self.tasks:
                self.tasks[task_id]['root_node_id'] = node_id
        
        # Sync status to TRM for graph visualization
        self._sync_status_to_trm(node_id, 'pending')
    
    def register_trm(self, task_id: str, trm: TaskRelationManager):
        """Register TRM instance for a task"""
        with self.trms_lock:
            self.trms[task_id] = trm
    
    def update_node_status(self, node_id: str, status: str, error: str = None):
        """
        Update node status and sync to TRM.
        
        This is the primary method for updating node state during execution.
        """
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['status'] = status
                if error:
                    self.nodes[node_id]['error'] = error
                if status in ['completed', 'failed', 'cancelled', 'impossible']:
                    self.nodes[node_id]['completed_at'] = datetime.now()
        
        # Sync to TRM for graph visualization
        self._sync_status_to_trm(node_id, status)
    
    def _sync_status_to_trm(self, node_id: str, status: str):
        """
        Sync status from self.nodes to TRM for graph updates.
        
        This keeps the Mermaid graph in sync with actual execution state.
        """
        with self.nodes_lock:
            if node_id not in self.nodes:
                return
            task_id = self.nodes[node_id].get('task_id')
        
        if not task_id:
            return
        
        with self.trms_lock:
            if task_id in self.trms:
                trm = self.trms[task_id]
                trm.update_node_status(node_id, status)
    
    def cancel_node(self, node_id: str) -> bool:
        """Cancel a specific node (keeps in tree but stops execution)"""
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['cancelled'] = True
                self.nodes[node_id]['status'] = 'cancelled'
                self.nodes[node_id]['completed_at'] = datetime.now()
                
                # Sync to TRM
                self._sync_status_to_trm(node_id, 'cancelled')
                return True
        return False
    
    def mark_node_complete(self, node_id: str) -> bool:
        """Mark a node as completed manually"""
        with self.nodes_lock:
            if node_id in self.nodes:
                self.nodes[node_id]['status'] = 'completed'
                self.nodes[node_id]['completed_at'] = datetime.now()
                self.nodes[node_id]['cancelled'] = False
                
                # Sync to TRM
                self._sync_status_to_trm(node_id, 'completed')
                return True
        return False
    
    def force_start_node(self, node_id: str) -> bool:
        """
        Force start a pending or cancelled node.
        
        Note: This is a simplified implementation. In production, you would need
        to properly reconstruct the execution context and submit to thread pool.
        """
        with self.nodes_lock:
            if node_id not in self.nodes:
                return False
            
            node = self.nodes[node_id]
            
            # Only start if pending or cancelled
            if node['status'] not in ['pending', 'cancelled']:
                return False
            
            # Reset status
            node['status'] = 'working'
            node['cancelled'] = False
            node['error'] = None
            
            # Sync to TRM
            self._sync_status_to_trm(node_id, 'working')
        
        print(f"[TaskManager] Force starting node {node_id}")
        return True
    
    def restart_node(self, node_id: str, comments: str = "") -> Optional[str]:
        """
        Restart a node with optional improvement comments.
        
        Creates a new node as a sibling with improved description.
        """
        with self.nodes_lock:
            if node_id not in self.nodes:
                return None
            
            node = self.nodes[node_id]
            task_id = node['task_id']
            parent_id = node['parent_id']
            abstract = node['abstract']
        
        # Get TRM to create new node in same position
        with self.trms_lock:
            if task_id not in self.trms:
                return None
            
            trm = self.trms[task_id]
            
            # Generate new node ID
            new_node_id = trm.generate_node_id()
            
            # Create new description with comments
            new_abstract = abstract
            if comments:
                new_abstract += f" [Improved: {comments[:50]}]"
            
            # Register new node
            self.register_node(task_id, new_node_id, {
                'abstract': new_abstract,
                'parent_id': parent_id,
                'status': 'pending'
            })
            
            # Add to TRM using 4-direction graph
            from .graph_directions import Direction
            
            # Add as right sibling of failed node
            trm.graph.add_node(
                new_node_id,
                abstract=new_abstract,
                description=comments if comments else "Restarted node",
                status='pending'
            )
            
            # Link as right sibling
            trm.graph.add_edge(node_id, Direction.RIGHT, new_node_id)
            
            trm._draw_graph()
        
        return new_node_id
    
    def remove_node(self, node_id: str) -> bool:
        """Remove a node and its entire subtree from the task tree"""
        task_id = None
        with self.nodes_lock:
            if node_id in self.nodes:
                task_id = self.nodes[node_id].get('task_id')
        
        if not task_id:
            return False
        
        # Get all descendant nodes before removal
        descendants = []
        with self.trms_lock:
            if task_id in self.trms:
                trm = self.trms[task_id]
                # Use 4-direction graph to get descendants
                descendants_set = trm.graph.get_descendants(node_id)
                descendants = list(descendants_set)
                descendants.append(node_id)  # Include self
        
        # Cancel and remove all descendants
        for nid in descendants:
            self.cancel_node(nid)
            with self.nodes_lock:
                if nid in self.nodes:
                    del self.nodes[nid]
        
        # Remove from TRM using 4-direction graph
        with self.trms_lock:
            if task_id in self.trms:
                trm = self.trms[task_id]
                trm.remove_node(node_id)
        
        return True
    
    def is_node_cancelled(self, node_id: str) -> bool:
        """Check if node is cancelled"""
        with self.nodes_lock:
            if node_id in self.nodes:
                return self.nodes[node_id].get('cancelled', False)
        return False
    
    def get_node_output_callback(self, node_id: str):
        """
        Get output callback for a node.
        
        Returns a callback function that writes to both:
        1. Memory (self.nodes[node_id]['terminal_output'] / 'llm_responses')
        2. Log file (via NodeLogger)
        """
        def callback(output_type, content):
            # Write to memory
            with self.nodes_lock:
                if node_id in self.nodes:
                    if output_type == 'terminal':
                        self.nodes[node_id]['terminal_output'].append(content)
                    elif output_type == 'llm':
                        self.nodes[node_id]['llm_responses'].append(content)
            
            # Write to log file
            with self.loggers_lock:
                if node_id in self.loggers:
                    logger = self.loggers[node_id]
                    if output_type == 'terminal':
                        logger.append_terminal(content)
                    elif output_type == 'llm':
                        logger.append_llm(content)
        
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
    
    def get_node_log(self, node_id: str) -> Optional[str]:
        """Get log file content for a node"""
        with self.loggers_lock:
            if node_id in self.loggers:
                return self.loggers[node_id].get_content()
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
        Background worker that executes the task tree.
        
        This runs in a separate thread and manages the entire lifecycle
        of a task from planning through execution to completion.
        """
        task_info = self.tasks[task_id]
        
        try:
            print(f"[{task_id}] ========== BACKGROUND WORKER STARTED ==========")
            
            task_info['status'] = TaskStatus.PLANNING
            
            # Create TRM with 4-direction graph support
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
            
            # Create MCP client
            mcp_client = MCPAgent(
                container_name=self.container_name,
                llm_url=self.llm_url,
                llm_key=self.llm_key,
                model=self.model,
                log_callback=lambda msg: self._log_message(task_id, msg),
                output_callback=None,
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
                depth=0
            )
            
            print(f"[{task_id}] ========== CALLING root_node.execute() ==========")
            
            # Execute task tree
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
        """Log debug messages to task-level log file"""
        log_file = os.path.join(self.work_dir, f"{task_id}.log")
        with open(log_file, 'a') as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")
    
    def _log_install(self, tool_name: str):
        """Log tool installations to shared install log"""
        install_log = os.path.join(self.log_dir, "install.log")
        os.makedirs(os.path.dirname(install_log), exist_ok=True)
        with open(install_log, 'a') as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] Installed: {tool_name}\n")