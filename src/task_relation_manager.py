#!/usr/bin/env python3
"""
task_relation_manager.py - Manages task graph structure and Mermaid visualization
"""
import os
import random
from typing import Dict, List, Optional, Set
from threading import Lock


class TaskRelationManager:
    """
    Manages the hierarchical task graph structure.
    Tracks parent-child relationships and generates Mermaid diagrams.
    """
    
    def __init__(self, graph_file_path: str):
        self.graph_file_path = graph_file_path
        self.nodes: Dict[str, dict] = {}  # node_id -> node_info
        self.edges: List[tuple] = []  # (from_id, to_id, edge_type)
        self.lock = Lock()
        
    def generate_node_id(self) -> str:
        """Generate a unique node ID"""
        while True:
            node_id = f"n{random.randint(100000, 999999)}"
            if node_id not in self.nodes:
                return node_id
    
    def add_root_node(self, node_id: str, abstract: str, description: str) -> str:
        """Add the root task node"""
        with self.lock:
            self.nodes[node_id] = {
                'abstract': abstract,
                'description': description,
                'parent_id': None,
                'children': [],
                'status': 'pending'
            }
            self._draw_graph()
            return node_id
    
    def add_sub_tasks(self, parent_node_id: str, sub_nodes: List['TaskNode']) -> None:
        """Add sub-tasks as children of a parent node"""
        with self.lock:
            if parent_node_id not in self.nodes:
                raise ValueError(f"Parent node {parent_node_id} not found")
            
            parent = self.nodes[parent_node_id]
            
            for i, sub_node in enumerate(sub_nodes):
                # Generate node ID for sub-task
                node_id = self.generate_node_id()
                sub_node.node_id = node_id
                
                # Set node_id in the pydantic model
                if hasattr(sub_node.task_pydantic_model, 'node_id'):
                    sub_node.task_pydantic_model.node_id = node_id
                if hasattr(sub_node.task_pydantic_model, 'parent_id'):
                    sub_node.task_pydantic_model.parent_id = parent_node_id
                
                # Add node
                self.nodes[node_id] = {
                    'abstract': sub_node.task_pydantic_model.abstract,
                    'description': sub_node.task_pydantic_model.description,
                    'parent_id': parent_node_id,
                    'children': [],
                    'status': 'pending'
                }
                
                # Add edge
                if i == 0:
                    # First child: DOWN from parent
                    self.edges.append((parent_node_id, node_id, 'DOWN'))
                else:
                    # Subsequent children: RIGHT from previous sibling
                    prev_node_id = sub_nodes[i-1].node_id
                    self.edges.append((prev_node_id, node_id, 'RIGHT'))
                
                parent['children'].append(node_id)
            
            self._draw_graph()
    
    def update_node_status(self, node_id: str, status: str) -> None:
        """Update the status of a node"""
        with self.lock:
            if node_id in self.nodes:
                self.nodes[node_id]['status'] = status
                self._draw_graph()
    
    def remove_node(self, node_id: str) -> None:
        """Remove a node and its sub-graph (when task is impossible)"""
        with self.lock:
            if node_id not in self.nodes:
                return
            
            # Recursively remove children
            children = self.nodes[node_id].get('children', [])
            for child_id in children:
                self.remove_node(child_id)
            
            # Remove edges involving this node
            self.edges = [(f, t, e) for f, t, e in self.edges 
                         if f != node_id and t != node_id]
            
            # Remove from parent's children list
            parent_id = self.nodes[node_id].get('parent_id')
            if parent_id and parent_id in self.nodes:
                self.nodes[parent_id]['children'].remove(node_id)
            
            # Remove the node
            del self.nodes[node_id]
            
            self._draw_graph()
    
    def get_upper_chain_advice(self, node_id: str) -> str:
        """
        Collect advice from parent and left siblings.
        Returns a summary of what has been done so far.
        """
        with self.lock:
            if node_id not in self.nodes:
                return ""
            
            advice_parts = []
            node = self.nodes[node_id]
            
            # Get parent context
            parent_id = node.get('parent_id')
            if parent_id and parent_id in self.nodes:
                parent = self.nodes[parent_id]
                advice_parts.append(f"Parent task: {parent['abstract']}")
            
            # Get left siblings (previous steps in the chain)
            if parent_id:
                siblings = self.nodes[parent_id].get('children', [])
                node_index = siblings.index(node_id) if node_id in siblings else -1
                
                if node_index > 0:
                    advice_parts.append("Previous steps completed:")
                    for i in range(node_index):
                        sibling_id = siblings[i]
                        sibling = self.nodes[sibling_id]
                        advice_parts.append(f"  - {sibling['abstract']} ({sibling['status']})")
            
            return "\n".join(advice_parts)
    
    def _draw_graph(self) -> None:
        """Generate Mermaid diagram and write to file with better dark mode colors"""
        lines = ["graph TD"]
        
        # Add nodes with better readability
        for node_id, node_info in self.nodes.items():
            abstract = node_info['abstract'][:50]  # Truncate for readability
            status = node_info['status']
            
            # Better emoji and status indicators
            status_icon = {
                'pending': '⏳',
                'planning': '🧠',
                'working': '⚙️',
                'completed': '✅',
                'failed': '❌',
                'cancelled': '🚫',
                'impossible': '⛔'
            }.get(status, '◯')
            
            # Escape special characters for Mermaid
            label = f"{status_icon} {abstract}"
            label = label.replace('"', "'")  # Replace quotes to avoid breaking Mermaid
            lines.append(f'    {node_id}["{label}"]')
        
        # Add edges with better styling
        for from_id, to_id, edge_type in self.edges:
            if edge_type == 'DOWN':
                lines.append(f'    {from_id} --> {to_id}')
            elif edge_type == 'RIGHT':
                lines.append(f'    {from_id} -.-> {to_id}')
        
        # Add enhanced styling for dark mode with better contrast
        lines.extend([
            '',
            '    %% Enhanced styling for dark mode',
            '    classDef completed fill:#2e7d32,stroke:#4caf50,stroke-width:3px,color:#ffffff',
            '    classDef working fill:#f57c00,stroke:#ff9800,stroke-width:3px,color:#ffffff',
            '    classDef planning fill:#1976d2,stroke:#2196f3,stroke-width:3px,color:#ffffff',
            '    classDef failed fill:#c62828,stroke:#f44336,stroke-width:3px,color:#ffffff',
            '    classDef cancelled fill:#616161,stroke:#9e9e9e,stroke-width:3px,color:#ffffff',
            '    classDef impossible fill:#6a1b9a,stroke:#9c27b0,stroke-width:3px,color:#ffffff',
            '    classDef pending fill:#37474f,stroke:#607d8b,stroke-width:2px,color:#e0e0e0',
        ])
        
        # Apply styles to nodes
        for node_id, node_info in self.nodes.items():
            status = node_info['status']
            if status == 'completed':
                lines.append(f'    class {node_id} completed')
            elif status == 'working':
                lines.append(f'    class {node_id} working')
            elif status == 'planning':
                lines.append(f'    class {node_id} planning')
            elif status == 'failed':
                lines.append(f'    class {node_id} failed')
            elif status == 'cancelled':
                lines.append(f'    class {node_id} cancelled')
            elif status == 'impossible':
                lines.append(f'    class {node_id} impossible')
            else:
                lines.append(f'    class {node_id} pending')
        
        # Write to file
        mermaid_content = '\n'.join(lines)
        with open(self.graph_file_path, 'w') as f:
            f.write(mermaid_content)
    
    def get_graph_content(self) -> str:
        """Read and return the current graph content"""
        try:
            with open(self.graph_file_path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return "graph TD\n    root[No graph generated yet]"