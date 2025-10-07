#!/usr/bin/env python3
"""
task_relation_manager.py - 4-direction graph with backwards compatibility
"""
import os
import random
from typing import Dict, List, Optional, Set
from threading import Lock

from .graph_directions import DirectionalGraph, Direction


class TaskRelationManager:
    """
    Manages hierarchical task graph using 4-direction topology.
    Maintains backwards compatibility with old node structure.
    """
    
    def __init__(self, graph_file_path: str):
        self.graph_file_path = graph_file_path
        self.graph = DirectionalGraph()
        self.lock = Lock()
    
    @property
    def nodes(self):
        """
        Backwards compatibility: return nodes dict with synthetic 'children' and 'parent_id'.
        
        This allows old code like task_manager.py to continue working.
        """
        compatible_nodes = {}
        
        for node_id, metadata in self.graph.nodes.items():
            # Get relationships from 4-direction graph
            parent_id = self.graph.get_parent(node_id)
            children = self.graph.get_children(node_id)
            
            # Synthetic node structure for backwards compatibility
            compatible_nodes[node_id] = {
                **metadata,  # All metadata (abstract, description, status)
                'parent_id': parent_id,
                'children': children
            }
        
        return compatible_nodes
    
    def generate_node_id(self) -> str:
        """Generate a unique node ID"""
        while True:
            node_id = f"n{random.randint(100000, 999999)}"
            if node_id not in self.graph.relations:
                return node_id
    
    def add_root_node(self, node_id: str, abstract: str, description: str) -> str:
        """Add the root task node"""
        with self.lock:
            self.graph.add_node(
                node_id,
                abstract=abstract,
                description=description,
                status='pending'
            )
            self._draw_graph()
            return node_id
    
    def add_sub_tasks(self, parent_node_id: str, sub_nodes: List['TaskNode']) -> None:
        """
        Add sub-tasks as children using 4-direction edges.
        
        First child: parent --DOWN--> child1
        Siblings:    child1 --RIGHT--> child2 --RIGHT--> child3
        """
        with self.lock:
            if parent_node_id not in self.graph.relations:
                raise ValueError(f"Parent node {parent_node_id} not found")
            
            for i, sub_node in enumerate(sub_nodes):
                # Generate node ID
                node_id = self.generate_node_id()
                sub_node.node_id = node_id
                
                # Set node_id in pydantic model
                if hasattr(sub_node.task_pydantic_model, 'node_id'):
                    sub_node.task_pydantic_model.node_id = node_id
                if hasattr(sub_node.task_pydantic_model, 'parent_id'):
                    sub_node.task_pydantic_model.parent_id = parent_node_id
                
                # Add node to graph
                self.graph.add_node(
                    node_id,
                    abstract=sub_node.task_pydantic_model.abstract,
                    description=sub_node.task_pydantic_model.description,
                    status='pending'
                )
                
                # Add edges using 4-direction system
                if i == 0:
                    # First child: DOWN from parent
                    self.graph.add_edge(parent_node_id, Direction.DOWN, node_id)
                else:
                    # Subsequent children: RIGHT from previous sibling
                    prev_node_id = sub_nodes[i-1].node_id
                    self.graph.add_edge(prev_node_id, Direction.RIGHT, node_id)
            
            self._draw_graph()
    
    def update_node_status(self, node_id: str, status: str) -> None:
        """Update the status of a node"""
        with self.lock:
            self.graph.update_node_metadata(node_id, status=status)
            self._draw_graph()
    
    def remove_node(self, node_id: str) -> None:
        """Remove a node and its entire subtree"""
        with self.lock:
            self.graph.remove_subtree(node_id)
            self._draw_graph()
    
    def get_upper_chain_advice(self, node_id: str) -> str:
        """
        Collect advice from ancestors (UP chain) and previous siblings (LEFT chain).
        """
        with self.lock:
            if node_id not in self.graph.relations:
                return ""
            
            advice_parts = []
            
            # Get parent context (immediate parent only for simplicity)
            parent_id = self.graph.get_parent(node_id)
            if parent_id:
                metadata = self.graph.get_node_metadata(parent_id)
                advice_parts.append(f"Parent task: {metadata.get('abstract', 'N/A')}")
            
            # Get previous siblings (LEFT chain)
            prev_siblings = self.graph.get_prev_siblings(node_id)
            if prev_siblings:
                advice_parts.append("Previous steps completed:")
                for sibling_id in reversed(prev_siblings):  # Oldest to newest
                    metadata = self.graph.get_node_metadata(sibling_id)
                    advice_parts.append(
                        f"  - {metadata.get('abstract', 'N/A')} ({metadata.get('status', 'unknown')})"
                    )
            
            return "\n".join(advice_parts)
    
    def move_node_to_new_parent(
        self, 
        node_id: str, 
        new_parent_id: str,
        reason: str = ""
    ) -> None:
        """
        Re-scope operation: move node to different parent.
        
        Example: Failed exploit → jump back to recon parent for lateral movement
        """
        with self.lock:
            # Update metadata with reason
            if reason:
                metadata = self.graph.get_node_metadata(node_id)
                abstract = metadata.get('abstract', '')
                self.graph.update_node_metadata(
                    node_id,
                    abstract=f"{abstract} [Re-scoped: {reason}]"
                )
            
            # Perform move
            self.graph.move_node(node_id, new_parent_id, position='last')
            self._draw_graph()
    
    def add_sibling_variant(
        self,
        reference_node_id: str,
        variant_node_id: str,
        abstract: str,
        description: str
    ) -> None:
        """
        Add variant node as right sibling (A/B testing, payload variants).
        
        Example: sqlmap-plain --RIGHT--> sqlmap-tamper
        """
        with self.lock:
            # Add new node
            self.graph.add_node(
                variant_node_id,
                abstract=abstract,
                description=description,
                status='pending'
            )
            
            # Insert as right sibling
            self.graph.add_edge(reference_node_id, Direction.RIGHT, variant_node_id)
            self._draw_graph()
    
    def get_credential_chain(self, node_id: str) -> List[dict]:
        """
        Find all previous nodes that may have cracked credentials.
        
        Traverses LEFT (siblings) then UP (ancestors) to collect credential sources.
        """
        with self.lock:
            credential_nodes = []
            
            # Check previous siblings
            prev_siblings = self.graph.get_prev_siblings(node_id)
            for sibling_id in prev_siblings:
                metadata = self.graph.get_node_metadata(sibling_id)
                abstract = metadata.get('abstract', '').lower()
                if any(kw in abstract for kw in ['crack', 'hash', 'password', 'credential']):
                    credential_nodes.append({
                        'node_id': sibling_id,
                        'abstract': metadata.get('abstract'),
                        'direction': 'LEFT'
                    })
            
            # Check ancestors
            ancestors = self.graph.get_ancestors(node_id)
            for ancestor_id in ancestors:
                metadata = self.graph.get_node_metadata(ancestor_id)
                abstract = metadata.get('abstract', '').lower()
                if any(kw in abstract for kw in ['crack', 'hash', 'password', 'credential']):
                    credential_nodes.append({
                        'node_id': ancestor_id,
                        'abstract': metadata.get('abstract'),
                        'direction': 'UP'
                    })
            
            return credential_nodes
    
    def _draw_graph(self) -> None:
        """Generate Mermaid diagram from 4-direction graph"""
        lines = ["graph TD"]
        
        # Add all nodes
        for node_id, metadata in self.graph.nodes.items():
            abstract = metadata.get('abstract', 'N/A')[:50]
            status = metadata.get('status', 'pending')
            
            status_icon = {
                'pending': '⏳',
                'planning': '🧠',
                'working': '⚙️',
                'completed': '✅',
                'failed': '❌',
                'cancelled': '🚫',
                'impossible': '⛔'
            }.get(status, '◯')
            
            label = f"{status_icon} {abstract}".replace('"', "'")
            lines.append(f'    {node_id}["{label}"]')
        
        # Add edges (only DOWN and RIGHT for visual clarity)
        # UP and LEFT are implicit reverse edges
        for node_id in self.graph.relations:
            # DOWN edges (parent → child)
            child = self.graph.get_neighbor(node_id, Direction.DOWN)
            if child:
                lines.append(f'    {node_id} --> {child}')
            
            # RIGHT edges (sibling → sibling)
            right = self.graph.get_neighbor(node_id, Direction.RIGHT)
            if right:
                lines.append(f'    {node_id} -.-> {right}')
        
        # Enhanced styling
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
        
        # Apply styles
        for node_id, metadata in self.graph.nodes.items():
            status = metadata.get('status', 'pending')
            lines.append(f'    class {node_id} {status}')
        
        # Write to file
        mermaid_content = '\n'.join(lines)
        with open(self.graph_file_path, 'w') as f:
            f.write(mermaid_content)
    
    def get_graph_content(self) -> str:
        """Read and return current graph content"""
        try:
            with open(self.graph_file_path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return "graph TD\n    root[No graph generated yet]"